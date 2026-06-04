"""TDD tests for swarm_manager and plugin integration (Phase 5, Task 5).

Tests cover:
- swarm_manager.trigger delegates to kanban_create with correct args.
- swarm_manager.trigger returns None when kanban_create is unavailable.
- MemoraProvider.handle_tool_call spawns a swarm task when auto_swarm=True.
- MemoraProvider.handle_tool_call does NOT spawn when auto_swarm=False.

Run with: pytest tests/test_swarm.py -v
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from memora.plugin import MemoraProvider
from memora import swarm_manager


class TestSwarmManagerTrigger(unittest.TestCase):
    """swarm_manager.trigger should call kanban_create with canonical args."""

    def test_trigger_calls_kanban_create(self):
        """trigger should invoke kanban_create with title, body, and tags."""
        with patch.object(swarm_manager, "kanban_create") as mock_create:
            mock_create.return_value = {"task_id": "abc-123"}

            result = swarm_manager.trigger(
                source="mcp_notion",
                content="New GTM strategy doc uploaded.",
                category="strategy",
                scope="company",
                agent_role="analyst",
            )

            self.assertEqual(result, {"task_id": "abc-123"})
            mock_create.assert_called_once()
            args, kwargs = mock_create.call_args
            self.assertIn("analyst", kwargs["title"])
            self.assertIn("company", kwargs["title"])
            self.assertIn("mcp_notion", kwargs["body"])
            self.assertIn("strategy", kwargs["tags"])
            self.assertIn("company", kwargs["tags"])

    def test_trigger_returns_none_when_kanban_create_unavailable(self):
        """When kanban_create is None, trigger should return None gracefully."""
        with patch.object(swarm_manager, "kanban_create", None):
            result = swarm_manager.trigger(
                source="rag",
                content="Test fact.",
                category="memory",
            )
            self.assertIsNone(result)

    def test_trigger_caps_content_at_2000_chars(self):
        """Very long facts should be truncated in the kanban body."""
        with patch.object(swarm_manager, "kanban_create") as mock_create:
            mock_create.return_value = {"task_id": "xyz"}
            long_content = "A" * 5000

            swarm_manager.trigger(source="rag", content=long_content)

            _args, kwargs = mock_create.call_args
            self.assertEqual(len(kwargs["body"].split("**Fact:**\n")[-1]), 2000)

    @patch.dict(os.environ, {"MEMORA_KANBAN_BACKEND": "linear", "LINEAR_API_KEY": "test-key"})
    @patch("memora.swarm_manager.urllib.request.urlopen")
    def test_trigger_linear_backend(self, mock_urlopen):
        """When MEMORA_KANBAN_BACKEND=linear, trigger should call Linear API."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "data": {"issueCreate": {"success": True, "issue": {"id": "i-1", "identifier": "TEAM-42", "url": "https://linear.app/issue/TEAM-42", "title": "test"}}}
        }).encode()
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        result = swarm_manager.trigger(
            source="rag",
            content="Linear test fact.",
            category="strategy",
        )

        self.assertEqual(result["identifier"], "TEAM-42")
        self.assertIn("linear.app", result["url"])

    @patch.dict(os.environ, {"MEMORA_KANBAN_BACKEND": "none"})
    def test_trigger_none_backend(self):
        """When backend is 'none', trigger should return None and log a warning."""
        result = swarm_manager.trigger(
            source="rag",
            content="No backend configured.",
            category="memory",
        )
        self.assertIsNone(result)


class TestPluginSwarmIntegration(unittest.TestCase):
    """MemoraProvider should optionally trigger swarm tasks on fact ingest."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.provider = MemoraProvider()
        self.provider._hermes_home = self.tmpdir
        self.provider._agent_identity = "test"
        self.provider._session_id = "test_session"
        self.provider._queue_path = Path(self.tmpdir) / "test_queue.db"
        self.provider._init_queue()
        self.provider._lock = __import__("threading").Lock()
        self.provider._seen_hashes = set()
        self.provider._circuit_open = False
        self.provider._consecutive_failures = 0
        self.provider._circuit_open_until = 0.0
        self.provider._base_url = "https://test.example.com"
        self.provider._token = "test_token"
        self.provider._l1_cache = MagicMock()
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

    @patch("memora.plugin.urllib.request.urlopen")
    @patch("memora.plugin.triage.should_trigger_swarm")
    @patch("memora.swarm_manager.kanban_create")
    def test_memora_add_triggers_swarm_when_auto_swarm_true(self, mock_kanban, mock_triage, mock_urlopen):
        """handle_tool_call memora_add should call swarm_manager.trigger when auto_swarm=True."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"status": "ok", "id": "fact-1"}).encode()
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp
        mock_triage.return_value = True
        mock_kanban.return_value = {"task_id": "kanban-1"}

        self.provider._auto_swarm = True
        result = self.provider.handle_tool_call(
            "memora_add",
            {"content": "Pivot pricing to 1.25% fee.", "category": "business"},
        )

        self.assertIn("ok", json.loads(result)["status"])
        mock_triage.assert_called_once_with("Pivot pricing to 1.25% fee.")
        mock_kanban.assert_called_once()
        _args, kwargs = mock_kanban.call_args
        self.assertIn("business", kwargs["tags"])

    @patch("memora.plugin.urllib.request.urlopen")
    @patch("memora.swarm_manager.kanban_create")
    def test_memora_add_does_not_trigger_when_auto_swarm_false(self, mock_kanban, mock_urlopen):
        """handle_tool_call memora_add should NOT call swarm_manager.trigger when auto_swarm=False."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"status": "ok", "id": "fact-2"}).encode()
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        self.provider._auto_swarm = False
        self.provider.handle_tool_call(
            "memora_add",
            {"content": "Stay the course on pricing.", "category": "business"},
        )

        mock_kanban.assert_not_called()

    @patch("memora.plugin.triage.should_trigger_swarm")
    @patch("memora.swarm_manager.kanban_create")
    def test_offline_queued_fact_triggers_swarm_when_auto_swarm_true(self, mock_kanban, mock_triage):
        """When network is down and fact is queued, swarm should still trigger."""
        mock_triage.return_value = True
        mock_kanban.return_value = {"task_id": "kanban-2"}
        self.provider._base_url = "https://offline.example.com"

        with patch("memora.plugin.urllib.request.urlopen", side_effect=Exception("Network down")):
            self.provider._auto_swarm = True
            result = self.provider.handle_tool_call(
                "memora_add",
                {"content": "New offline strategy insight.", "category": "strategy"},
            )

        self.assertEqual(json.loads(result)["status"], "queued_offline")
        mock_triage.assert_called_once_with("New offline strategy insight.")
        mock_kanban.assert_called_once()


if __name__ == "__main__":
    unittest.main()
