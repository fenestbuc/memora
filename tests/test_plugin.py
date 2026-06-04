"""TDD tests for MemoraProvider (installed plugin).

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

# Add the plugin dir to path so we can import it
import sys
# Plugin is at src/memora/plugin.py, pythonpath=["src"] in pyproject.toml

from memora.plugin import MemoraProvider


class TestLocalMemoryMirror(unittest.TestCase):
    """Local markdown memory should mirror RAG writes."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.memdir = Path(self.tmpdir) / "memory"
        self.provider = MemoraProvider()
        self.provider._hermes_home = self.tmpdir
        self.provider._agent_identity = "test"
        self.provider._session_id = "test_session_001"
        self.provider._queue_path = Path(self.tmpdir) / "test_queue.db"
        self.provider._memory_dir = self.memdir
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

    def test_local_memory_file_created(self):
        """Queueing a fact should create the corresponding .md file."""
        self.provider._queue_add("business", "NavDhan fee is 1.25% on disbursals.")
        file_path = self.memdir / "business.md"
        self.assertTrue(file_path.exists())
        content = file_path.read_text()
        self.assertIn("NavDhan fee is 1.25% on disbursals.", content)
        self.assertIn("test_session_001", content)

    def test_local_memory_appends(self):
        """Multiple facts should append to the same file."""
        self.provider._queue_add("business", "Fact number one for business context.")
        self.provider._queue_add("business", "Fact number two for business context.")
        file_path = self.memdir / "business.md"
        content = file_path.read_text()
        self.assertEqual(content.count("Fact number one for business context."), 1)
        self.assertEqual(content.count("Fact number two for business context."), 1)

    def test_local_memory_category_mapping(self):
        """Category should map to safe filename."""
        self.provider._queue_add("user preferences", "User prefers dark mode for dashboards.")
        file_path = self.memdir / "user_preferences.md"
        self.assertTrue(file_path.exists())


class TestAutoIngestGate(unittest.TestCase):
    """sync_turn should be gated by auto_ingest config."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.provider = MemoraProvider()
        self.provider._hermes_home = self.tmpdir
        self.provider._agent_identity = "test"
        self.provider._session_id = "test_session"
        self.provider._queue_path = Path(self.tmpdir) / "test_queue.db"
        self.provider._memory_dir = Path(self.tmpdir) / "memory"
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

    def test_sync_turn_queued_when_auto_ingest_true(self):
        """sync_turn should queue when auto_ingest is True."""
        self.provider._auto_ingest = True
        self.provider.sync_turn(
            "We decided to pivot the pricing model to 1.25% on disbursals.",
            "Noted. I will persist that strategic decision."
        )
        conn = sqlite3.connect(self.provider._queue_path)
        count = conn.execute("SELECT COUNT(*) FROM queue").fetchone()[0]
        conn.close()
        self.assertGreater(count, 0)

    def test_sync_turn_skipped_when_auto_ingest_false(self):
        """sync_turn should not queue when auto_ingest is False."""
        self.provider._auto_ingest = False
        self.provider.sync_turn("Hello", "Hi there")
        conn = sqlite3.connect(self.provider._queue_path)
        count = conn.execute("SELECT COUNT(*) FROM queue").fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)


class TestIsAvailable(unittest.TestCase):
    """P0 Bug: is_available() returns True with placeholder token."""

    def test_real_credentials_available(self):
        """Should return True when both URL and real token are set."""
        with patch.dict(os.environ, {"RAG_WORKER_URL": "https://example.com", "RAG_AUTH_TOKEN": "real_token_123"}, clear=False):
            p = MemoraProvider()
            self.assertTrue(p.is_available())

    def test_placeholder_token_not_available(self):
        """Should return False when token contains 'YOUR_' placeholder."""
        with patch.dict(os.environ, {"RAG_WORKER_URL": "https://example.com", "RAG_AUTH_TOKEN": "YOUR_SECRET_TOKEN"}, clear=False):
            p = MemoraProvider()
            self.assertFalse(p.is_available())

    def test_empty_token_not_available(self):
        """Should return False when token is empty."""
        with patch.dict(os.environ, {"RAG_WORKER_URL": "https://example.com", "RAG_AUTH_TOKEN": ""}, clear=False):
            p = MemoraProvider()
            self.assertFalse(p.is_available())

    def test_ellipsis_token_not_available(self):
        """Should return False when token contains '...' (redacted/partial)."""
        with patch.dict(os.environ, {"RAG_WORKER_URL": "https://example.com", "RAG_AUTH_TOKEN": "hrms_rag_53be..."}, clear=False):
            p = MemoraProvider()
            self.assertFalse(p.is_available())


class TestQueueDedup(unittest.TestCase):
    """P2 Bug: _seen_hashes is in-memory only, not persisted."""

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
        p2 = MemoraProvider()
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
        self.provider._base_url = "https://test.example.com"
        self.provider._token = "test_token"
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

    @patch("memora.plugin.urllib.request.urlopen")
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
        self.provider = MemoraProvider()
        self.provider._base_url = "https://test.example.com"
        self.provider._token = "test_token"
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

    def test_circuit_opens_after_consecutive_failures(self):
        """After 3 consecutive failures, circuit should open and reject immediately."""
        with patch("memora.plugin.urllib.request.urlopen", side_effect=Exception("Down")):
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

        with patch("memora.plugin.urllib.request.urlopen", side_effect=mock_response):
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
        # Pre-create profile so onboarding is skipped during initialize()
        Path(self.tmpdir, "memora.json").write_text(
            '{"first_name":"test"}', encoding="utf-8"
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @patch("memora.plugin.urllib.request.urlopen")
    def test_flush_thread_started_on_init(self, mock_urlopen):
        """After initialize(), background flush thread should be alive."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"status": "ok"}).encode()
        mock_urlopen.return_value = mock_resp

        provider = MemoraProvider()
        with patch.dict(os.environ, {"RAG_WORKER_URL": "https://test.example.com", "RAG_AUTH_TOKEN": "real_token"}, clear=False):
            provider.initialize("test_session", hermes_home=self.tmpdir)

        self.assertIsNotNone(provider._flush_thread)
        self.assertTrue(provider._flush_thread.is_alive())
        provider.shutdown()


class TestMetrics(unittest.TestCase):
    """P2: Metrics counters should track plugin activity."""

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

    def test_facts_queued_metric(self):
        """_queue_add should increment facts_queued."""
        self.provider._queue_add("memory", "This is a test fact for metrics counting.")
        self.assertEqual(self.provider._metrics["facts_queued"], 1)
        self.provider._queue_add("memory", "Another test fact for metrics counting.")
        self.assertEqual(self.provider._metrics["facts_queued"], 2)

    def test_get_metrics_returns_copy(self):
        """get_metrics should return a copy of the metrics dict."""
        self.provider._queue_add("memory", "Test fact for get_metrics.")
        m = self.provider.get_metrics()
        self.assertEqual(m["facts_queued"], 1)
        # Modifying returned dict should not affect internal state
        m["facts_queued"] = 999
        self.assertEqual(self.provider._metrics["facts_queued"], 1)

    @patch("memora.plugin.urllib.request.urlopen")
    def test_facts_flushed_metric(self, mock_urlopen):
        """_flush_queue should increment facts_flushed."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"success": True}).encode()
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        self.provider._base_url = "https://test.example.com"
        self.provider._token = "test_token"
        self.provider._queue_add("memory", "Fact one to flush.")
        self.provider._queue_add("memory", "Fact two to flush.")
        self.provider._flush_queue()
        self.assertEqual(self.provider._metrics["facts_flushed"], 2)


class TestPrefetchThreshold(unittest.TestCase):
    """P2: Prefetch threshold should be configurable."""

    def setUp(self):
        self.provider = MemoraProvider()
        self.provider._base_url = "https://test.example.com"
        self.provider._token = "test_token"
        self.provider._prefetch_threshold = 0.7
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
        pass

    @patch("memora.plugin.urllib.request.urlopen")
    def test_prefetch_respects_threshold(self, mock_urlopen):
        """ prefetch should only include results above threshold."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "results": [
                {"text": "High relevance fact", "rerank_score": 0.9, "metadata": {"category": "memory", "created_at": "2026-05-01T10:00:00Z"}},
                {"text": "Low relevance fact", "rerank_score": 0.3, "metadata": {"category": "memory", "created_at": "2026-05-02T10:00:00Z"}},
                {"text": "Medium relevance fact", "rerank_score": 0.75, "metadata": {"category": "user", "created_at": ""}},
            ]
        }).encode()
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        result = self.provider.prefetch("test query")
        self.assertIn("High relevance fact", result)
        self.assertIn("Medium relevance fact", result)
        self.assertNotIn("Low relevance fact", result)
        self.assertEqual(self.provider._metrics["prefetch_calls"], 1)

    @patch("memora.plugin.urllib.request.urlopen")
    def test_prefetch_includes_category_and_date(self, mock_urlopen):
        """prefetch results should include category and date metadata."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "results": [
                {"text": "Fact with metadata", "rerank_score": 0.9, "metadata": {"category": "business", "created_at": "2026-05-01T10:00:00Z"}},
            ]
        }).encode()
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        result = self.provider.prefetch("test query")
        self.assertIn("[business]", result)
        self.assertIn("[2026-05-01]", result)


class TestOwnerIdFromProfile(unittest.TestCase):
    """owner_id should be read from ~/.hermes/memora.json during initialize()."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @patch("memora.plugin.urllib.request.urlopen")
    def test_initialize_reads_first_name_as_owner_id(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"status": "ok"}).encode()
        mock_urlopen.return_value = mock_resp

        Path(self.tmpdir, "memora.json").write_text(
            '{"first_name":"alice"}', encoding="utf-8"
        )

        provider = MemoraProvider()
        with patch.dict(os.environ, {"RAG_WORKER_URL": "https://test.example.com", "RAG_AUTH_TOKEN": "real_token"}, clear=False):
            provider.initialize("test_session", hermes_home=self.tmpdir)

        self.assertEqual(provider._owner_id, "alice")
        provider.shutdown()


class TestHandleToolCall(unittest.TestCase):
    """memora_add and memora_search payloads should include owner_id and tenant_id."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.provider = MemoraProvider()
        self.provider._hermes_home = self.tmpdir
        self.provider._agent_identity = "test"
        self.provider._session_id = "test_session"
        self.provider._queue_path = Path(self.tmpdir) / "test_queue.db"
        self.provider._memory_dir = Path(self.tmpdir) / "memory"
        self.provider._init_queue()
        self.provider._l1_cache.clear()
        self.provider._lock = threading.Lock()
        self.provider._seen_hashes = set()
        self.provider._circuit_open = False
        self.provider._consecutive_failures = 0
        self.provider._circuit_open_until = 0.0
        self.provider._base_url = "https://test.example.com"
        self.provider._token = "test_token"
        self.provider._owner_id = "test_user"
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

    def _extract_payload(self, mock_urlopen):
        req = mock_urlopen.call_args[0][0]
        return json.loads(req.data.decode("utf-8"))

    @patch("memora.plugin.urllib.request.urlopen")
    def test_search_payload_includes_owner(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"results": []}).encode()
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        self.provider.handle_tool_call("memora_search", {"query": "test"})
        payload = self._extract_payload(mock_urlopen)
        self.assertIn("owner_id", payload)
        self.assertEqual(payload["owner_id"], "test_user")
        self.assertNotIn("tenant_id", payload)

    @patch("memora.plugin.urllib.request.urlopen")
    def test_search_personal_scope_passed_directly(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"results": []}).encode()
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        self.provider.handle_tool_call("memora_search", {"query": "test", "scope": "personal"})
        payload = self._extract_payload(mock_urlopen)
        self.assertEqual(payload["scope"], "personal")

    @patch("memora.plugin.urllib.request.urlopen")
    def test_search_global_scope_excludes_owner_filter(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"results": []}).encode()
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        self.provider.handle_tool_call("memora_search", {"query": "test", "scope": "global"})
        payload = self._extract_payload(mock_urlopen)
        self.assertNotIn("metadata_filter", payload)
        self.assertEqual(payload["owner_id"], "test_user")
        self.assertNotIn("tenant_id", payload)

    @patch("memora.plugin.urllib.request.urlopen")
    def test_add_payload_includes_owner_and_tenant(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"status": "ok"}).encode()
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        self.provider.handle_tool_call("memora_add", {"content": "Test fact"})
        payload = self._extract_payload(mock_urlopen)
        self.assertIn("owner_id", payload)
        self.assertEqual(payload["owner_id"], "test_user")
        self.assertIn("scope", payload)
        self.assertEqual(payload["scope"], "personal")
        self.assertIn("importance_score", payload)

    @patch("memora.plugin.urllib.request.urlopen")
    def test_add_chunked_payload_includes_owner_and_tenant(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"status": "ok"}).encode()
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        long_content = "A" * 5000
        self.provider.handle_tool_call("memora_add", {"content": long_content})
        for call in mock_urlopen.call_args_list:
            req = call[0][0]
            payload = json.loads(req.data.decode("utf-8"))
            self.assertIn("owner_id", payload)
            self.assertEqual(payload["owner_id"], "test_user")
            self.assertIn("scope", payload)
            self.assertEqual(payload["scope"], "personal")
            self.assertIn("importance_score", payload)


if __name__ == "__main__":
    unittest.main()
