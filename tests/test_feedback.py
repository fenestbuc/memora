"""TDD tests for feedback_interceptor and kanban_reassign interception.

Tests cover:
- capture_routing_correction normalizes expected keys.
- capture_routing_correction gracefully handles missing fields.
- MemoraProvider.handle_tool_call intercepts kanban_reassign and persists feedback.

Run with: pytest tests/test_feedback.py -v
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from memora.feedback_interceptor import capture_routing_correction
from memora.plugin import MemoraProvider


class TestCaptureRoutingCorrection(unittest.TestCase):
    """Unit tests for capture_routing_correction."""

    def test_full_args(self):
        """Should return a fully populated correction dict."""
        args = {
            "task_id": "task-123",
            "original_agent": "agent-alpha",
            "target_agent": "agent-beta",
            "reason": "Better expertise match",
        }
        result = capture_routing_correction(args)
        self.assertEqual(result["task_id"], "task-123")
        self.assertEqual(result["original_agent"], "agent-alpha")
        self.assertEqual(result["target_agent"], "agent-beta")
        self.assertEqual(result["reason"], "Better expertise match")
        self.assertIn("captured_at", result)

    def test_alias_keys(self):
        """Should map from_agent / to_agent alias keys."""
        args = {
            "task_id": "task-456",
            "from_agent": "agent-x",
            "to_agent": "agent-y",
        }
        result = capture_routing_correction(args)
        self.assertEqual(result["original_agent"], "agent-x")
        self.assertEqual(result["target_agent"], "agent-y")

    def test_missing_fields(self):
        """Should handle missing keys gracefully."""
        result = capture_routing_correction({})
        self.assertEqual(result["task_id"], "")
        self.assertEqual(result["original_agent"], "")
        self.assertEqual(result["target_agent"], "")
        self.assertEqual(result["reason"], "")
        self.assertIn("captured_at", result)

    def test_empty_reason(self):
        """Should accept empty reason."""
        result = capture_routing_correction(
            {"task_id": "t1", "original_agent": "a1", "target_agent": "a2"}
        )
        self.assertEqual(result["reason"], "")

    def test_appends_to_jsonl(self):
        """Should append correction as a single JSON line when jsonl_path is provided."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            tmp_path = f.name
        try:
            args = {
                "task_id": "task-jsonl",
                "original_agent": "agent-a",
                "target_agent": "agent-b",
                "reason": "jsonl test",
            }
            result = capture_routing_correction(args, jsonl_path=tmp_path)
            self.assertEqual(result["task_id"], "task-jsonl")

            with open(tmp_path, "r", encoding="utf-8") as f:
                lines = [line.strip() for line in f if line.strip()]
            self.assertEqual(len(lines), 1)
            parsed = json.loads(lines[0])
            self.assertEqual(parsed["task_id"], "task-jsonl")
            self.assertEqual(parsed["original_agent"], "agent-a")
            self.assertEqual(parsed["target_agent"], "agent-b")
            self.assertEqual(parsed["reason"], "jsonl test")
            self.assertIn("captured_at", parsed)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_appends_multiple_to_jsonl(self):
        """Multiple calls should append distinct lines without overwriting."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            tmp_path = f.name
        try:
            capture_routing_correction(
                {"task_id": "t1", "original_agent": "a1", "target_agent": "a2"},
                jsonl_path=tmp_path,
            )
            capture_routing_correction(
                {"task_id": "t2", "original_agent": "a2", "target_agent": "a3"},
                jsonl_path=tmp_path,
            )
            with open(tmp_path, "r", encoding="utf-8") as f:
                lines = [line.strip() for line in f if line.strip()]
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[0])["task_id"], "t1")
            self.assertEqual(json.loads(lines[1])["task_id"], "t2")
        finally:
            Path(tmp_path).unlink(missing_ok=True)


class TestKanbanReassignInterception(unittest.TestCase):
    """MemoraProvider.handle_tool_call should intercept kanban_reassign."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.provider = MemoraProvider()
        self.provider._hermes_home = self.tmpdir
        self.provider._agent_identity = "test"
        self.provider._session_id = "test_session"
        self.provider._queue_path = Path(self.tmpdir) / "test_queue.db"
        self.provider._init_queue()
        self.provider._lock = threading.Lock()
        self.provider._seen_hashes = set()
        self.provider._circuit_open = False
        self.provider._consecutive_failures = 0
        self.provider._circuit_open_until = 0.0
        self.provider._metrics = {
            "facts_queued": 0,
            "facts_flushed": 0,
            "facts_failed": 0,
            "search_calls": 0,
            "prefetch_calls": 0,
            "circuit_opens": 0,
        }

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_handle_kanban_reassign_queues_feedback(self):
        """kanban_reassign should queue a feedback fact and return captured correction."""
        args = {
            "task_id": "task-789",
            "original_agent": "analyst",
            "target_agent": "reviewer",
            "reason": "Wrong role assigned initially",
        }
        result_raw = self.provider.handle_tool_call("kanban_reassign", args)
        result = json.loads(result_raw)

        self.assertEqual(result["status"], "feedback_captured")
        self.assertEqual(result["correction"]["task_id"], "task-789")
        self.assertEqual(result["correction"]["original_agent"], "analyst")
        self.assertEqual(result["correction"]["target_agent"], "reviewer")

        # Verify SQLite queue contains the feedback
        conn = sqlite3.connect(self.provider._queue_path)
        row = conn.execute(
            "SELECT category, content FROM queue WHERE category = ?", ("feedback",)
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "feedback")
        queued = json.loads(row[1])
        self.assertEqual(queued["task_id"], "task-789")
        self.assertEqual(queued["reason"], "Wrong role assigned initially")

    def test_handle_kanban_reassign_writes_jsonl(self):
        """kanban_reassign should also append a JSONL line in hermes home."""
        args = {
            "task_id": "task-jsonl-001",
            "original_agent": "analyst",
            "target_agent": "reviewer",
            "reason": "JSONL persistence test",
        }
        self.provider.handle_tool_call("kanban_reassign", args)

        jsonl_path = Path(self.tmpdir) / "routing_corrections.jsonl"
        self.assertTrue(jsonl_path.exists())
        lines = [line.strip() for line in jsonl_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(lines), 1)
        parsed = json.loads(lines[0])
        self.assertEqual(parsed["task_id"], "task-jsonl-001")
        self.assertEqual(parsed["reason"], "JSONL persistence test")

    def test_handle_kanban_reassign_increments_metrics(self):
        """Intercepting kanban_reassign should increment facts_queued metric."""
        self.assertEqual(self.provider._metrics["facts_queued"], 0)
        self.provider.handle_tool_call(
            "kanban_reassign",
            {"task_id": "t1", "original_agent": "a1", "target_agent": "a2"},
        )
        self.assertEqual(self.provider._metrics["facts_queued"], 1)

    def test_kanban_reassign_does_not_hit_rag_worker(self):
        """kanban_reassign should not make any HTTP calls."""
        with patch("memora.plugin.urllib.request.urlopen") as mock_urlopen:
            self.provider.handle_tool_call(
                "kanban_reassign",
                {"task_id": "t2", "target_agent": "ceo"},
            )
            mock_urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
