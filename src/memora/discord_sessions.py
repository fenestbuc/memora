"""Discord thread session continuity.

Maps Discord channel_ids to persistent session_ids so that follow-up
messages in the same thread maintain conversational context.

Sessions expire after 24 hours of inactivity (configurable).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_TTL_HOURS = 24


def _get_db_path(hermes_home: str | None = None) -> Path:
    home = Path(hermes_home) if hermes_home else Path.home() / ".hermes"
    return home / "memora_discord_sessions.db"


def _init_db(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS discord_session (
            channel_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            last_message_at TEXT NOT NULL,
            turn_count INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_session_last_at ON discord_session(last_message_at)
    """)
    conn.commit()


def get_or_create_session(
    channel_id: str,
    hermes_home: str | None = None,
    ttl_hours: int = DEFAULT_TTL_HOURS,
) -> str:
    """Return an existing session_id for *channel_id*, or create a new one.

    If the session is older than *ttl_hours*, a new session_id is generated.

    Args:
        channel_id: Discord channel / thread ID.
        hermes_home: Path to Hermes home directory.
        ttl_hours: Session expiry in hours (default 24).

    Returns:
        A UUIDv4 session_id string.
    """
    if not channel_id:
        # Stateless fallback — generate ephemeral session
        return str(uuid.uuid4())

    db_path = _get_db_path(hermes_home)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    _init_db(conn)

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=ttl_hours)

    try:
        # Try to find existing active session
        cursor = conn.execute(
            "SELECT session_id, last_message_at, turn_count FROM discord_session WHERE channel_id = ?",
            (channel_id,),
        )
        row = cursor.fetchone()

        if row:
            session_id, last_at_str, turn_count = row
            last_at = datetime.fromisoformat(last_at_str)
            if last_at >= cutoff:
                # Session is still valid — update activity
                conn.execute(
                    "UPDATE discord_session SET last_message_at = ?, turn_count = ? WHERE channel_id = ?",
                    (now.isoformat(), turn_count + 1, channel_id),
                )
                conn.commit()
                logger.debug(
                    "Discord session %s continued for channel %s (turn %d)",
                    session_id, channel_id, turn_count + 1
                )
                return session_id

        # Expired or missing — create new session
        new_session = str(uuid.uuid4())
        conn.execute(
            """
            INSERT OR REPLACE INTO discord_session (channel_id, session_id, last_message_at, turn_count)
            VALUES (?, ?, ?, ?)
            """,
            (channel_id, new_session, now.isoformat(), 1),
        )
        conn.commit()
        logger.info("New Discord session %s for channel %s", new_session, channel_id)
        return new_session
    finally:
        conn.close()


def cleanup_expired_sessions(
    hermes_home: str | None = None,
    ttl_hours: int = DEFAULT_TTL_HOURS,
) -> int:
    """Delete sessions older than *ttl_hours*.

    Returns:
        Number of deleted rows.
    """
    db_path = _get_db_path(hermes_home)
    if not db_path.exists():
        return 0

    conn = sqlite3.connect(str(db_path))
    _init_db(conn)

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=ttl_hours)).isoformat()

    try:
        cursor = conn.execute("DELETE FROM discord_session WHERE last_message_at < ?", (cutoff,))
        conn.commit()
        deleted = cursor.rowcount
        if deleted:
            logger.info("Cleaned up %d expired Discord sessions", deleted)
        return deleted
    finally:
        conn.close()
