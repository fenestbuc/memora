"""CEO digest generator.

Summarizes open GitHub PRs for the CEO approval flow and alerts on
new members joining the digital-twin network.

**No auto-merge.** Every PR requires explicit CEO approval via the digest.
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


def _classify_pr_risk(branch: str, title: str) -> str:
    """Heuristic risk classification for a PR.

    Returns:
        One of ``low``, ``medium``, or ``high``.
    """
    lower_branch = branch.lower()
    lower_title = title.lower()

    # Low risk: member declarations, docs
    if lower_branch.startswith("memora/member-") or lower_branch.startswith("docs/"):
        return "low"

    # High risk: optimizer suggestions, core logic changes
    if lower_branch.startswith("memora-optimizer-") or lower_branch.startswith("memora-core-"):
        return "high"

    # Medium risk: feedback-driven prompt tweaks
    if lower_branch.startswith("memora-feedback-"):
        return "medium"

    # Anything else involving evals or prompts
    if "prompt" in lower_title or "eval" in lower_title or "routing" in lower_title:
        return "high"

    return "medium"


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

    Fetches open PRs via the GitHub CLI, classifies each by risk level,
    and logs a summary of **all** PRs awaiting CEO approval.

    **No PRs are merged automatically.** The CEO must review the digest
    and approve each change individually.
    """
    prs = _fetch_open_prs()

    if not prs:
        logger.info("CEO Digest: No open PRs awaiting approval.")
        return

    # Classify and group PRs
    low_risk: list[dict[str, Any]] = []
    medium_risk: list[dict[str, Any]] = []
    high_risk: list[dict[str, Any]] = []

    for pr in prs:
        branch = pr.get("headRefName", "")
        title = pr.get("title", "")
        risk = _classify_pr_risk(branch, title)
        if risk == "low":
            low_risk.append(pr)
        elif risk == "medium":
            medium_risk.append(pr)
        else:
            high_risk.append(pr)

    lines: list[str] = [
        "╔══════════════════════════════════════════════════════════════╗",
        "║              CEO Digest — Pending Approvals                  ║",
        "╠══════════════════════════════════════════════════════════════╣",
        "",
        f"  Total PRs awaiting approval: {len(prs)}",
        f"  • High risk : {len(high_risk)}",
        f"  • Medium risk: {len(medium_risk)}",
        f"  • Low risk  : {len(low_risk)}",
        "",
        "  No PRs are merged automatically. Review each below and run:",
        "    gh pr merge <number> --merge",
        "",
    ]

    def _render_section(header: str, prs_list: list[dict], emoji: str) -> None:
        if not prs_list:
            return
        lines.append(f"{emoji} {header}")
        lines.append("─" * (len(header) + 4))
        for pr in prs_list:
            author = pr.get("author", {}).get("login", "unknown")
            lines.append(
                f"  #{pr.get('number')} {pr.get('title')}\n"
                f"     by {author} — {pr.get('url')}"
            )
        lines.append("")

    _render_section("HIGH RISK — Core logic / optimizer / prompts", high_risk, "🔴")
    _render_section("MEDIUM RISK — Feedback-driven changes", medium_risk, "🟡")
    _render_section("LOW RISK — Member declarations / docs", low_risk, "🟢")

    lines.append(
        "╚══════════════════════════════════════════════════════════════╝"
    )

    digest = "\n".join(lines)
    logger.info("%s", digest)
