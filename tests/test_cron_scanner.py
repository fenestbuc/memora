"""Tests for the cron scanner."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from memora.cron_scanner import cron_matches, due_jobs, iter_cron_jobs, run_cron_job


def test_cron_matches_exact_time() -> None:
    dt = datetime(2026, 6, 18, 9, 0, 0, tzinfo=timezone.utc)
    assert cron_matches(dt, "0 9 * * *") is True
    assert cron_matches(dt, "30 9 * * *") is False


def test_cron_matches_weekday() -> None:
    # 2026-06-18 is a Thursday (Python weekday 3)
    dt = datetime(2026, 6, 18, 9, 0, 0, tzinfo=timezone.utc)
    assert cron_matches(dt, "0 9 * * 4") is True
    assert cron_matches(dt, "0 9 * * 1") is False


def test_iter_cron_jobs(tmp_path: Path) -> None:
    crons_dir = tmp_path / "crons" / "sales-alice"
    crons_dir.mkdir(parents=True)
    (crons_dir / "digest.md").write_text(
        "---\nschedule: \"0 17 * * *\"\nowner: alice\nprompt: summarize\n---\n", encoding="utf-8"
    )

    jobs = list(iter_cron_jobs(tmp_path))
    assert len(jobs) == 1
    assert jobs[0].owner == "alice"
    assert jobs[0].schedule == "0 17 * * *"
    assert jobs[0].prompt == "summarize"


def test_due_jobs_filters_by_schedule(tmp_path: Path) -> None:
    crons_dir = tmp_path / "crons" / "sales-alice"
    crons_dir.mkdir(parents=True)
    (crons_dir / "digest.md").write_text(
        "---\nschedule: \"0 9 * * *\"\nowner: alice\nprompt: morning\n---\n", encoding="utf-8"
    )

    dt = datetime(2026, 6, 18, 9, 0, 0, tzinfo=timezone.utc)
    assert len(due_jobs(tmp_path, dt)) == 1

    dt = datetime(2026, 6, 18, 10, 0, 0, tzinfo=timezone.utc)
    assert len(due_jobs(tmp_path, dt)) == 0


def test_run_cron_job_calls_runner() -> None:
    jobs = list(iter_cron_jobs(None))
    assert len(jobs) == 0

    # Construct a job manually
    from memora.cron_scanner import CronJob
    job = CronJob(path=Path("/tmp/digest.md"), owner="bob", schedule="0 9 * * *", prompt="hello")

    calls: list[dict] = []
    def runner(payload: dict):
        calls.append(payload)
        return {"answer": "ok"}

    run_cron_job(job, runner)
    assert calls[0]["query"] == "hello"
    assert calls[0]["owner_id"] == "bob"
    assert calls[0]["scope"] == "company"
