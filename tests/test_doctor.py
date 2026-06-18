"""Tests for the memora-doctor health check command."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from memora.doctor import _load_queue_counts, _repo_sync_lag_seconds, run_doctor


def _capture_json(capsys: pytest.CaptureFixture[str]) -> dict:
    """Parse the JSON emitted by run_doctor from captured stdout."""
    out = capsys.readouterr().out
    # The live CLI may print environment info in tests that patch stderr; stdout
    # should contain exactly one JSON object at the end.
    start = out.find("{")
    if start == -1:
        raise AssertionError(f"No JSON object found in stdout: {out!r}")
    return json.loads(out[start:])


def _mock_client(health: dict | None, stats: dict | None) -> MagicMock:
    """Build a mock HttpClient that returns the given health and stats.

    The returned ``get`` side-effect maps endpoints so the mock stays reusable
    across repeated doctor runs without exhausting a finite response list.
    """

    def _get(path: str, **_: Any) -> dict:
        if path == "/health":
            return health or {}
        if path == "/memory/stats":
            return stats or {}
        raise ValueError(f"Unexpected mock endpoint: {path}")

    return MagicMock(get=MagicMock(side_effect=_get), post=MagicMock(return_value={}))


def _make_hermes_home(tmp_path: Path) -> Path:
    """Create a fake Hermes home with one queue database and required tables."""
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    queue_db = hermes_home / "memora_queue_default.db"
    conn = sqlite3.connect(queue_db)
    conn.executescript(
        "CREATE TABLE queue (id INTEGER PRIMARY KEY);"
        "CREATE TABLE failed_queue (id INTEGER PRIMARY KEY);"
    )
    conn.close()
    return hermes_home


def _make_company_dir(tmp_path: Path) -> Path:
    """Create a fake company memory directory initialised as a git repo."""
    company_dir = tmp_path / "company-memory"
    company_dir.mkdir()
    (company_dir / ".git").mkdir()
    return company_dir


def test_run_doctor_reports_unconfigured(capsys: pytest.CaptureFixture[str]) -> None:
    with patch.dict("os.environ", {"RAG_WORKER_URL": "", "RAG_AUTH_TOKEN": ""}, clear=False):
        code = run_doctor(json_output=True)

    report = _capture_json(capsys)
    assert code == 1
    assert report["worker"]["ok"] is False
    assert "RAG_WORKER_URL not configured" in report["worker"]["error"]


def test_run_doctor_reports_healthy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    company_dir = _make_company_dir(tmp_path)
    _make_hermes_home(tmp_path)

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("RAG_WORKER_URL", "https://worker.test")
    monkeypatch.setenv("RAG_AUTH_TOKEN", "token")

    mock_health = {"status": "ok", "version": "1.2.0", "models": {"llm": "test"}}
    mock_stats = {"total": 100, "pending_vector_sync": 0}

    with patch("memora.doctor.resolve_company_memory_dir", return_value=company_dir):
        with patch("memora.doctor._repo_sync_lag_seconds", return_value=0):
            with patch(
                "memora.doctor.HttpClient", return_value=_mock_client(mock_health, mock_stats)
            ):
                code = run_doctor(json_output=True)

    report = _capture_json(capsys)
    assert code == 0
    assert report["worker"]["ok"] is True
    assert report["stats"]["ok"] is True
    assert report["queue"]["queued"] == 0
    assert report["queue"]["failed"] == 0
    assert report["repo_sync_lag_seconds"] == 0


def test_run_doctor_warns_on_pending_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    company_dir = _make_company_dir(tmp_path)

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
                return_value=_mock_client(mock_health, mock_stats),
            ):
                code = run_doctor(json_output=True)

    report = _capture_json(capsys)
    assert code == 1
    assert "pending_vector_sync" in report["stats"]["warning"]


def test_run_doctor_warns_on_queue_depth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    company_dir = _make_company_dir(tmp_path)
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    queue_db = hermes_home / "memora_queue_default.db"
    conn = sqlite3.connect(queue_db)
    conn.executescript(
        "CREATE TABLE queue (id INTEGER PRIMARY KEY);"
        "CREATE TABLE failed_queue (id INTEGER PRIMARY KEY);"
    )
    # Seed 150 queued rows to breach the 100-row threshold.
    conn.executemany("INSERT INTO queue (id) VALUES (?)", [(i,) for i in range(150)])
    conn.commit()
    conn.close()

    env = {
        "RAG_WORKER_URL": "https://worker.test",
        "RAG_AUTH_TOKEN": "token",
        "HOME": str(tmp_path),
    }

    mock_health = {"status": "ok", "version": "1.2.0", "models": {"llm": "test"}}
    mock_stats = {"total": 100, "pending_vector_sync": 0}

    with patch.dict("os.environ", env, clear=True):
        with patch("memora.doctor.resolve_company_memory_dir", return_value=company_dir):
            with patch(
                "memora.doctor.HttpClient",
                return_value=_mock_client(mock_health, mock_stats),
            ):
                code = run_doctor(json_output=True)

    report = _capture_json(capsys)
    assert code == 1
    assert report["queue"]["queued"] == 150
    assert "queue depth" in report["queue"]["warning"]


def test_run_doctor_warns_on_failed_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    company_dir = _make_company_dir(tmp_path)
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    queue_db = hermes_home / "memora_queue_default.db"
    conn = sqlite3.connect(queue_db)
    conn.executescript(
        "CREATE TABLE queue (id INTEGER PRIMARY KEY);"
        "CREATE TABLE failed_queue (id INTEGER PRIMARY KEY);"
    )
    # Seed 25 failed rows to breach the 10-row threshold.
    conn.executemany("INSERT INTO failed_queue (id) VALUES (?)", [(i,) for i in range(25)])
    conn.commit()
    conn.close()

    env = {
        "RAG_WORKER_URL": "https://worker.test",
        "RAG_AUTH_TOKEN": "token",
        "HOME": str(tmp_path),
    }

    mock_health = {"status": "ok", "version": "1.2.0", "models": {"llm": "test"}}
    mock_stats = {"total": 100, "pending_vector_sync": 0}

    with patch.dict("os.environ", env, clear=True):
        with patch("memora.doctor.resolve_company_memory_dir", return_value=company_dir):
            with patch(
                "memora.doctor.HttpClient",
                return_value=_mock_client(mock_health, mock_stats),
            ):
                code = run_doctor(json_output=True)

    report = _capture_json(capsys)
    assert code == 1
    assert report["queue"]["failed"] == 25
    assert "failed queue" in report["queue"]["warning"]


def test_repo_sync_lag_reported_for_git_repo(tmp_path: Path) -> None:
    company_dir = _make_company_dir(tmp_path)
    # Create a fake git commit so _repo_sync_lag_seconds returns a non-negative value.
    subprocess_run = pytest.importorskip("subprocess").run
    subprocess_run(["git", "init", "-q"], cwd=company_dir, check=True)
    subprocess_run(
        [
            "git",
            "-c",
            "user.email=test@test.com",
            "-c",
            "user.name=Test",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "init",
        ],
        cwd=company_dir,
        check=True,
    )

    lag = _repo_sync_lag_seconds(company_dir)
    assert lag is not None
    assert lag >= 0


def test_repo_sync_lag_warning_triggers_unhealthy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    company_dir = _make_company_dir(tmp_path)
    env = {
        "RAG_WORKER_URL": "https://worker.test",
        "RAG_AUTH_TOKEN": "token",
        "HOME": str(tmp_path),
    }

    mock_health = {"status": "ok", "version": "1.2.0", "models": {"llm": "test"}}
    mock_stats = {"total": 100, "pending_vector_sync": 0}

    with patch.dict("os.environ", env, clear=True):
        with patch("memora.doctor.resolve_company_memory_dir", return_value=company_dir):
            with patch(
                "memora.doctor.HttpClient",
                return_value=_mock_client(mock_health, mock_stats),
            ):
                with patch("memora.doctor._repo_sync_lag_seconds", return_value=20000):
                    code = run_doctor(json_output=True)

    report = _capture_json(capsys)
    assert code == 1
    assert report["repo_sync_lag_seconds"] == 20000
    assert "repo_sync_warning" in report
    assert "hours ago" in report["repo_sync_warning"]


def test_sqlite_connection_closed_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_load_queue_counts closes the SQLite connection after a successful read."""
    hermes_home = _make_hermes_home(tmp_path)

    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=None)
    mock_conn.execute.return_value.fetchone.side_effect = [(42,), (7,)]

    with patch("memora.doctor.sqlite3.connect", return_value=mock_conn) as mock_connect:
        counts = _load_queue_counts(hermes_home)

    mock_connect.assert_called_once()
    mock_conn.close.assert_called_once()
    assert counts == {"queued": 42, "failed": 7}


def test_sqlite_connection_closed_on_missing_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_load_queue_counts closes the connection even when the schema is unexpected."""
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    queue_db = hermes_home / "memora_queue_default.db"
    # Create a database with only one of the two expected tables.
    conn = sqlite3.connect(queue_db)
    conn.execute("CREATE TABLE queue (id INTEGER PRIMARY KEY)")
    conn.close()

    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=None)
    # First query succeeds, second raises as if the table were missing.
    mock_conn.execute.return_value.fetchone.side_effect = [
        (0,),
        sqlite3.OperationalError("no such table: failed_queue"),
    ]

    with patch("memora.doctor.sqlite3.connect", return_value=mock_conn) as mock_connect:
        counts = _load_queue_counts(hermes_home)

    mock_connect.assert_called_once()
    mock_conn.close.assert_called_once()
    # The malformed DB is skipped, so totals come only from the successfully-queried rows.
    assert counts == {"queued": 0, "failed": 0}


def test_repeated_doctor_runs_do_not_leak_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SQLite connections should be closed so many runs succeed without side effects."""
    company_dir = _make_company_dir(tmp_path)
    _make_hermes_home(tmp_path)

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("RAG_WORKER_URL", "https://worker.test")
    monkeypatch.setenv("RAG_AUTH_TOKEN", "token")

    mock_health = {"status": "ok", "version": "1.2.0", "models": {"llm": "test"}}
    mock_stats = {"total": 100, "pending_vector_sync": 0}

    with patch("memora.doctor.resolve_company_memory_dir", return_value=company_dir):
        with patch("memora.doctor._repo_sync_lag_seconds", return_value=0):
            with patch(
                "memora.doctor.HttpClient", return_value=_mock_client(mock_health, mock_stats)
            ):
                for _ in range(10):
                    code = run_doctor(json_output=True)
                    assert code == 0
