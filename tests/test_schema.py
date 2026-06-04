"""Schema tests for Memora Enterprise Phase 1 (append-only + pending actions).

Run with: pytest tests/test_schema.py -v
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from memora.plugin import MemoraProvider


@pytest.fixture
def db_cursor():
    """Provide a SQLite cursor for the initialized Memora schema."""
    tmpdir = tempfile.mkdtemp()
    provider = MemoraProvider()
    provider._hermes_home = tmpdir
    provider._agent_identity = "test"
    provider._session_id = "test_session"
    provider._queue_path = Path(tmpdir) / "test_queue.db"
    provider._init_queue()

    conn = sqlite3.connect(provider._queue_path)
    cursor = conn.cursor()
    yield cursor
    conn.close()
    import shutil

    shutil.rmtree(tmpdir, ignore_errors=True)


def test_facts_table_has_append_only_columns(db_cursor):
    """facts table must have superseded_by and scope columns."""
    cursor = db_cursor.execute("PRAGMA table_info(facts)")
    columns = {col[1]: col for col in cursor.fetchall()}
    assert "superseded_by" in columns, "Missing superseded_by column in facts table"
    assert "scope" in columns, "Missing scope column in facts table"
    # Verify type defaults
    assert columns["superseded_by"][2] == "TEXT"
    assert columns["scope"][4] == "'personal'"  # default value


def test_pending_actions_table_exists(db_cursor):
    """pending_actions table must exist with correct schema."""
    cursor = db_cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='pending_actions'"
    )
    assert cursor.fetchone() is not None, "Missing pending_actions table"

    cursor = db_cursor.execute("PRAGMA table_info(pending_actions)")
    columns = {col[1]: col for col in cursor.fetchall()}
    assert "id" in columns
    assert "action_type" in columns
    assert "payload" in columns
    assert "created_at" in columns
    assert "status" in columns
    assert columns["status"][4] == "'pending'"  # default value
