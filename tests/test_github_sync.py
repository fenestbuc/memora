"""Tests for the GitHub sync engine (Phase 2, Task 2).

Run with: pytest tests/test_github_sync.py -v
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from memora.github_sync import generate_company_pr


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    """Return a temporary directory acting as a fake repo root."""
    return tmp_path


def test_generate_company_pr_calls_subprocess_run(tmp_repo: Path) -> None:
    """generate_company_pr must run git checkout, add, commit, push and gh pr create."""
    with patch("memora.github_sync.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock()

        generate_company_pr(
            title="Update GTM strategy",
            filename="gtm_strategy.md",
            content="New strategy context",
            repo_path=tmp_repo,
        )

        # Verify at least 4 subprocess calls: checkout, add, commit, push, pr create (>=4)
        assert mock_run.call_count >= 4, (
            f"Expected at least 4 subprocess calls, got {mock_run.call_count}"
        )

        # Gather called commands (first element of args list)
        called_commands = [call.args[0] for call in mock_run.call_args_list]

        assert any(cmd[0:2] == ["git", "checkout"] for cmd in called_commands)
        assert any(cmd[0:2] == ["git", "add"] for cmd in called_commands)
        assert any(cmd[0:2] == ["git", "commit"] for cmd in called_commands)
        assert any(cmd[0:2] == ["git", "push"] for cmd in called_commands)
        assert any(cmd[0:3] == ["gh", "pr", "create"] for cmd in called_commands)


def test_generate_company_pr_writes_file(tmp_repo: Path) -> None:
    """generate_company_pr must write the given markdown content to the repo."""
    with patch("memora.github_sync.subprocess.run"):
        generate_company_pr(
            title="Update GTM strategy",
            filename="gtm_strategy.md",
            content="New strategy context",
            repo_path=tmp_repo,
        )

        expected_file = tmp_repo / "gtm_strategy.md"
        assert expected_file.exists()
        assert expected_file.read_text(encoding="utf-8") == "New strategy context"


def test_generate_company_pr_uses_custom_base_branch(tmp_repo: Path) -> None:
    """generate_company_pr must pass the custom base branch to gh pr create."""
    with patch("memora.github_sync.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock()

        generate_company_pr(
            title="Feature X",
            filename="feat_x.md",
            content="content",
            repo_path=tmp_repo,
            base_branch="develop",
        )

        called_commands = [call.args[0] for call in mock_run.call_args_list]
        pr_create_cmd = next(
            cmd for cmd in called_commands if cmd[0:3] == ["gh", "pr", "create"]
        )
        assert "--base" in pr_create_cmd
        base_index = pr_create_cmd.index("--base")
        assert pr_create_cmd[base_index + 1] == "develop"
