"""Tests for the memora-doctor health check command."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

from memora.doctor import run_doctor


def test_run_doctor_reports_unconfigured() -> None:
    with patch.dict("os.environ", {"RAG_WORKER_URL": "", "RAG_AUTH_TOKEN": ""}, clear=False):
        code = run_doctor(json_output=True)

    assert code == 1


def test_run_doctor_reports_healthy(tmp_path: Path) -> None:
    company_dir = tmp_path / "company-memory"
    company_dir.mkdir()
    (company_dir / ".git").mkdir()
    queue_db = tmp_path / "memora_queue_default.db"
    conn = sqlite3.connect(queue_db)
    conn.executescript(
        "CREATE TABLE queue (id INTEGER PRIMARY KEY);"
        "CREATE TABLE failed_queue (id INTEGER PRIMARY KEY);"
    )
    conn.close()

    env = {
        "RAG_WORKER_URL": "https://worker.test",
        "RAG_AUTH_TOKEN": "token",
        "HOME": str(tmp_path),
    }

    mock_health = {"status": "ok", "version": "1.2.0", "models": {"llm": "test"}}
    mock_stats = {"total": 100, "pending_vector_sync": 5}

    with patch.dict("os.environ", env, clear=True):
        with patch("memora.doctor.resolve_company_memory_dir", return_value=company_dir):
            with patch(
                "memora.doctor.HttpClient",
                return_value=MagicMock(
                    get=MagicMock(side_effect=[mock_health, mock_stats]),
                    post=MagicMock(return_value={}),
                ),
            ):
                code = run_doctor(json_output=True)

    assert code == 0


def test_run_doctor_warns_on_pending_sync(tmp_path: Path) -> None:
    company_dir = tmp_path / "company-memory"
    company_dir.mkdir()
    (company_dir / ".git").mkdir()

    env = {
        "RAG_WORKER_URL": "https://worker.test",
        "RAG_AUTH_TOKEN": "token",
        "HOME": str(tmp_path),
    }

    mock_health = {"status": "ok", "version": "1.2.0", "models": {"llm": "test"}}
    mock_stats = {"total": 100, "pending_vector_sync": 999}

    with patch.dict("os.environ", env, clear=True):
        with patch("memora.doctor.resolve_company_memory_dir", return_value=company_dir):
            with patch(
                "memora.doctor.HttpClient",
                return_value=MagicMock(
                    get=MagicMock(side_effect=[mock_health, mock_stats]),
                    post=MagicMock(return_value={}),
                ),
            ):
                code = run_doctor(json_output=True)

    assert code == 1
