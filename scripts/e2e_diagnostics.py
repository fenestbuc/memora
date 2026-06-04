#!/usr/bin/env python3
"""End-to-end diagnostic script for Memora.

Executes the full integrated flow:
1. Ingest Fact -> 2. Local File creation -> 3. Swarm Trigger -> 4. Pending Action queue
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure src is on path when run standalone
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from memora.plugin import MemoraProvider
from memora.startup_hook import process_startup
from memora import swarm_manager


def main() -> int:
    tmpdir = tempfile.mkdtemp()
    print(f"[SETUP] Using temp dir: {tmpdir}")

    memory_dir = Path(tmpdir) / "memory"
    provider = MemoraProvider()

    # Mock RAG worker responses for health check and add
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({"status": "ok", "id": "fact-e2e-1"}).encode()
    mock_resp.__enter__.return_value = mock_resp

    with patch("memora.plugin.urllib.request.urlopen", return_value=mock_resp):
        with patch.dict(
            os.environ,
            {"RAG_WORKER_URL": "https://e2e.test", "RAG_AUTH_TOKEN": "e2e_token"},
            clear=False,
        ):
            provider.initialize(
                "e2e_session_001",
                hermes_home=tmpdir,
                config={
                    "auto_swarm": True,
                    "memory_dir": str(memory_dir),
                },
            )

    # ------------------------------------------------------------------
    # Step 1: Ingest Fact
    # ------------------------------------------------------------------
    provider._queue_add(
        "business",
        "NavDhan MSME credit fee is 1.25% on disbursals.",
    )
    print("[STEP 1] Ingested fact")

    # ------------------------------------------------------------------
    # Step 2: Local File creation
    # ------------------------------------------------------------------
    local_file = memory_dir / "business.md"
    if not local_file.exists():
        print("[FAIL] Local memory file was not created")
        return 1

    local_content = local_file.read_text()
    if "NavDhan MSME credit fee is 1.25% on disbursals." not in local_content:
        print("[FAIL] Ingested fact not found in local memory file")
        return 1

    if "e2e_session_001" not in local_content:
        print("[FAIL] Session ID not found in local memory file")
        return 1

    print("[STEP 2] Local memory file created and contains fact")

    # ------------------------------------------------------------------
    # Step 3: Swarm Trigger
    # ------------------------------------------------------------------
    with patch.object(swarm_manager, "kanban_create") as mock_kanban:
        mock_kanban.return_value = {"task_id": "e2e-swarm-1"}
        with patch("memora.plugin.triage.should_trigger_swarm", return_value=True):
            with patch("memora.plugin.urllib.request.urlopen", return_value=mock_resp):
                result = provider.handle_tool_call(
                    "memora_add",
                    {"content": "E2E test strategy insight.", "category": "strategy"},
                )

        if not mock_kanban.called:
            print("[FAIL] Swarm task was not triggered")
            return 1

        _args, kwargs = mock_kanban.call_args
        if "strategy" not in kwargs.get("tags", []):
            print("[FAIL] Swarm task tags do not include category")
            return 1

    print("[STEP 3] Swarm triggered correctly")

    # ------------------------------------------------------------------
    # Step 4: Pending Action queue
    # ------------------------------------------------------------------
    conn = sqlite3.connect(provider._queue_path)
    conn.execute(
        """
        INSERT INTO pending_actions (id, action_type, payload, created_at)
        VALUES (?, ?, ?, ?)
        """,
        ("e2e-action-1", "send_ceo_digest", "{}", "2026-06-04T00:00:00+00:00"),
    )
    conn.commit()

    with patch("memora.ceo_digest.send_digest") as mock_digest:
        with patch("memora.startup_hook.subprocess.run"):
            process_startup(conn)

    cursor = conn.execute(
        "SELECT status FROM pending_actions WHERE id = ?", ("e2e-action-1",)
    )
    row = cursor.fetchone()
    conn.close()

    if row is None or row[0] != "completed":
        print(f"[FAIL] Pending action not processed. Status: {row[0] if row else 'missing'}")
        return 1

    print("[STEP 4] Pending action processed successfully")

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    provider.shutdown()
    print("\n[E2E] All diagnostics PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
