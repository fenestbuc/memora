"""Git sync and PR generation engine for company-scope facts.

Uses ``subprocess.run`` to execute git commands and the GitHub CLI (``gh``)
to create pull requests.
"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path
from typing import Union


def generate_company_pr(
    title: str,
    filename: str,
    content: str,
    repo_path: Union[str, Path, None] = None,
    base_branch: str = "main",
) -> None:
    """Commit a company-scope fact to a new branch and open a GitHub PR.

    Args:
        title: Title for the PR (and the git commit message).
        filename: Name of the markdown file to write / update (relative to repo root).
        content: Markdown content to write into *filename*.
        repo_path: Absolute path to the git repository. Defaults to the
            current working directory.
        base_branch: Branch to check out from. Defaults to ``main``.
    """
    repo = Path(repo_path) if repo_path else Path.cwd()
    branch_name = f"memora/{uuid.uuid4().hex[:8]}"
    file_path = repo / filename

    # 1. Ensure parent directories exist and write the markdown file.
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")

    # 2. Create and check out a new branch.
    subprocess.run(
        ["git", "checkout", "-b", branch_name],
        cwd=repo,
        check=True,
    )

    # 3. Stage the file.
    subprocess.run(
        ["git", "add", str(file_path.relative_to(repo))],
        cwd=repo,
        check=True,
    )

    # 4. Commit.
    subprocess.run(
        ["git", "commit", "-m", title],
        cwd=repo,
        check=True,
    )

    # 5. Push the branch to the remote.
    subprocess.run(
        ["git", "push", "-u", "origin", branch_name],
        cwd=repo,
        check=True,
    )

    # 6. Open the PR via gh CLI.
    subprocess.run(
        [
            "gh",
            "pr",
            "create",
            "--title",
            title,
            "--body",
            f"Auto-generated PR for company fact: {filename}",
            "--base",
            base_branch,
        ],
        cwd=repo,
        check=True,
    )
