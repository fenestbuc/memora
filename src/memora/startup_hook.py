"""Startup interceptor for Memora Enterprise.

Processes ``pending_actions`` from the local SQLite queue on boot and
pulls the latest ``main`` branch.
"""

from __future__ import annotations

import logging
import sqlite3
import subprocess
from typing import Any

from . import ceo_digest

logger = logging.getLogger(__name__)


def _git_pull_main() -> None:
    """Reset local main to latest origin/main safely."""
    try:
        subprocess.run(
            ["git", "fetch", "origin", "main"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "checkout", "main"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "reset", "--hard", "origin/main"],
            check=True,
            capture_output=True,
        )
        logger.info("Reset main to latest origin/main.")
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        logger.warning("Git sync failed: %s", exc)


def process_startup(conn_or_cursor: Any) -> None:
    """Process pending actions and sync git state on startup.

    Args:
        conn_or_cursor: An active :class:`sqlite3.Connection` or
            :class:`sqlite3.Cursor`.  Updates are committed automatically
            when a ``Connection`` is passed (or inferred from a cursor).
    """
    if isinstance(conn_or_cursor, sqlite3.Connection):
        conn = conn_or_cursor
        cursor = conn.cursor()
    else:
        cursor = conn_or_cursor
        conn = getattr(cursor, "connection", None)
        if conn is None:
            raise ValueError(
                "A sqlite3 Connection or Cursor with a .connection attribute is required"
            )

    cursor.execute(
        "SELECT id, action_type, payload FROM pending_actions "
        "WHERE status = 'pending' ORDER BY created_at"
    )
    rows = cursor.fetchall()

    action_map = {
        "send_ceo_digest": ceo_digest.send_digest,
    }

    for row in rows:
        row_id, action_type, _payload = row
        handler = action_map.get(action_type)
        if handler is not None:
            try:
                handler()
                cursor.execute(
                    "UPDATE pending_actions SET status = 'completed' WHERE id = ?",
                    (row_id,),
                )
                logger.info("Processed pending action %s (%s)", row_id, action_type)
            except Exception as exc:  # pragma: no cover
                logger.error("Failed to process action %s: %s", row_id, exc)
                cursor.execute(
                    "UPDATE pending_actions SET status = 'failed' WHERE id = ?",
                    (row_id,),
                )
        else:
            logger.warning("Unknown pending action type: %s", action_type)
            cursor.execute(
                "UPDATE pending_actions SET status = 'unknown' WHERE id = ?",
                (row_id,),
            )

    if conn is not None:
        conn.commit()

    _git_pull_main()
