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


@pytest.fixture
def real_git_repo(tmp_path: Path) -> tuple[Path, Path]:
    """Return a local repo and its bare origin remote for real git tests."""
    origin = tmp_path / "origin.git"
    repo = tmp_path / "repo"
    origin.mkdir()
    repo.mkdir()

    subprocess.run(["git", "init", "--bare", str(origin)], check=True)
    subprocess.run(["git", "init", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test User"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", str(origin)],
        check=True,
    )

    readme = repo / "README.md"
    readme.write_text("# init\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True)
    subprocess.run(["git", "-C", str(repo), "branch", "-m", "main"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "push", "-u", "origin", "main"],
        check=True,
    )

    return repo, origin


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


def test_generate_company_pr_multifile_e2e(
    real_git_repo: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multi-file PR helper must write, commit, push all files and call gh pr create."""
    repo, origin = real_git_repo
    gh_calls: list[list[str]] = []
    original_run = subprocess.run

    def _passthrough_run(
        cmd: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        if cmd and cmd[:3] == ["gh", "pr", "create"]:
            gh_calls.append(list(cmd))
            return subprocess.CompletedProcess(
                cmd, returncode=0, stdout="", stderr=""
            )
        return original_run(cmd, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        "memora.github_sync.subprocess.run", _passthrough_run
    )

    generate_company_pr(
        title="Add company facts",
        files={
            "company/fact_a.md": "Fact A content",
            "company/fact_b.md": "Fact B content",
        },
        repo_path=repo,
    )

    # Files are written where requested.
    assert (repo / "company" / "fact_a.md").read_text(encoding="utf-8") == "Fact A content"
    assert (repo / "company" / "fact_b.md").read_text(encoding="utf-8") == "Fact B content"

    # Both files are in the latest commit.
    show = subprocess.run(
        ["git", "-C", str(repo), "show", "--stat", "--oneline", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "company/fact_a.md" in show.stdout
    assert "company/fact_b.md" in show.stdout

    # A memora/* branch was pushed to the bare origin.
    remote_branches = subprocess.run(
        ["git", "-C", str(origin), "for-each-ref", "--format=%(refname:short)", "refs/heads/"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert any(
        branch.startswith("memora/") for branch in remote_branches.stdout.splitlines()
    )

    # gh pr create was called with the expected arguments.
    assert len(gh_calls) == 1
    pr_cmd = gh_calls[0]
    assert "--title" in pr_cmd
    title_index = pr_cmd.index("--title")
    assert pr_cmd[title_index + 1] == "Add company facts"
    assert "--base" in pr_cmd
    base_index = pr_cmd.index("--base")
    assert pr_cmd[base_index + 1] == "main"
    assert "company/fact_a.md" in " ".join(pr_cmd)
    assert "company/fact_b.md" in " ".join(pr_cmd)


def test_generate_company_pr_checks_out_base_branch(
    real_git_repo: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """generate_company_pr must create the feature branch from base_branch, not the current branch."""
    repo, origin = real_git_repo

    # Create a develop branch ahead of main and check it out.
    subprocess.run(["git", "-C", str(repo), "checkout", "-b", "develop"], check=True)
    develop_file = repo / "develop.txt"
    develop_file.write_text("develop work", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "develop.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "develop commit"], check=True)

    main_head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "main"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    develop_head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "develop"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert main_head != develop_head

    # Intercept gh pr create and the final git push (we only want local assertions).
    original_run = subprocess.run
    gh_calls: list[list[str]] = []

    def _passthrough_run(
        cmd: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        if cmd and cmd[:3] == ["gh", "pr", "create"]:
            gh_calls.append(list(cmd))
            return subprocess.CompletedProcess(
                cmd, returncode=0, stdout="", stderr=""
            )
        return original_run(cmd, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        "memora.github_sync.subprocess.run", _passthrough_run
    )

    generate_company_pr(
        title="Base branch test",
        filename="fact.md",
        content="content",
        repo_path=repo,
        base_branch="main",
    )

    # Find the newly created memora branch and verify it branched from main.
    branches = subprocess.run(
        ["git", "-C", str(repo), "branch", "--list", "memora/*"],
        check=True,
        capture_output=True,
        text=True,
    )
    memora_branch = branches.stdout.strip().lstrip("* ").strip()
    assert memora_branch.startswith("memora/")

    feature_parent = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", f"{memora_branch}^"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    # With the bug, the feature branch is created from develop; after the fix, from main.
    assert feature_parent == main_head
    assert feature_parent != develop_head

    # The PR should still target main.
    assert len(gh_calls) == 1
    pr_cmd = gh_calls[0]
    base_index = pr_cmd.index("--base")
    assert pr_cmd[base_index + 1] == "main"
