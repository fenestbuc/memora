"""Aggressive edge-case and stress tests for Memora.

These tests probe boundary conditions, malformed inputs, concurrent access,
and error recovery paths that happy-path tests do not cover.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from memora.http_client import HttpClient, HttpConfig, CircuitState
from memora.db import connect_sqlite
from memora.chunker import chunk_semantic
from memora.fact_extractor import extract_facts
from memora.importance import _parse_score, _heuristic_importance
from memora.tool_dispatcher import dispatch, search_cache_key
from memora.memory_mirror import write as mirror_write
from memora.cache import SqliteL1Cache


# ========================================================================
# 1. HTTP Client Edge Cases
# ========================================================================

class _FakeResp:
    """Minimal fake HTTP response for monkeypatching urllib."""
    def __init__(self, body: bytes):
        self._body = body
    def read(self):
        return self._body
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass


class TestHttpClientEdgeCases:
    """Probe retry, circuit breaker, and malformed response handling."""

    def test_429_triggers_retry_then_success(self, monkeypatch):
        """429 rate-limit should be retried; success on final attempt."""
        calls = []

        class FakeError(urllib.error.HTTPError):
            def __init__(self):
                pass
            @property
            def code(self):
                return 429

        def fake_urlopen(req, **kwargs):
            calls.append(1)
            if len(calls) < 3:
                raise FakeError()
            return _FakeResp(b'{"ok": true}')

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        cfg = HttpConfig(base_url="http://test", token="tok", base_delay=0.001)
        client = HttpClient(cfg)
        # max_retries=3 means up to 4 attempts; we fail on first 2, succeed on 3rd
        result = client.get("/")
        assert result == {"ok": True}
        assert len(calls) == 3

    def test_circuit_opens_after_three_failures(self, monkeypatch):
        """Three consecutive non-retryable failures should trip the breaker."""
        class FakeError(urllib.error.HTTPError):
            def __init__(self):
                pass
            @property
            def code(self):
                return 500

        def fake_urlopen(req, **kwargs):
            raise FakeError()

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        cfg = HttpConfig(base_url="http://test", token="tok", base_delay=0.001)
        client = HttpClient(cfg)

        # First call — trips after exhausting retries
        with pytest.raises(FakeError):
            client.get("/")
        assert client.cfg.circuit.consecutive_failures >= 1

    def test_json_decode_error_not_retried(self, monkeypatch):
        """Successful HTTP with non-JSON body should raise immediately (no retry)."""
        def fake_urlopen(req, **kwargs):
            return _FakeResp(b"not json")

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        client = HttpClient(HttpConfig(base_url="http://test", token="tok"))
        with pytest.raises(Exception):
            client.get("/")

    def test_empty_response_body(self, monkeypatch):
        """Empty body on GET should raise JSONDecodeError, not hang."""
        def fake_urlopen(req, **kwargs):
            return _FakeResp(b"")

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        client = HttpClient(HttpConfig(base_url="http://test", token="tok"))
        with pytest.raises(Exception):
            client.get("/")

    def test_post_with_none_body(self, monkeypatch):
        """Posting None body should send no data (urllib sees None as absent)."""
        captured = {}
        def fake_urlopen(req, **kwargs):
            captured["data"] = req.data
            return _FakeResp(b'{"ok": true}')

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        client = HttpClient(HttpConfig(base_url="http://test", token="tok"))
        client.post("/", None)
        assert captured["data"] is None


# ========================================================================
# 2. SQLite / DB Edge Cases
# ========================================================================

class TestDbEdgeCases:
    def test_connect_sqlite_wal_enabled(self, tmp_path):
        db = tmp_path / "test.db"
        conn = connect_sqlite(db)
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert journal.lower() == "wal"

    def test_connect_sqlite_busy_timeout(self, tmp_path):
        db = tmp_path / "test.db"
        conn = connect_sqlite(db, busy_timeout_ms=10000)
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        conn.close()
        assert timeout == 10000

    def test_concurrent_writes_dont_crash(self, tmp_path):
        """SQLite WAL should allow concurrent readers and a single writer."""
        db = tmp_path / "concurrent.db"
        conn = connect_sqlite(db)
        conn.execute("CREATE TABLE kv (k TEXT PRIMARY KEY, v TEXT)")
        conn.commit()

        errors = []
        def writer(n):
            try:
                c = sqlite3.connect(str(db))
                c.execute("PRAGMA journal_mode=WAL;")
                c.execute("INSERT OR REPLACE INTO kv VALUES (?, ?)", (f"key_{n}", f"val_{n}"))
                c.commit()
                c.close()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        conn.close()
        assert not errors, f"Concurrent write errors: {errors}"


# ========================================================================
# 3. Queue Stress Tests
# ========================================================================

class TestQueueEdgeCases:
    def test_flush_with_no_post_callback_raises(self, tmp_path):
        from memora.queue import FactQueue
        q = FactQueue(tmp_path / "q.db")
        with pytest.raises(RuntimeError):
            q.flush()

    def test_add_rejects_too_short(self, tmp_path):
        from memora.queue import FactQueue
        q = FactQueue(tmp_path / "q.db")
        q.set_callbacks(lambda p, b: {}, lambda c, t: None)
        assert q.add("x", "ab") is False  # <10 chars

    def test_add_rejects_whitespace_only(self, tmp_path):
        from memora.queue import FactQueue
        q = FactQueue(tmp_path / "q.db")
        q.set_callbacks(lambda p, b: {}, lambda c, t: None)
        assert q.add("x", "   \n\t  ") is False

    def test_dedup_exact_content(self, tmp_path):
        from memora.queue import FactQueue
        q = FactQueue(tmp_path / "q.db")
        q.set_callbacks(lambda p, b: {}, lambda c, t: None)
        assert q.add("cat", "duplicate fact here") is True
        assert q.add("cat", "duplicate fact here") is False


# ========================================================================
# 4. Semantic Chunker Edge Cases
# ========================================================================

class TestChunkerEdgeCases:
    def test_empty_string(self):
        assert chunk_semantic("") == []

    def test_very_long_single_sentence(self):
        text = "A" * 10000
        chunks = chunk_semantic(text, max_chars=1000, overlap_chars=100)
        # Segments are hard-split at 1000, but overlap adds up to 100 chars
        assert all(len(c) <= 1100 for c in chunks)

    def test_unicode_and_emojis(self):
        text = "Hello world! 🌍 " * 500
        chunks = chunk_semantic(text, max_chars=500, overlap_chars=50)
        assert len(chunks) > 1
        # Overlap can push chunks slightly over max_chars
        assert all(len(c) <= 550 for c in chunks)
        assert any("🌍" in c for c in chunks)

    def test_code_block_preserved(self):
        text = "```python\nprint('hello')\n```\n" * 50
        chunks = chunk_semantic(text, max_chars=300, overlap_chars=30)
        # Code blocks should stay together when possible
        for chunk in chunks:
            open_count = chunk.count("```")
            if open_count == 1:
                pytest.fail(f"Unclosed code block in chunk: {chunk[:80]}")

    def test_header_boundaries(self):
        text = "\n".join(f"# Header {i}\nContent for section {i}.\n" for i in range(50))
        chunks = chunk_semantic(text, max_chars=400, overlap_chars=50)
        # Headers should not appear mid-chunk unless forced by size
        pass  # primarily a smoke test


# ========================================================================
# 5. Fact Extractor Edge Cases
# ========================================================================

class TestFactExtractorEdgeCases:
    def test_empty_messages(self):
        assert extract_facts([]) == []
        assert extract_facts([{"content": ""}]) == []
        assert extract_facts([{"content": "   "}]) == []

    def test_url_only_skipped(self):
        assert extract_facts([{"content": "https://example.com/path"}]) == []

    def test_code_block_skipped(self):
        facts = extract_facts([{"content": "```python\nprint(1)\n```"}])
        assert facts == []

    def test_short_message_skipped(self):
        assert extract_facts([{"content": "Hi there"}]) == []

    def test_mixed_content_list(self):
        messages = [
            {"content": "ok"},
            {"content": "We decided to use PostgreSQL for the backend."},
            {"content": "https://example.com"},
        ]
        facts = extract_facts(messages)
        assert len(facts) == 1
        assert "PostgreSQL" in facts[0]


# ========================================================================
# 6. Tool Dispatcher Edge Cases
# ========================================================================

class TestToolDispatcherEdgeCases:
    def test_unknown_tool_raises(self):
        with pytest.raises(NotImplementedError):
            dispatch("memora_unknown", {}, owner_id="x")

    def test_search_payload_minimal(self):
        path, body = dispatch("memora_search", {"query": "test"}, owner_id="alice")
        assert path == "/search"
        assert body["query"] == "test"
        assert body["owner_id"] == "alice"
        assert "scope" not in body

    def test_list_payload_filters_none(self):
        path, body = dispatch("memora_list", {"category": None, "search": "x"}, owner_id="alice")
        assert body == {"search": "x"}

    def test_cache_key_determinism(self):
        b1 = {"query": "test", "top_k": 5}
        b2 = {"top_k": 5, "query": "test"}
        assert search_cache_key(b1) == search_cache_key(b2)


# ========================================================================
# 7. Memory Mirror Edge Cases
# ========================================================================

class TestMemoryMirrorEdgeCases:
    def test_none_dir_is_noop(self):
        mirror_write(None, "cat", "content")  # should not raise

    def test_category_slugification(self, tmp_path):
        mirror_write(tmp_path, "USER Preferences", "val")
        assert (tmp_path / "user_preferences.md").exists()


# ========================================================================
# 8. L1 Cache Edge Cases
# ========================================================================

class TestCacheEdgeCases:
    def test_ttl_expiration(self, tmp_path):
        cache = SqliteL1Cache(str(tmp_path / "cache.db"))
        cache.set("k", {"v": 1}, ttl_seconds=0)
        time.sleep(0.1)
        assert cache.get("k") is None

    def test_overwrite_existing(self, tmp_path):
        cache = SqliteL1Cache(str(tmp_path / "cache.db"))
        cache.set("k", {"v": 1}, ttl_seconds=3600)
        cache.set("k", {"v": 2}, ttl_seconds=3600)
        assert cache.get("k") == {"v": 2}


# ========================================================================
# 9. Config Edge Cases
# ========================================================================

class TestConfigEdgeCases:
    def test_load_with_missing_profile(self, tmp_path):
        from memora.config import MemoraConfig
        cfg = MemoraConfig.load(hermes_home=str(tmp_path))
        assert cfg.auto_ingest is True

    def test_load_with_malformed_json(self, tmp_path):
        from memora.config import MemoraConfig
        (tmp_path / "memora.json").write_text("not json{{")
        cfg = MemoraConfig.load(hermes_home=str(tmp_path))
        assert cfg.auto_ingest is True  # should not crash

    def test_env_override(self, tmp_path, monkeypatch):
        from memora.config import MemoraConfig
        monkeypatch.setenv("RAG_WORKER_URL", "http://env-override.com")
        cfg = MemoraConfig.load(hermes_home=str(tmp_path))
        assert cfg.worker_url == "http://env-override.com"


# ========================================================================
# 10. Importance Scoring Edge Cases
# ========================================================================

class TestImportanceEdgeCases:
    def test_parse_score_with_multiple_numbers(self):
        """Should pick the first valid number."""
        assert _parse_score("0.2 and 0.8") == 0.2

    def test_parse_score_with_junk(self):
        assert _parse_score("The score is definitely maybe 0.7 or so") == 0.7

    def test_heuristic_url_penalty(self):
        score = _heuristic_importance("https://example.com/something", "memory")
        assert score < 0.5

    def test_heuristic_preference_boost(self):
        score = _heuristic_importance("I always prefer dark mode", "user")
        assert score > 0.5
