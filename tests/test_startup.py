"""Tests for startup hook and CEO digest (Phase 3, Task 3).

Run with: pytest tests/test_startup.py -v
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from memora.startup_hook import process_startup


@pytest.fixture
def db_cursor():
    """Provide a SQLite cursor with the pending_actions schema."""
    tmpdir = tempfile.mkdtemp()
    db_path = Path(tmpdir) / "test_queue.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS pending_actions (
            id TEXT PRIMARY KEY,
            action_type TEXT,
            payload JSON,
            created_at TEXT,
            status TEXT DEFAULT 'pending'
        )"""
    )
    cursor = conn.cursor()
    yield cursor
    conn.close()
    import shutil

    shutil.rmtree(tmpdir, ignore_errors=True)


def test_process_pending_actions(db_cursor):
    """process_startup should execute send_ceo_digest actions and mark them completed."""
    db_cursor.execute(
        "INSERT INTO pending_actions (id, action_type) VALUES ('1', 'send_ceo_digest')"
    )
    db_cursor.connection.commit()

    with patch("memora.ceo_digest.send_digest") as mock_send:
        with patch("memora.startup_hook.subprocess.run") as _mock_run:
            process_startup(db_cursor)

    mock_send.assert_called_once()
    status = db_cursor.execute(
        "SELECT status FROM pending_actions WHERE id='1'"
    ).fetchone()[0]
    assert status == "completed"


def test_process_startup_unknown_action(db_cursor):
    """Unknown action types should be marked 'unknown', not crash."""
    db_cursor.execute(
        "INSERT INTO pending_actions (id, action_type) VALUES ('2', 'unknown_action')"
    )
    db_cursor.connection.commit()

    with patch("memora.ceo_digest.send_digest") as mock_send:
        with patch("memora.startup_hook.subprocess.run") as _mock_run:
            process_startup(db_cursor)

    mock_send.assert_not_called()
    status = db_cursor.execute(
        "SELECT status FROM pending_actions WHERE id='2'"
    ).fetchone()[0]
    assert status == "unknown"


def test_process_startup_pulls_git_main(db_cursor):
    """process_startup should fetch, checkout, and hard-reset to origin/main."""
    db_cursor.execute(
        "INSERT INTO pending_actions (id, action_type) VALUES ('3', 'send_ceo_digest')"
    )
    db_cursor.connection.commit()

    with patch("memora.ceo_digest.send_digest"):
        with patch("memora.startup_hook.subprocess.run") as mock_run:
            process_startup(db_cursor)

    called_commands = [call.args[0] for call in mock_run.call_args_list]
    assert ["git", "fetch", "origin", "main"] in called_commands
    assert ["git", "checkout", "main"] in called_commands
    assert ["git", "reset", "--hard", "origin/main"] in called_commands
