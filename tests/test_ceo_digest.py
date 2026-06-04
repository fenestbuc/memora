"""Tests for the CEO digest generator (Task 2 of Memora Resilience plan).

Run with: pytest tests/test_ceo_digest.py -v
"""

from __future__ import annotations

import json
import logging
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from memora.ceo_digest import _fetch_open_prs, send_digest


def test_fetch_open_prs_includes_head_ref_name() -> None:
    """_fetch_open_prs must request headRefName so we can filter by branch prefix."""
    with patch("memora.ceo_digest.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            stdout='[{"number":1,"headRefName":"main"}]'
        )
        result = _fetch_open_prs()
        assert result == [{"number": 1, "headRefName": "main"}]
        args = mock_run.call_args[0][0]
        json_fields = next(arg for arg in args if arg.startswith("number,"))
        assert "headRefName" in json_fields


def test_send_digest_no_prs_logs_message(caplog: pytest.LogCaptureFixture) -> None:
    """When no open PRs exist, send_digest logs the no-PR message."""
    with patch("memora.ceo_digest.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="[]")
        with caplog.at_level(logging.INFO):
            send_digest()
        assert "No open PRs awaiting approval" in caplog.text


def test_send_digest_auto_merges_feedback_branch(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A PR whose branch starts with memora-feedback- triggers gh pr merge --auto --merge."""
    prs = [
        {
            "number": 42,
            "title": "Feedback update",
            "author": {"login": "bot"},
            "url": "http://example.com/42",
            "headRefName": "memora-feedback-2024-01-01",
        }
    ]
    with patch("memora.ceo_digest.subprocess.run") as mock_run:
        mock_run.side_effect = [
            MagicMock(stdout=json.dumps(prs)),  # _fetch_open_prs
            MagicMock(),  # _auto_merge_pr successful
        ]
        with caplog.at_level(logging.INFO):
            send_digest()

        merge_call = mock_run.call_args_list[1]
        assert merge_call.args[0] == ["gh", "pr", "merge", "42", "--auto", "--merge"]
        assert "auto-merged" in caplog.text
        assert "awaiting approval" not in caplog.text


def test_send_digest_auto_merges_optimizer_branch(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A PR whose branch starts with memora-optimizer- triggers gh pr merge --auto --merge."""
    prs = [
        {
            "number": 7,
            "title": "Optimizer tweak",
            "author": {"login": "optimizer"},
            "url": "http://example.com/7",
            "headRefName": "memora-optimizer-weights",
        }
    ]
    with patch("memora.ceo_digest.subprocess.run") as mock_run:
        mock_run.side_effect = [
            MagicMock(stdout=json.dumps(prs)),
            MagicMock(),
        ]
        with caplog.at_level(logging.INFO):
            send_digest()

        merge_call = mock_run.call_args_list[1]
        assert merge_call.args[0] == ["gh", "pr", "merge", "7", "--auto", "--merge"]
        assert "auto-merged" in caplog.text


def test_send_digest_mixed_prs(caplog: pytest.LogCaptureFixture) -> None:
    """One auto-merge PR and one normal PR both appear in the digest correctly."""
    prs = [
        {
            "number": 1,
            "title": "Feedback",
            "author": {"login": "bot"},
            "url": "http://example.com/1",
            "headRefName": "memora-feedback-fix",
        },
        {
            "number": 2,
            "title": "Big feature",
            "author": {"login": "alice"},
            "url": "http://example.com/2",
            "headRefName": "feature-big",
        },
    ]
    with patch("memora.ceo_digest.subprocess.run") as mock_run:
        mock_run.side_effect = [
            MagicMock(stdout=json.dumps(prs)),
            MagicMock(),  # merge PR #1
        ]
        with caplog.at_level(logging.INFO):
            send_digest()

        merge_call = mock_run.call_args_list[1]
        assert merge_call.args[0] == ["gh", "pr", "merge", "1", "--auto", "--merge"]

        assert "Auto-merged PRs" in caplog.text
        assert "auto-merged" in caplog.text
        assert "Open PRs awaiting approval" in caplog.text
        assert "#2 Big feature" in caplog.text


def test_send_digest_logs_merge_failure(caplog: pytest.LogCaptureFixture) -> None:
    """If gh pr merge fails, the digest notes the failure and logs a warning."""
    prs = [
        {
            "number": 99,
            "title": "Bad feedback",
            "author": {"login": "bot"},
            "url": "http://example.com/99",
            "headRefName": "memora-feedback-bad",
        }
    ]
    with patch("memora.ceo_digest.subprocess.run") as mock_run:
        mock_run.side_effect = [
            MagicMock(stdout=json.dumps(prs)),
            subprocess.CalledProcessError(1, "gh"),  # merge fails
        ]
        with caplog.at_level(logging.INFO):
            send_digest()

        assert "Auto-merge failed for PR #99" in caplog.text
        assert "auto-merge failed" in caplog.text
