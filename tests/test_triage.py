"""TDD tests for triage gate (Task 3).

Tests cover:
- should_trigger_swarm calls the triage API when HERMES_TRIAGE_URL is set.
- should_trigger_swarm falls back to heuristic on API failure.
- should_trigger_swarm uses heuristic when no URL is configured.
- Actionable vs non-actionable content detection.
- Plugin gates swarm_manager.trigger behind should_trigger_swarm.

Run with: pytest tests/test_triage.py -v
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from memora import triage
from memora.plugin import MemoraProvider


class TestTriageApi(unittest.TestCase):
    """When HERMES_TRIAGE_URL is set, the API should be consulted."""

    def setUp(self):
        # Ensure env is clean
        self._original_url = os.environ.pop("HERMES_TRIAGE_URL", None)

    def tearDown(self):
        if self._original_url is not None:
            os.environ["HERMES_TRIAGE_URL"] = self._original_url
        elif "HERMES_TRIAGE_URL" in os.environ:
            del os.environ["HERMES_TRIAGE_URL"]

    @patch("memora.triage.urllib.request.urlopen")
    def test_api_true_when_actionable(self, mock_urlopen):
        """API returning actionable=True should result in True."""
        os.environ["HERMES_TRIAGE_URL"] = "https://triage.example.com/check"
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"actionable": True}).encode()
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        result = triage.should_trigger_swarm("We need to deploy the hotfix today.")
        self.assertTrue(result)

    @patch("memora.triage.urllib.request.urlopen")
    def test_api_false_when_not_actionable(self, mock_urlopen):
        """API returning actionable=False should result in False."""
        os.environ["HERMES_TRIAGE_URL"] = "https://triage.example.com/check"
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"actionable": False}).encode()
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        result = triage.should_trigger_swarm("The sky is blue.")
        self.assertFalse(result)

    @patch("memora.triage.urllib.request.urlopen")
    def test_api_error_fallback_to_heuristic(self, mock_urlopen):
        """On HTTP error, should fall back to heuristic."""
        os.environ["HERMES_TRIAGE_URL"] = "https://triage.example.com/check"
        mock_urlopen.side_effect = Exception("Connection refused")

        result = triage.should_trigger_swarm("Deploy the new service immediately.")
        self.assertTrue(result)  # heuristic says actionable

    @patch("memora.triage.urllib.request.urlopen")
    def test_api_error_fallback_to_heuristic_non_actionable(self, mock_urlopen):
        """On HTTP error, non-actionable content should return False."""
        os.environ["HERMES_TRIAGE_URL"] = "https://triage.example.com/check"
        mock_urlopen.side_effect = Exception("Connection refused")

        result = triage.should_trigger_swarm("The sky is blue.")
        self.assertFalse(result)  # heuristic says non-actionable


class TestTriageHeuristic(unittest.TestCase):
    """When no triage URL is set, use keyword heuristic."""

    def setUp(self):
        self._original_url = os.environ.pop("HERMES_TRIAGE_URL", None)

    def tearDown(self):
        if self._original_url is not None:
            os.environ["HERMES_TRIAGE_URL"] = self._original_url
        elif "HERMES_TRIAGE_URL" in os.environ:
            del os.environ["HERMES_TRIAGE_URL"]

    def test_heuristic_detects_actionable(self):
        """Keywords like 'deploy' should trigger True."""
        self.assertTrue(triage.should_trigger_swarm("We must deploy the fix today."))

    def test_heuristic_detects_todo(self):
        """Keywords like 'todo' should trigger True."""
        self.assertTrue(triage.should_trigger_swarm("todo: update documentation"))

    def test_heuristic_detects_decision(self):
        """Keywords like 'decided' should trigger True."""
        self.assertTrue(triage.should_trigger_swarm("We decided to migrate to PostgreSQL."))

    def test_heuristic_non_actionable(self):
        """Plain facts without action keywords should return False."""
        self.assertFalse(triage.should_trigger_swarm("The office is located in Bangalore."))

    def test_heuristic_non_actionable_vague(self):
        """Vague statements should return False."""
        self.assertFalse(triage.should_trigger_swarm("It seems like a nice day."))


class TestPluginSwarmGate(unittest.TestCase):
    """Plugin should gate swarm_manager.trigger behind should_trigger_swarm."""

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
    def test_swarm_triggered_when_triage_returns_true(
        self, mock_kanban, mock_triage, mock_urlopen
    ):
        """When triage returns True, swarm_manager.trigger should fire."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"status": "ok", "id": "fact-1"}).encode()
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp
        mock_triage.return_value = True
        mock_kanban.return_value = {"task_id": "kanban-1"}

        self.provider._auto_swarm = True
        result = self.provider.handle_tool_call(
            "memora_add",
            {"content": "Deploy hotfix to production.", "category": "ops"},
        )

        self.assertIn("ok", json.loads(result)["status"])
        mock_triage.assert_called_once_with("Deploy hotfix to production.")
        mock_kanban.assert_called_once()

    @patch("memora.plugin.urllib.request.urlopen")
    @patch("memora.plugin.triage.should_trigger_swarm")
    @patch("memora.swarm_manager.kanban_create")
    def test_swarm_blocked_when_triage_returns_false(
        self, mock_kanban, mock_triage, mock_urlopen
    ):
        """When triage returns False, swarm_manager.trigger should NOT fire."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"status": "ok", "id": "fact-2"}).encode()
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp
        mock_triage.return_value = False

        self.provider._auto_swarm = True
        result = self.provider.handle_tool_call(
            "memora_add",
            {"content": "The sky is blue today.", "category": "memory"},
        )

        self.assertIn("ok", json.loads(result)["status"])
        mock_triage.assert_called_once_with("The sky is blue today.")
        mock_kanban.assert_not_called()

    @patch("memora.plugin.urllib.request.urlopen")
    @patch("memora.plugin.triage.should_trigger_swarm")
    @patch("memora.swarm_manager.kanban_create")
    def test_swarm_blocked_when_auto_swarm_false(
        self, mock_kanban, mock_triage, mock_urlopen
    ):
        """When auto_swarm is False, triage should not even be called."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"status": "ok", "id": "fact-3"}).encode()
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        self.provider._auto_swarm = False
        result = self.provider.handle_tool_call(
            "memora_add",
            {"content": "Deploy hotfix to production.", "category": "ops"},
        )

        self.assertIn("ok", json.loads(result)["status"])
        mock_triage.assert_not_called()
        mock_kanban.assert_not_called()

    @patch("memora.plugin.urllib.request.urlopen")
    @patch("memora.plugin.triage.should_trigger_swarm")
    @patch("memora.swarm_manager.kanban_create")
    def test_swarm_blocked_for_chunked_add_when_triage_false(
        self, mock_kanban, mock_triage, mock_urlopen
    ):
        """When chunked add and triage returns False, swarm should NOT fire."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"status": "ok", "id": "fact-4"}).encode()
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp
        mock_triage.return_value = False

        self.provider._auto_swarm = True
        long_content = "A" * 5000
        result = self.provider.handle_tool_call(
            "memora_add",
            {"content": long_content, "category": "memory"},
        )

        self.assertIn("success", json.loads(result)["status"])
        mock_triage.assert_called_once_with(long_content)
        mock_kanban.assert_not_called()

    @patch("memora.swarm_manager.kanban_create")
    @patch("memora.plugin.triage.should_trigger_swarm")
    def test_offline_queued_fact_triggers_swarm_when_triage_true(
        self, mock_triage, mock_kanban
    ):
        """When network is down, fact queued, and triage True, swarm should trigger."""
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

    @patch("memora.swarm_manager.kanban_create")
    @patch("memora.plugin.triage.should_trigger_swarm")
    def test_offline_queued_fact_blocked_when_triage_false(
        self, mock_triage, mock_kanban
    ):
        """When network is down, fact queued, and triage False, swarm should NOT trigger."""
        mock_triage.return_value = False
        self.provider._base_url = "https://offline.example.com"

        with patch("memora.plugin.urllib.request.urlopen", side_effect=Exception("Network down")):
            self.provider._auto_swarm = True
            result = self.provider.handle_tool_call(
                "memora_add",
                {"content": "The weather is nice.", "category": "memory"},
            )

        self.assertEqual(json.loads(result)["status"], "queued_offline")
        mock_triage.assert_called_once_with("The weather is nice.")
        mock_kanban.assert_not_called()


if __name__ == "__main__":
    unittest.main()
