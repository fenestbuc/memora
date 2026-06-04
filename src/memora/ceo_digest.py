"""CEO digest generator.

Summarizes open GitHub PRs for the CEO approval flow and alerts on
new members joining the digital-twin network.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from .org_graph import load_members

logger = logging.getLogger(__name__)


def _fetch_open_prs() -> list[dict[str, Any]]:
    """Return open PRs via ``gh pr list``."""
    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--json",
                "number,title,author,url,createdAt,headRefName",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        logger.warning("Could not fetch open PRs: %s", exc)
        return []


def _auto_merge_pr(pr_number: int) -> bool:
    """Enable auto-merge for a single PR via the GitHub CLI.

    Args:
        pr_number: The GitHub PR number to merge.

    Returns:
        True if the CLI reported success, False otherwise.
    """
    try:
        subprocess.run(
            ["gh", "pr", "merge", str(pr_number), "--auto", "--merge"],
            capture_output=True,
            text=True,
            check=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        logger.warning("Auto-merge failed for PR #%s: %s", pr_number, exc)
        return False


def get_new_members(members_dir: str | Path, state_path: str | Path) -> list[dict[str, Any]]:
    """Compare current members against the last known state and return new ones.

    Args:
        members_dir: Directory containing ``*.json`` member files.
        state_path: Path to a JSON file that tracks known member filenames.

    Returns:
        List of member dicts that have not been seen before.
    """
    directory = Path(members_dir)
    state_file = Path(state_path)

    current_files = sorted(
        p.name for p in directory.glob("*.json")
    ) if directory.exists() else []

    known_files: list[str] = []
    if state_file.exists():
        try:
            known_files = json.loads(state_file.read_text(encoding="utf-8"))
            if not isinstance(known_files, list):
                known_files = []
        except (json.JSONDecodeError, OSError):
            known_files = []

    new_files = set(current_files) - set(known_files)
    new_members: list[dict[str, Any]] = []
    for filename in sorted(new_files):
        try:
            data = json.loads((directory / filename).read_text(encoding="utf-8"))
            new_members.append(data)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read new member file %s: %s", filename, exc)

    return new_members


def save_member_state(members_dir: str | Path, state_path: str | Path) -> None:
    """Persist the current list of member filenames to *state_path*.

    Args:
        members_dir: Directory containing ``*.json`` member files.
        state_path: Path to a JSON file that tracks known member filenames.
    """
    directory = Path(members_dir)
    state_file = Path(state_path)

    current_files = sorted(
        p.name for p in directory.glob("*.json")
    ) if directory.exists() else []

    state_file.write_text(json.dumps(current_files, indent=2), encoding="utf-8")


def send_new_member_alert(members_dir: str | Path, state_path: str | Path) -> None:
    """Log a digest alert for any new members since the last check.

    After alerting, the member state is updated so the same members are
    not reported again on the next run.

    Args:
        members_dir: Directory containing ``*.json`` member files.
        state_path: Path to a JSON file that tracks known member filenames.
    """
    new_members = get_new_members(members_dir, state_path)
    if not new_members:
        return

    lines = ["CEO Alert — New Member(s) Joined:", ""]
    for member in new_members:
        role = member.get("role", "Unknown")
        name = member.get("first_name", "Unknown")
        lines.append(f"  • {name} ({role})")

    logger.info("%s", "\n".join(lines))
    save_member_state(members_dir, state_path)


def send_digest() -> None:
    """Generate and send the CEO digest.

    Fetches open PRs via the GitHub CLI, auto-merges safe branches
    (``memora-feedback-*`` and ``memora-optimizer-*``), and logs a
    summary of the remaining PRs awaiting approval.
    """
    prs = _fetch_open_prs()

    auto_merge_prefixes = ("memora-feedback-", "memora-optimizer-")
    auto_merged: list[tuple[dict[str, Any], bool]] = []
    pending: list[dict[str, Any]] = []

    for pr in prs:
        branch = pr.get("headRefName", "")
        if branch.startswith(auto_merge_prefixes):
            success = _auto_merge_pr(pr["number"])
            auto_merged.append((pr, success))
        else:
            pending.append(pr)

    lines: list[str] = []

    if auto_merged:
        lines.append("CEO Digest — Auto-merged PRs:")
        lines.append("")
        for pr, success in auto_merged:
            status = "auto-merged" if success else "auto-merge failed"
            author = pr.get("author", {}).get("login", "unknown")
            lines.append(
                f"  #{pr.get('number')} {pr.get('title')}\n"
                f"     by {author} — {pr.get('url')} — {status}"
            )

    if pending:
        if auto_merged:
            lines.append("")
        header = (
            "CEO Digest — Open PRs awaiting approval:"
            if not auto_merged
            else "Open PRs awaiting approval:"
        )
        lines.append(header)
        lines.append("")
        for pr in pending:
            author = pr.get("author", {}).get("login", "unknown")
            lines.append(
                f"  #{pr.get('number')} {pr.get('title')}\n"
                f"     by {author} — {pr.get('url')}"
            )

    if not lines:
        logger.info("CEO Digest: No open PRs awaiting approval.")
        return

    digest = "\n".join(lines)
    logger.info("%s", digest)
