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
    filename: str | None = None,
    content: str | None = None,
    files: dict[str, str] | None = None,
    repo_path: Union[str, Path, None] = None,
    base_branch: str = "main",
) -> None:
    """Commit company-scope facts to a new branch and open a GitHub PR.

    Args:
        title: Title for the PR (and the git commit message).
        filename: Optional single file to write (backward compatibility).
        content: Content for the single file.
        files: Mapping of relative paths to file contents. When provided, it
            is used instead of ``filename`` / ``content``.
        repo_path: Absolute path to the git repository. Defaults to the
            current working directory.
        base_branch: Branch to check out from. Defaults to ``main``.
    """
    repo = Path(repo_path) if repo_path else Path.cwd()
    branch_name = f"memora/{uuid.uuid4().hex[:8]}"

    file_map = dict(files) if files else {}
    if filename is not None:
        file_map[filename] = content or ""

    if not file_map:
        raise ValueError("generate_company_pr requires either files or filename+content")

    written_paths: list[Path] = []
    for rel_path, file_content in file_map.items():
        file_path = repo / rel_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(file_content, encoding="utf-8")
        written_paths.append(file_path.relative_to(repo))

    # 1. Check out the base branch so the new branch is created from it.
    subprocess.run(
        ["git", "checkout", base_branch],
        cwd=repo,
        check=True,
    )

    # 2. Create and check out a new branch.
    subprocess.run(
        ["git", "checkout", "-b", branch_name],
        cwd=repo,
        check=True,
    )

    # 3. Stage all files.
    for rel_path in written_paths:
        subprocess.run(
            ["git", "add", str(rel_path)],
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
    body_paths = ", ".join(str(p) for p in written_paths)
    subprocess.run(
        [
            "gh",
            "pr",
            "create",
            "--title",
            title,
            "--body",
            f"Auto-generated PR for company brain updates:\n{body_paths}",
            "--base",
            base_branch,
        ],
        cwd=repo,
        check=True,
    )
