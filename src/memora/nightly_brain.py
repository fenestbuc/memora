#!/usr/bin/env python3
"""Nightly brain maintenance — runs indexer, wiki ingest, and conflict detection.

Produces a summary JSON report for observability.
"""

import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
import urllib.request
import os

# Default workspace path; override with first CLI arg
_workspace = str(Path.home() / "memora-workspace")
_scripts = Path.home() / "memora-workspace" / "scripts"
sys.path.insert(0, str(_scripts))

from memora.brain_indexer import scan_workspace, index_sessions
from memora.wiki_ingester import init_wiki, ingest_session, lint_wiki
from memora.conflict_detector import detect_conflicts
from memora.decay import apply_decay_to_queue_db

_MAX_RETRIES = 2

def evaluate_rag(url: str, token: str) -> dict:
    """Trigger evaluation on the RAG worker."""
    req = urllib.request.Request(
        f"{url}/evaluate",
        data=json.dumps({"top_k": 10}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "hermes-nightly/1.0"
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return {
                "status": "ok",
                "mrr": data.get("mrr", 0),
                "hit_rate": data.get("hit_rate", 0),
                "total": data.get("total", 0)
            }
    except Exception as e:
        return {"status": "failed", "error": str(e)}

def _get_rag_token() -> str:
    """Extract token from wrangler.toml as fallback."""
    try:
        wrangler = Path.home() / "hermes-workspace" / "hermes-rag" / "wrangler.toml"
        for line in wrangler.read_text().splitlines():
            if 'AUTH_TOKEN=' in line or 'AUTH_TOKEN =' in line:
                return line.split('"', 2)[1]
    except Exception:
        pass
    return ""

def _run_step(name: str, fn, *args, retries: int = _MAX_RETRIES):
    """Run a step with retry. Returns (result_dict, error_or_none)."""
    for attempt in range(retries + 1):
        try:
            result = fn(*args)
            return {"status": "ok", "result": result, "attempts": attempt + 1}, None
        except Exception as e:
            if attempt == retries:
                return {"status": "failed", "error": str(e), "attempts": attempt + 1}, e
    return {"status": "failed", "error": "unknown", "attempts": retries + 1}, None


def main(workspace_path: str | None = None):
    workspace = workspace_path or _workspace
    print("=== Nightly Brain Maintenance ===")
    start = datetime.now(timezone.utc)
    report = {
        "started": start.isoformat(),
        "workspace": workspace,
        "steps": {},
    }

    # 1. Index workspace
    print("[1/4] Indexing workspace...")
    r1, e1 = _run_step("scan_workspace", scan_workspace, workspace)
    report["steps"]["scan_workspace"] = r1
    if e1:
        traceback.print_exc()

    r2, e2 = _run_step("index_sessions", index_sessions, workspace)
    report["steps"]["index_sessions"] = r2
    if e2:
        traceback.print_exc()

    # 2. Ingest sessions into wiki
    print("[2/4] Ingesting sessions into wiki...")
    wiki = Path(workspace) / "wiki"
    init_wiki(str(wiki))

    sessions_dir = Path(workspace) / "sessions"
    ingested = 0
    skipped = 0
    if sessions_dir.exists():
        for sess in sorted(sessions_dir.glob("*.md")):
            r, e = _run_step("ingest", ingest_session, str(wiki), str(sess), retries=0)
            if r["status"] == "ok":
                ingested += 1
            else:
                skipped += 1
                print(f"  Skip {sess.name}: {r.get('error')}")

    report["steps"]["wiki_ingest"] = {"ingested": ingested, "skipped": skipped}

    # 3. Detect conflicts
    print("[3/4] Detecting conflicts...")
    r3, e3 = _run_step("detect_conflicts", detect_conflicts, workspace)
    report["steps"]["detect_conflicts"] = r3
    if e3:
        traceback.print_exc()

    # 4. Lint for broken wikilinks
    print("[4/4] Linting wiki...")
    broken = lint_wiki(str(wiki))
    report["steps"]["lint_wiki"] = {"broken_links": broken, "count": len(broken)}
    if broken:
        print(f"  Found {len(broken)} broken wikilink(s)")

    # 5. Evaluate RAG Backend
    print("[5/5] Evaluating RAG retrieval...")
    url = os.environ.get("RAG_WORKER_URL", "")
    token = os.environ.get("RAG_AUTH_TOKEN") or _get_rag_token()
    if token and url:
        r5 = evaluate_rag(url, token)
        report["steps"]["evaluate_rag"] = r5
        if r5["status"] == "ok":
            print(f"  MRR: {r5['mrr']:.2f}, Hit Rate: {r5['hit_rate']:.2f} (Total Evals: {r5['total']})")
    else:
        report["steps"]["evaluate_rag"] = {"status": "skipped", "reason": "No auth token or URL found"}

    # 6. Memory decay
    print("[6/6] Applying memory decay...")
    queue_path = Path.home() / ".hermes" / "memora_queue_default.db"
    if queue_path.exists():
        try:
            decay_stats = apply_decay_to_queue_db(queue_path, half_life_days=90, archive_threshold=0.15)
            report["steps"]["memory_decay"] = {"status": "ok", **decay_stats}
            print(f"  Scored: {decay_stats['scored']}, Decayed: {decay_stats['decayed']}, Archived: {decay_stats['archived']}")
        except Exception as e:
            report["steps"]["memory_decay"] = {"status": "failed", "error": str(e)}
            traceback.print_exc()
    else:
        report["steps"]["memory_decay"] = {"status": "skipped", "reason": "No queue DB found"}

    # Write report
    report["finished"] = datetime.now(timezone.utc).isoformat()
    report_dir = Path(workspace) / ".second-brain" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"nightly_{start.strftime('%Y%m%d_%H%M%S')}.json"
    report_path.write_text(json.dumps(report, indent=2))
    print(f"  Report: {report_path}")

    # Summary
    failed = [k for k, v in report["steps"].items() if v.get("status") == "failed"]
    if failed:
        print(f"  FAILED steps: {failed}")
        sys.exit(1)
    print("  All clear.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
