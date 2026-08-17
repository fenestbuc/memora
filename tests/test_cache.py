"""Failing unit tests for the local SQLite L1 cache.

These tests describe the expected behavior of the cache before the implementation
exists.  Running ``pytest`` in this directory should fail with import or
``AttributeError`` / ``NotImplementedError`` failures.
"""

from __future__ import annotations

import pathlib
import sqlite3
import time

import pytest

# The cache implementation does not exist yet — we import by the intended API.
from memora.cache import SqliteL1Cache


@pytest.fixture
def tmp_db(tmp_path: pathlib.Path) -> pathlib.Path:
    """Return a temporary database path that is cleaned up automatically."""
    return tmp_path / "cache.db"


class TestInit:
    """Tests for cache construction and database bootstrapping."""

    def test_init_creates_db_file(self, tmp_db: pathlib.Path) -> None:
        """Creating a cache instance should create the SQLite file on disk."""
        assert not tmp_db.exists()
        SqliteL1Cache(db_path=tmp_db)
        assert tmp_db.exists()

    def test_init_ensures_schema(self, tmp_db: pathlib.Path) -> None:
        """The cache table(s) must be present after instantiation."""
        SqliteL1Cache(db_path=tmp_db)
        conn = sqlite3.connect(tmp_db)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='cache';"
        )
        assert cursor.fetchone() is not None
        conn.close()

    def test_default_db_path_is_profile_cache(self, tmp_path: pathlib.Path, monkeypatch) -> None:
        """Default cache state belongs under HERMES_HOME, never the CWD."""
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
        cache = SqliteL1Cache()
        assert cache.db_path == tmp_path / "hermes-home" / "cache" / "memora" / "l1.db"


class TestSetAndGet:
    """Tests for storing and retrieving values."""

    def test_set_and_get_string(self, tmp_db: pathlib.Path) -> None:
        cache = SqliteL1Cache(db_path=tmp_db)
        cache.set("hello", "world")
        assert cache.get("hello") == "world"

    def test_set_and_get_int(self, tmp_db: pathlib.Path) -> None:
        cache = SqliteL1Cache(db_path=tmp_db)
        cache.set("meaning", 42)
        assert cache.get("meaning") == 42

    def test_set_and_get_dict(self, tmp_db: pathlib.Path) -> None:
        cache = SqliteL1Cache(db_path=tmp_db)
        payload = {"foo": "bar", "nested": {"count": 99}}
        cache.set("cfg", payload)
        assert cache.get("cfg") == payload

    def test_get_missing_key_returns_none(self, tmp_db: pathlib.Path) -> None:
        cache = SqliteL1Cache(db_path=tmp_db)
        assert cache.get("no-such-key") is None

    def test_overwrite_existing_key(self, tmp_db: pathlib.Path) -> None:
        cache = SqliteL1Cache(db_path=tmp_db)
        cache.set("key", "first")
        cache.set("key", "second")
        assert cache.get("key") == "second"

    def test_empty_string_key(self, tmp_db: pathlib.Path) -> None:
        cache = SqliteL1Cache(db_path=tmp_db)
        cache.set("", "empty-key-value")
        assert cache.get("") == "empty-key-value"

    def test_none_value_roundtrip(self, tmp_db: pathlib.Path) -> None:
        cache = SqliteL1Cache(db_path=tmp_db)
        cache.set("null", None)
        assert cache.get("null") is None


class TestDelete:
    """Tests for removing entries."""

    def test_deletes_existing_key(self, tmp_db: pathlib.Path) -> None:
        cache = SqliteL1Cache(db_path=tmp_db)
        cache.set("x", 1)
        cache.delete("x")
        assert cache.get("x") is None

    def test_delete_nonexistent_key_is_noop(self, tmp_db: pathlib.Path) -> None:
        cache = SqliteL1Cache(db_path=tmp_db)
        cache.delete("nonexistent")  # should not raise

    def test_delete_multiple_keys(self, tmp_db: pathlib.Path) -> None:
        cache = SqliteL1Cache(db_path=tmp_db)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)
        cache.delete("a")
        cache.delete("b")
        assert cache.get("a") is None
        assert cache.get("b") is None
        assert cache.get("c") == 3


class TestClear:
    """Tests for purging the entire cache."""

    def test_clear_removes_all_entries(self, tmp_db: pathlib.Path) -> None:
        cache = SqliteL1Cache(db_path=tmp_db)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.clear()
        assert cache.get("a") is None
        assert cache.get("b") is None

    def test_clear_on_empty_cache(self, tmp_db: pathlib.Path) -> None:
        cache = SqliteL1Cache(db_path=tmp_db)
        cache.clear()  # should not raise
        assert cache.get("anything") is None

    def test_clear_does_not_corrupt_schema(self, tmp_db: pathlib.Path) -> None:
        cache = SqliteL1Cache(db_path=tmp_db)
        cache.set("x", 10)
        cache.clear()
        cache.set("y", 20)
        assert cache.get("y") == 20


class TestTTL:
    """Tests for time-to-live / automatic expiration."""

    def test_expired_key_returns_none(self, tmp_db: pathlib.Path) -> None:
        cache = SqliteL1Cache(db_path=tmp_db)
        cache.set("short", "lived", ttl_seconds=0)
        time.sleep(0.05)  # small buffer for system clock
        assert cache.get("short") is None

    def test_unexpired_key_returns_value(self, tmp_db: pathlib.Path) -> None:
        cache = SqliteL1Cache(db_path=tmp_db)
        cache.set("long", "lived", ttl_seconds=10)
        assert cache.get("long") == "lived"

    def test_zero_ttl_means_immediate_expiry(self, tmp_db: pathlib.Path) -> None:
        cache = SqliteL1Cache(db_path=tmp_db)
        cache.set("instant", "poof", ttl_seconds=0)
        time.sleep(0.01)
        assert cache.get("instant") is None

    def test_default_ttl_is_no_expiry(self, tmp_db: pathlib.Path) -> None:
        cache = SqliteL1Cache(db_path=tmp_db)
        cache.set("forever", "stamp")
        assert cache.get("forever") == "stamp"


class TestStats:
    """Tests for basic cache instrumentation."""

    def test_hit_count_increments(self, tmp_db: pathlib.Path) -> None:
        cache = SqliteL1Cache(db_path=tmp_db)
        cache.set("key", "value")
        _ = cache.get("key")
        _ = cache.get("key")
        assert cache.stats.hits == 2

    def test_miss_count_increments(self, tmp_db: pathlib.Path) -> None:
        cache = SqliteL1Cache(db_path=tmp_db)
        _ = cache.get("missing")
        _ = cache.get("also-missing")
        assert cache.stats.misses == 2

    def test_total_operations_tracked(self, tmp_db: pathlib.Path) -> None:
        cache = SqliteL1Cache(db_path=tmp_db)
        cache.set("k", "v")
        cache.get("k")
        cache.get("x")
        assert cache.stats.total == 3

    def test_stats_after_clear(self, tmp_db: pathlib.Path) -> None:
        cache = SqliteL1Cache(db_path=tmp_db)
        cache.set("k", "v")
        cache.get("k")
        cache.clear()
        assert cache.stats.hits == 1  # stats should survive clear


class TestConcurrency:
    """Smoke tests for basic thread-safety expectations."""

    def test_multiple_instances_share_state(self, tmp_db: pathlib.Path) -> None:
        """Two cache instances pointing to the same DB should see each other's writes."""
        cache_a = SqliteL1Cache(db_path=tmp_db)
        cache_b = SqliteL1Cache(db_path=tmp_db)
        cache_a.set("shared", "data")
        assert cache_b.get("shared") == "data"
