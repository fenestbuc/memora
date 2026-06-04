"""Memory decay engine — applies exponential decay to fact importance scores
and archives facts that fall below a configurable threshold.

Decay formula:
    decayed_score = original_score * exp(-lambda * days_since_update)
    where lambda = ln(2) / half_life_days

Archived facts (archived=1) are excluded from default search but remain
stored for audit and can be queried explicitly via memora_archive.
"""

from __future__ import annotations

import logging
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .importance import compute_importance

logger = logging.getLogger(__name__)


def apply_decay(
    conn: sqlite3.Connection,
    half_life_days: float = 90.0,
    archive_threshold: float = 0.15,
) -> dict[str, int]:
    """Apply exponential decay to all non-archived facts and archive those
    that fall below the threshold.

    Also scores any un-scored facts using the LLM heuristic before decaying.

    Args:
        conn: SQLite connection to the RAG queue or a local mirror DB.
        half_life_days: Number of days for importance to halve.
        archive_threshold: Facts scoring below this after decay are archived.

    Returns:
        Dict with counts: ``{"scored": int, "decayed": int, "archived": int}``
    """
    lambda_ = math.log(2) / half_life_days
    now = datetime.now(timezone.utc)
    stats = {"scored": 0, "decayed": 0, "archived": 0}

    cursor = conn.execute(
        """
        SELECT id, content, category, importance_score, updated_at, archived
        FROM facts
        WHERE archived = 0 OR archived IS NULL
        """
    )
    rows = cursor.fetchall()

    for row in rows:
            fact_id, content, category, current_score, updated_at_str, _archived = row

            # Score un-scored facts
            if current_score is None:
                try:
                    current_score = compute_importance(content or "", category or "memory")
                    conn.execute(
                        "UPDATE facts SET importance_score = ? WHERE id = ?",
                        (current_score, fact_id),
                    )
                    stats["scored"] += 1
                except Exception as exc:
                    logger.warning("Could not score fact %s: %s", fact_id, exc)
                    current_score = 0.5

            # Compute age in days
            try:
                updated_at = datetime.fromisoformat(updated_at_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                updated_at = now

            age_days = max(0, (now - updated_at).total_seconds() / 86400)
            decayed_score = current_score * math.exp(-lambda_ * age_days)

            # Archive if below threshold
            if decayed_score < archive_threshold:
                conn.execute(
                    """
                    UPDATE facts
                    SET importance_score = ?, decayed_at = ?, archived = 1, updated_at = ?
                    WHERE id = ?
                    """,
                    (round(decayed_score, 4), now.isoformat(), now.isoformat(), fact_id),
                )
                stats["archived"] += 1
                logger.debug("Archived fact %s (score %.3f)", fact_id, decayed_score)
            else:
                conn.execute(
                    "UPDATE facts SET importance_score = ?, updated_at = ? WHERE id = ?",
                    (round(decayed_score, 4), now.isoformat(), fact_id),
                )
                stats["decayed"] += 1

    conn.commit()

    total = stats["scored"] + stats["decayed"] + stats["archived"]
    if total == 0:
        logger.info("No facts eligible for decay")
    else:
        logger.info(
            "Decay complete: scored=%d, decayed=%d, archived=%d",
            stats["scored"], stats["decayed"], stats["archived"]
        )
    return stats


def apply_decay_to_queue_db(
    queue_path: Path,
    half_life_days: float = 90.0,
    archive_threshold: float = 0.15,
) -> dict[str, int]:
    """Convenience wrapper that opens the queue DB, runs decay, and closes."""
    conn = sqlite3.connect(str(queue_path))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")

    # Ensure schema exists
    conn.execute("""
        CREATE TABLE IF NOT EXISTS facts (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            category TEXT,
            importance_score REAL,
            decayed_at TEXT,
            archived INTEGER DEFAULT 0,
            updated_at TEXT
        )
    """)
    conn.commit()

    try:
        return apply_decay(conn, half_life_days, archive_threshold)
    finally:
        conn.close()
