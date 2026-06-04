"""Tests for the memory decay engine."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from memora.decay import apply_decay_to_queue_db


def _fresh_db(tmp_path: Path) -> Path:
    db = tmp_path / "decay.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """CREATE TABLE facts (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            category TEXT,
            importance_score REAL DEFAULT 0.5,
            decayed_at TEXT,
            archived INTEGER DEFAULT 0,
            updated_at TEXT DEFAULT (datetime('now'))
        )"""
    )
    conn.commit()
    conn.close()
    return db


class TestDecayEngine:
    def test_decay_reduces_score(self, tmp_path: Path) -> None:
        """Facts older than half_life_days should have lower scores."""
        db = _fresh_db(tmp_path)
        conn = sqlite3.connect(db)
        old = datetime.now(timezone.utc) - timedelta(days=40)
        conn.execute(
            "INSERT INTO facts (id, content, importance_score, updated_at) VALUES (?, ?, ?, ?)",
            ("f1", "old fact", 0.8, old.isoformat()),
        )
        conn.commit()
        conn.close()

        stats = apply_decay_to_queue_db(db, half_life_days=30)

        conn = sqlite3.connect(db)
        row = conn.execute("SELECT importance_score, archived FROM facts WHERE id = 'f1'").fetchone()
        conn.close()
        assert row[0] < 0.8
        assert row[1] == 0  # still above archive threshold

    def test_archives_below_threshold(self, tmp_path: Path) -> None:
        """Facts that drop below archive_threshold get archived."""
        db = _fresh_db(tmp_path)
        conn = sqlite3.connect(db)
        very_old = datetime.now(timezone.utc) - timedelta(days=300)
        conn.execute(
            "INSERT INTO facts (id, content, importance_score, updated_at) VALUES (?, ?, ?, ?)",
            ("f1", "very old fact", 0.3, very_old.isoformat()),
        )
        conn.commit()
        conn.close()

        stats = apply_decay_to_queue_db(db, half_life_days=30, archive_threshold=0.1)

        conn = sqlite3.connect(db)
        row = conn.execute("SELECT importance_score, archived FROM facts WHERE id = 'f1'").fetchone()
        conn.close()
        assert row[1] == 1

    def test_recent_facts_unchanged(self, tmp_path: Path) -> None:
        """Facts from 1 hour ago retain ~99.9% of their score."""
        db = _fresh_db(tmp_path)
        conn = sqlite3.connect(db)
        recent = datetime.now(timezone.utc) - timedelta(hours=1)
        conn.execute(
            "INSERT INTO facts (id, content, importance_score, updated_at) VALUES (?, ?, ?, ?)",
            ("f1", "recent fact", 0.9, recent.isoformat()),
        )
        conn.commit()
        conn.close()

        apply_decay_to_queue_db(db, half_life_days=30)

        conn = sqlite3.connect(db)
        score = conn.execute("SELECT importance_score FROM facts WHERE id = 'f1'").fetchone()[0]
        conn.close()
        assert score == pytest.approx(0.9, abs=0.01)

    def test_empty_db(self, tmp_path: Path) -> None:
        """Running decay on an empty DB is a no-op."""
        db = _fresh_db(tmp_path)
        stats = apply_decay_to_queue_db(db)
        assert stats["decayed"] == 0
        assert stats["archived"] == 0
