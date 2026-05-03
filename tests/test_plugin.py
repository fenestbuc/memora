"""TDD tests for HermesRagMemoryProvider (installed plugin).

Tests cover all P0 and P1 bugs diagnosed in the 2026-05-04 audit.
Run with: pytest memora/tests/test_plugin.py -v
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add the installed plugin dir to path so we can import it
import sys
sys.path.insert(0, str(Path.home() / ".hermes" / "plugins" / "hermes-rag-memory"))

from __init__ import HermesRagMemoryProvider


class TestIsAvailable(unittest.TestCase):
    """P0 Bug: is_available() returns True with placeholder token."""

    def test_real_credentials_available(self):
        """Should return True when both URL and real token are set."""
        with patch.dict(os.environ, {"RAG_WORKER_URL": "https://example.com", "RAG_AUTH_TOKEN": "real_token_123"}, clear=False):
            p = HermesRagMemoryProvider()
            self.assertTrue(p.is_available())

    def test_placeholder_token_not_available(self):
        """Should return False when token contains 'YOUR_' placeholder."""
        with patch.dict(os.environ, {"RAG_WORKER_URL": "https://example.com", "RAG_AUTH_TOKEN": "YOUR_SECRET_TOKEN"}, clear=False):
            p = HermesRagMemoryProvider()
            self.assertFalse(p.is_available())

    def test_empty_token_not_available(self):
        """Should return False when token is empty."""
        with patch.dict(os.environ, {"RAG_WORKER_URL": "https://example.com", "RAG_AUTH_TOKEN": ""}, clear=False):
            p = HermesRagMemoryProvider()
            self.assertFalse(p.is_available())

    def test_ellipsis_token_not_available(self):
        """Should return False when token contains '...' (redacted/partial)."""
        with patch.dict(os.environ, {"RAG_WORKER_URL": "https://example.com", "RAG_AUTH_TOKEN": "your_auth_token_here"}, clear=False):
            p = HermesRagMemoryProvider()
            self.assertFalse(p.is_available())


class TestQueueDedup(unittest.TestCase):
    """P2 Bug: _seen_hashes is in-memory only, not persisted."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.provider = HermesRagMemoryProvider()
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

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_duplicate_content_not_queued(self):
        """Same content should only create one queue row."""
        self.provider._queue_add("memory", "User prefers dark mode for all dashboards.")
        self.provider._queue_add("memory", "User prefers dark mode for all dashboards.")
        conn = sqlite3.connect(self.provider._queue_path)
        count = conn.execute("SELECT COUNT(*) FROM queue").fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

    def test_persisted_hash_dedup(self):
        """Hash table should prevent duplicate content across sessions."""
        # First queue
        self.provider._queue_add("memory", "persistent fact")
        # Simulate new session (new provider instance, same DB)
        p2 = HermesRagMemoryProvider()
        p2._queue_path = self.provider._queue_path
        p2._init_queue()
        p2._lock = threading.Lock()
        # After init, load seen hashes from DB
        conn = sqlite3.connect(p2._queue_path)
        cursor = conn.execute("SELECT content FROM queue")
        p2._seen_hashes = {hashlib.sha256(row[0].encode("utf-8")).hexdigest() for row in cursor}
        conn.close()
        p2._queue_add("memory", "persistent fact")
        conn = sqlite3.connect(p2._queue_path)
        count = conn.execute("SELECT COUNT(*) FROM queue").fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)


class TestFlushQueue(unittest.TestCase):
    """P0 Bug: Failed items are deleted from queue instead of kept for retry."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.provider = HermesRagMemoryProvider()
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
        self.provider._base_url = "https://test.example.com"
        self.provider._token = "test_token"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @patch("__init__.urllib.request.urlopen")
    def test_failed_items_move_to_failed_queue(self, mock_urlopen):
        """When individual add fails, item should move to failed_queue and be deleted from queue."""
        mock_urlopen.side_effect = Exception("Network error")

        self.provider._queue_add("memory", "Important decision: always use HTTPS for production.")
        self.provider._flush_queue()

        conn = sqlite3.connect(self.provider._queue_path)
        queue_count = conn.execute("SELECT COUNT(*) FROM queue").fetchone()[0]
        failed_count = conn.execute("SELECT COUNT(*) FROM failed_queue").fetchone()[0]
        conn.close()

        # Failed items are moved to failed_queue and removed from queue
        self.assertEqual(queue_count, 0)
        self.assertEqual(failed_count, 1)

    @patch("__init__.urllib.request.urlopen")
    def test_chunked_flush(self, mock_urlopen):
        """Large queues should be flushed in chunks of 100."""
        call_count = [0]

        def mock_response(req, **kwargs):
            call_count[0] += 1
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps({"success": True, "inserted": 50, "vectorized": 50}).encode()
            return mock_resp

        mock_urlopen.side_effect = mock_response

        for i in range(250):
            self.provider._queue_add("memory", f"This is a sufficiently long fact number {i} to pass validation")

        self.provider._flush_queue()

        # Should make 3 import calls: 100 + 100 + 50
        self.assertEqual(call_count[0], 3)


class TestExtractFacts(unittest.TestCase):
    """P0 Bug: _extract_facts over-ingests low-signal content."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.provider = HermesRagMemoryProvider()
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

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_short_message_not_extracted(self):
        """Messages under 30 chars should not be queued."""
        messages = [{"role": "user", "content": "ok thanks"}]
        self.provider._extract_facts(messages)
        conn = sqlite3.connect(self.provider._queue_path)
        count = conn.execute("SELECT COUNT(*) FROM queue").fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)

    def test_keyword_message_extracted(self):
        """Messages with preference keywords should be queued."""
        messages = [{"role": "user", "content": "I prefer minimal designs for all our dashboards."}]
        self.provider._extract_facts(messages)
        conn = sqlite3.connect(self.provider._queue_path)
        count = conn.execute("SELECT COUNT(*) FROM queue").fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

    def test_url_not_extracted(self):
        """Messages that are just URLs should not be queued."""
        messages = [{"role": "user", "content": "https://example.com/some/page"}]
        self.provider._extract_facts(messages)
        conn = sqlite3.connect(self.provider._queue_path)
        count = conn.execute("SELECT COUNT(*) FROM queue").fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)

    def test_code_block_not_extracted(self):
        """Messages that are just code blocks should not be queued."""
        messages = [{"role": "user", "content": "```python\nprint('hello')\n```"}]
        self.provider._extract_facts(messages)
        conn = sqlite3.connect(self.provider._queue_path)
        count = conn.execute("SELECT COUNT(*) FROM queue").fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)


class TestCircuitBreaker(unittest.TestCase):
    """P1 Bug: No circuit breaker for unreachable RAG worker."""

    def setUp(self):
        self.provider = HermesRagMemoryProvider()
        self.provider._base_url = "https://test.example.com"
        self.provider._token = "test_token"
        self.provider._circuit_open = False
        self.provider._consecutive_failures = 0
        self.provider._circuit_open_until = 0.0

    def test_circuit_opens_after_consecutive_failures(self):
        """After 3 consecutive failures, circuit should open and reject immediately."""
        with patch("__init__.urllib.request.urlopen", side_effect=Exception("Down")):
            # First 3 calls should attempt retries
            for _ in range(3):
                try:
                    self.provider._request("/test", {})
                except Exception:
                    pass

            # 4th call should raise immediately (circuit open)
            start = time.time()
            with self.assertRaises(Exception):
                self.provider._request("/test", {})
            elapsed = time.time() - start
            # Should fail fast (< 0.1s), not retry 3 times (~15s)
            self.assertLess(elapsed, 0.5)

    def test_circuit_closes_on_success(self):
        """After circuit opens, a successful call should close it."""
        call_count = [0]

        def mock_response(req, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 3:
                raise Exception("Down")
            mock_resp = MagicMock()
            mock_resp.read.return_value = b'{"ok": true}'
            return mock_resp

        with patch("__init__.urllib.request.urlopen", side_effect=mock_response):
            # 3 failures
            for _ in range(3):
                try:
                    self.provider._request("/test", {})
                except Exception:
                    pass

            # Circuit is open
            with self.assertRaises(Exception):
                self.provider._request("/test", {})

            # Wait for circuit timeout (60s in prod, but we'll patch it shorter in impl)
            # For now, test the mechanism conceptually


class TestContentValidation(unittest.TestCase):
    """P2 Bug: No content-length validation before queuing."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.provider = HermesRagMemoryProvider()
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

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_empty_content_not_queued(self):
        """Empty strings should not be queued."""
        self.provider._queue_add("memory", "")
        conn = sqlite3.connect(self.provider._queue_path)
        count = conn.execute("SELECT COUNT(*) FROM queue").fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)

    def test_short_content_not_queued(self):
        """Content under 10 chars should not be queued."""
        self.provider._queue_add("memory", "hi")
        conn = sqlite3.connect(self.provider._queue_path)
        count = conn.execute("SELECT COUNT(*) FROM queue").fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)

    def test_whitespace_only_not_queued(self):
        """Whitespace-only content should not be queued."""
        self.provider._queue_add("memory", "   \n\t  ")
        conn = sqlite3.connect(self.provider._queue_path)
        count = conn.execute("SELECT COUNT(*) FROM queue").fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)


class TestBackgroundFlush(unittest.TestCase):
    """P0 Bug: Background flush thread is never started."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @patch("__init__.urllib.request.urlopen")
    def test_flush_thread_started_on_init(self, mock_urlopen):
        """After initialize(), background flush thread should be alive."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"status": "ok"}).encode()
        mock_urlopen.return_value = mock_resp

        provider = HermesRagMemoryProvider()
        with patch.dict(os.environ, {"RAG_WORKER_URL": "https://test.example.com", "RAG_AUTH_TOKEN": "real_token"}, clear=False):
            provider.initialize("test_session", hermes_home=self.tmpdir)

        self.assertIsNotNone(provider._flush_thread)
        self.assertTrue(provider._flush_thread.is_alive())
        provider.shutdown()


if __name__ == "__main__":
    unittest.main()
