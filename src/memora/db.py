"""Shared SQLite connection helper with WAL and sensible defaults."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional


def connect_sqlite(
    path: Path | str,
    *,
    wal: bool = True,
    synchronous: str = "NORMAL",
    busy_timeout_ms: int = 5000,
    check_same_thread: bool = False,
) -> sqlite3.Connection:
    """Open a SQLite connection with production-safe PRAGMAs.

    Args:
        path: Database file path.
        wal: Enable WAL journal mode (default True).
        synchronous: SYNCHRONOUS level (default NORMAL).
        busy_timeout_ms: BUSY_TIMEOUT in milliseconds (default 5000).
        check_same_thread: Passed to sqlite3.connect (default False for
            multi-threaded use; set True in single-threaded contexts).

    Returns:
        An open sqlite3 Connection.
    """
    conn = sqlite3.connect(str(path), check_same_thread=check_same_thread)
    if wal:
        conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(f"PRAGMA synchronous={synchronous};")
    conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms};")
    return conn
