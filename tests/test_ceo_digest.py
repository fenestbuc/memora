"""Tests for the CEO digest generator.

Run with: pytest tests/test_ceo_digest.py -v
"""

from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock, patch

import pytest

from memora.ceo_digest import (
    _classify_pr_risk,
    _fetch_open_prs,
    send_digest,
)


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


def test_send_digest_no_auto_merge(caplog: pytest.LogCaptureFixture) -> None:
    """No PR should ever trigger gh pr merge --auto --merge."""
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
        mock_run.return_value = MagicMock(stdout=json.dumps(prs))
        with caplog.at_level(logging.INFO):
            send_digest()

        # Only one subprocess call (fetch PRs), no merge calls
        assert mock_run.call_count == 1
        assert "awaiting approval" in caplog.text
        assert "auto-merge" not in caplog.text.lower()


def test_send_digest_classifies_optimizer_as_high_risk(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Optimizer PRs should be flagged as high risk."""
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
        mock_run.return_value = MagicMock(stdout=json.dumps(prs))
        with caplog.at_level(logging.INFO):
            send_digest()

        assert "HIGH RISK" in caplog.text
        assert "#7 Optimizer tweak" in caplog.text


def test_send_digest_mixed_prs(caplog: pytest.LogCaptureFixture) -> None:
    """Multiple PRs should appear in the digest with correct risk levels."""
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
        {
            "number": 3,
            "title": "Add member GTM-Sreyan",
            "author": {"login": "onboarding"},
            "url": "http://example.com/3",
            "headRefName": "memora/member-gtm-sreyan",
        },
    ]
    with patch("memora.ceo_digest.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=json.dumps(prs))
        with caplog.at_level(logging.INFO):
            send_digest()

        assert "Total PRs awaiting approval: 3" in caplog.text
        assert "#1 Feedback" in caplog.text
        assert "#2 Big feature" in caplog.text
        assert "#3 Add member GTM-Sreyan" in caplog.text
        assert "No PRs are merged automatically" in caplog.text


def test_classify_pr_risk_branch_prefixes() -> None:
    """Risk classification should reflect branch naming conventions."""
    assert _classify_pr_risk("memora/member-ceo-vaibhav", "Add CEO") == "low"
    assert _classify_pr_risk("docs/readme", "Update docs") == "low"
    assert _classify_pr_risk("memora-feedback-fix", "Feedback") == "medium"
    assert _classify_pr_risk("memora-optimizer-prompt", "Optimize") == "high"
    assert _classify_pr_risk("feature-big", "Big feature") == "medium"


def test_classify_pr_risk_title_keywords() -> None:
    """Title keywords should influence risk classification."""
    assert _classify_pr_risk("feature-x", "Refactor prompt routing") == "high"
    assert _classify_pr_risk("feature-x", "Update eval dataset") == "high"
