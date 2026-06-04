"""Tests for Discord session continuity."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from memora.discord_sessions import get_or_create_session, cleanup_expired_sessions


class TestDiscordSessionStore:
    def test_get_or_create_creates_new(self, tmp_path: Path) -> None:
        sid = get_or_create_session("chan_1", hermes_home=str(tmp_path))
        assert len(sid) == 36  # UUIDv4 length

    def test_get_or_create_returns_existing(self, tmp_path: Path) -> None:
        sid1 = get_or_create_session("chan_1", hermes_home=str(tmp_path))
        sid2 = get_or_create_session("chan_1", hermes_home=str(tmp_path))
        assert sid1 == sid2

    def test_cleanup_removes_expired(self, tmp_path: Path) -> None:
        get_or_create_session("chan_1", hermes_home=str(tmp_path))
        # Manually age the row
        db = tmp_path / "memora_discord_sessions.db"
        conn = sqlite3.connect(db)
        old = datetime.now(timezone.utc) - timedelta(hours=25)
        conn.execute(
            "UPDATE discord_session SET last_message_at = ? WHERE channel_id = ?",
            (old.isoformat(), "chan_1"),
        )
        conn.commit()
        conn.close()

        deleted = cleanup_expired_sessions(hermes_home=str(tmp_path), ttl_hours=24)
        assert deleted == 1

        # Should create a new session now
        sid = get_or_create_session("chan_1", hermes_home=str(tmp_path))
        assert sid  # non-empty

    def test_empty_channel_returns_uuid(self, tmp_path: Path) -> None:
        sid = get_or_create_session("", hermes_home=str(tmp_path))
        assert len(sid) == 36
