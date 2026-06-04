"""SQLite write-behind queue for Memora facts.

Crash-safe, deduplicated, with a background flush thread.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import connect_sqlite

logger = logging.getLogger(__name__)

_INIT_SQL = [
    """CREATE TABLE IF NOT EXISTS queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        action TEXT NOT NULL,
        category TEXT,
        content TEXT NOT NULL,
        source_session TEXT,
        source_file TEXT,
        created_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS failed_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        action TEXT NOT NULL,
        category TEXT,
        content TEXT NOT NULL,
        source_session TEXT,
        source_file TEXT,
        created_at TEXT,
        failed_at TEXT,
        error TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS seen_hashes (
        hash TEXT PRIMARY KEY,
        created_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS facts (
        id TEXT PRIMARY KEY,
        content TEXT NOT NULL,
        category TEXT,
        superseded_by TEXT,
        scope TEXT DEFAULT 'personal',
        created_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS pending_actions (
        id TEXT PRIMARY KEY,
        action_type TEXT,
        payload JSON,
        created_at TEXT,
        status TEXT DEFAULT 'pending'
    )""",
]


class FactQueue:
    """Thread-safe SQLite-backed queue with dedup and background flush."""

    def __init__(
        self,
        queue_path: Path,
        *,
        session_id: str = "",
        max_seen_hashes: int = 10000,
        chunk_size: int = 100,
    ) -> None:
        self._path = queue_path
        self._session_id = session_id
        self._max_seen = max_seen_hashes
        self._chunk_size = chunk_size

        self._lock = threading.Lock()
        self._conn = connect_sqlite(queue_path, check_same_thread=False)
        for sql in _INIT_SQL:
            self._conn.execute(sql)
        self._conn.commit()

        self._seen: set[str] = set()
        self._seen_order: deque[str] = deque()
        self._load_seen_hashes()

        self._flush_thread: threading.Thread | None = None
        self._flush_stop = threading.Event()

        # Callbacks injected by the provider
        self._post_fn: Any | None = None  # callable(path, body) -> dict
        self._mirror_fn: Any | None = None  # callable(category, content)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_callbacks(self, post_fn: Any, mirror_fn: Any) -> None:
        """Inject HTTP post and mirror callbacks."""
        self._post_fn = post_fn
        self._mirror_fn = mirror_fn

    def add(self, category: str, content: str) -> bool:
        """Enqueue a fact if it passes validation and dedup.

        Returns True if the fact was actually queued.
        """
        stripped = content.strip()
        if len(stripped) < 10:
            return False
        if not any(c.isalnum() for c in stripped):
            return False

        h = hashlib.sha256(content.encode("utf-8")).hexdigest()
        with self._lock:
            if h in self._seen:
                return False
            self._seen.add(h)
            self._seen_order.append(h)
            while len(self._seen_order) > self._max_seen:
                old = self._seen_order.popleft()
                self._seen.discard(old)

            now = datetime.now(timezone.utc).isoformat()
            self._conn.execute(
                "INSERT OR IGNORE INTO seen_hashes (hash, created_at) VALUES (?, ?)",
                (h, now),
            )
            self._conn.execute(
                "INSERT INTO queue (action, category, content, source_session, created_at) VALUES (?, ?, ?, ?, ?)",
                ("add", category, content, self._session_id, now),
            )
            self._conn.commit()

        if self._mirror_fn:
            self._mirror_fn(category, content)
        return True

    def flush(self) -> dict[str, int]:
        """Flush queued facts to the RAG backend.

        Returns counters: ``flushed``, ``failed``.
        """
        if self._post_fn is None:
            raise RuntimeError("FactQueue post callback not set")

        with self._lock:
            cursor = self._conn.execute(
                "SELECT id, action, category, content, source_session, source_file, created_at FROM queue ORDER BY id"
            )
            rows = cursor.fetchall()
            if not rows:
                return {"flushed": 0, "failed": 0}

            flushed = 0
            failed = 0
            ids_to_delete: list[int] = []

            for i in range(0, len(rows), self._chunk_size):
                chunk = rows[i : i + self._chunk_size]
                facts = [
                    {
                        "id": f"{row[2]}::{self._session_id or 'unknown'}::{row[0]}",
                        "category": row[2],
                        "content": row[3],
                        "source_session": row[4] or self._session_id,
                        "source_file": row[5],
                    }
                    for row in chunk
                ]

                try:
                    result = self._post_fn("/memory/import", {"facts": facts})
                    if result.get("success"):
                        ids_to_delete.extend(r[0] for r in chunk)
                        flushed += len(chunk)
                    else:
                        raise Exception(f"Batch import failed: {result}")
                except Exception as batch_err:
                    logger.debug("Batch import failed, falling back: %s", batch_err)
                    for row in chunk:
                        row_id, action, category, content, source_session, source_file, created_at = row
                        try:
                            self._post_fn("/memory/add", {
                                "content": content,
                                "category": category,
                                "source_session": source_session or self._session_id,
                            })
                            ids_to_delete.append(row_id)
                            flushed += 1
                        except Exception as e:
                            logger.debug("Failed to flush queue item %s: %s", row_id, e)
                            failed += 1
                            now = datetime.now(timezone.utc).isoformat()
                            self._conn.execute(
                                """INSERT INTO failed_queue
                                   (action, category, content, source_session, source_file, created_at, failed_at, error)
                                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                                (action, category, content, source_session, source_file, created_at, now, str(e)),
                            )
                            ids_to_delete.append(row_id)

            if ids_to_delete:
                placeholders = ",".join("?" * len(ids_to_delete))
                self._conn.execute(f"DELETE FROM queue WHERE id IN ({placeholders})", ids_to_delete)
                self._conn.commit()

            return {"flushed": flushed, "failed": failed}

    def vacuum(self) -> None:
        """Run VACUUM to reclaim space."""
        try:
            self._conn.commit()
            import sqlite3

            conn = sqlite3.connect(self._path)
            conn.execute("VACUUM")
            conn.close()
            logger.debug("Vacuumed queue: %s", self._path)
        except Exception as e:
            logger.debug("Queue vacuum failed: %s", e)

    def start_background_flush(self, interval_sec: float = 60.0) -> None:
        """Start a daemon thread that flushes periodically."""
        if self._flush_thread is not None and self._flush_thread.is_alive():
            return
        self._flush_stop.clear()

        def _run():
            while not self._flush_stop.is_set():
                self._flush_stop.wait(interval_sec)
                if not self._flush_stop.is_set():
                    try:
                        self.flush()
                    except Exception as e:
                        logger.debug("Background flush failed: %s", e)

        self._flush_thread = threading.Thread(target=_run, daemon=True, name="rag-flush")
        self._flush_thread.start()

    def stop_background_flush(self) -> None:
        """Signal the background thread to stop and wait."""
        if self._flush_thread is None:
            return
        self._flush_stop.set()
        self._flush_thread.join(timeout=5.0)
        if self._flush_thread.is_alive():
            logger.warning("Background flush thread did not stop within 5s")

    def close(self) -> None:
        """Stop background flush and close the connection."""
        self.stop_background_flush()
        try:
            self._conn.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load_seen_hashes(self) -> None:
        cursor = self._conn.execute("SELECT hash FROM seen_hashes")
        for row in cursor:
            h = row[0]
            self._seen.add(h)
            self._seen_order.append(h)
