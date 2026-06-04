"""Organization topology graph builder for the Memora digital-twin network.

Reads member declarations from a ``members/`` directory and renders a
hierarchical ASCII tree grouped by role.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def load_members(members_dir: str | Path) -> list[dict[str, Any]]:
    """Load every ``*.json`` file from *members_dir* as a member record.

    Args:
        members_dir: Directory containing ``{role}-{name}.json`` files.

    Returns:
        List of parsed member dicts.
    """
    directory = Path(members_dir)
    members: list[dict[str, Any]] = []

    if not directory.exists():
        logger.warning("Members directory not found: %s", directory)
        return members

    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            members.append(data)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Skipping invalid member file %s: %s", path.name, exc)

    return members


def build_org_graph(members: list[dict[str, Any]]) -> str:
    """Build an ASCII tree of the digital-twin network grouped by role.

    Args:
        members: List of member dicts (must contain at least ``role`` and
            ``first_name`` keys).

    Returns:
        A multi-line string suitable for console or markdown rendering.
    """
    header = "Memora Digital Twins Network"
    separator = "=" * len(header)
    lines = [header, separator, ""]

    if not members:
        lines.append("(No members registered yet)")
        return "\n".join(lines)

    # Group by role
    roles: dict[str, list[str]] = {}
    for member in members:
        role = member.get("role", "Unknown")
        name = member.get("first_name", "Unknown")
        roles.setdefault(role, []).append(name)

    # Sort roles alphabetically for stable output
    for role in sorted(roles.keys()):
        names = sorted(roles[role])
        lines.append(role)
        # Render members as a tree branch
        for idx, name in enumerate(names):
            is_last = idx == len(names) - 1
            prefix = "└── " if is_last else "├── "
            lines.append(f"{prefix}{name}")

    return "\n".join(lines)
