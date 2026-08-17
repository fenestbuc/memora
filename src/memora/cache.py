"""Local SQLite L1 cache implementation for Memora."""

from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import time
from dataclasses import dataclass, field


@dataclass
class CacheStats:
    """Simple hit / miss / total counters."""

    hits: int = 0
    misses: int = 0
    total: int = 0


class SqliteL1Cache:
    """A lightweight, SQLite-backed key-value cache with optional TTL."""

    def __init__(self, db_path: pathlib.Path | str | None = None) -> None:
        """Open (or create) the SQLite cache.

        Args:
            db_path: Filesystem path for the SQLite database.  If *None* a
                default file in the current working directory is used.
        """
        if db_path is None:
            hermes_home = pathlib.Path(os.environ.get("HERMES_HOME", pathlib.Path.home() / ".hermes"))
            db_path = hermes_home / "cache" / "memora" / "l1.db"
        self.db_path = pathlib.Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._stats = CacheStats()
        self._ensure_schema()

    # --------------------------------------------------------------------- #
    # Internals
    # --------------------------------------------------------------------- #
    def _ensure_schema(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    expires_at REAL
                )
                """
            )
            conn.commit()

    # --------------------------------------------------------------------- #
    # Public API
    # --------------------------------------------------------------------- #
    def set(
        self,
        key: str,
        value,
        ttl_seconds: float | None = None,
    ) -> None:
        """Store *value* under *key*.

        Args:
            ttl_seconds: When provided, the entry is considered expired after
                ``ttl_seconds`` have elapsed.
        """
        self._stats.total += 1
        expires_at = None if ttl_seconds is None else time.time() + ttl_seconds
        serialized = json.dumps(value)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO cache (key, value, expires_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    expires_at = excluded.expires_at
                """,
                (key, serialized, expires_at),
            )
            conn.commit()

    def get(self, key: str):
        """Retrieve the value stored under *key*.

        Returns the deserialized value, or ``None`` if the key is missing or
        expired.
        """
        self._stats.total += 1
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT value, expires_at FROM cache WHERE key = ?",
                (key,),
            ).fetchone()

            if row is None:
                self._stats.misses += 1
                return None

            serialized, expires_at = row
            if expires_at is not None and time.time() > expires_at:
                self._stats.misses += 1
                conn.execute("DELETE FROM cache WHERE key = ?", (key,))
                conn.commit()
                return None

            self._stats.hits += 1
            return json.loads(serialized)

    def delete(self, key: str) -> None:
        """Remove *key* from the cache if it exists."""
        self._stats.total += 1
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM cache WHERE key = ?", (key,))
            conn.commit()

    def clear(self) -> None:
        """Delete all cached entrieswhile preserving the table schema."""
        self._stats.total += 1
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM cache")
            conn.commit()

    @property
    def stats(self) -> CacheStats:
        """Return the current cache statistics."""
        return self._stats
