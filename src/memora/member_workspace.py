"""Workspace scaffolding for a new Memora digital twin.

This module is intentionally small and dependency-free so that both the
onboarding flow and ``install.sh`` can import it without pulling in the
full provider stack.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any


def _slug(value: str) -> str:
    """Return a lowercased, filesystem-safe hyphen slug."""
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def build_member_files(profile: dict[str, Any]) -> dict[str, str]:
    """Return a mapping of relative repo paths to initial content for a member.

    The layout mirrors GBrain's per-person folder convention:
    ``members/<role>-<name>/`` contains ``USER.md``, ``concepts/``,
    ``customers/``, ``meetings/``, and ``sources/``. A flat JSON metadata
    file is kept at ``members/<role>-<name>.json`` for backward compatibility
    with existing org-graph tooling.
    """
    first_name = profile.get("first_name", "Unknown")
    role = profile.get("role", "Unknown")
    repo = profile.get("company_github_repo", "")
    email = profile.get("email")
    email_line = f"\n- **Email:** {email}" if email else ""

    base = f"members/{_slug(role)}-{_slug(first_name)}"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    user_md = f"""# {first_name} — {role}

*Member profile created on {today}.*

## Role and focus

- **Role:** {role}{email_line}
- **Top priorities:** (update as you go)
- **Preferred answer style:** (terse / detailed / bullet-rich)

## Current active work

- (Add customers, projects, or initiatives here.)

## Concepts I own

See [`concepts/`](./concepts/).

## Useful sources

See [`sources/`](./sources/).

## Company repo

{repo}
"""

    concepts_readme = """# Concepts

Frameworks, definitions, and recurring themes specific to this member.

- (Add your first concept here.)
"""

    customers_readme = """# Customers

Customer pages owned by this member.

- (Add your first customer note here.)
"""

    meetings_readme = """# Meetings

Meeting notes owned or attended by this member.

- (Add your first meeting note here.)
"""

    sources_readme = """# Sources

Links to dashboards, docs, inboxes, and other external sources this member
checks regularly.

- (Add your first source link here.)
"""

    return {
        f"{base}.json": json.dumps(profile, indent=2),
        f"{base}/USER.md": user_md,
        f"{base}/concepts/README.md": concepts_readme,
        f"{base}/customers/README.md": customers_readme,
        f"{base}/meetings/README.md": meetings_readme,
        f"{base}/sources/README.md": sources_readme,
    }
