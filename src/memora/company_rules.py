"""Load shared company rule files into the agent system prompt.

These files live at the root of the company memory repository and act as
versioned policy that every Memora-powered agent reads before it files or
answers anything.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

COMPANY_RULE_FILES = [
    "_brain-filing-rules.md",
    "_output-rules.md",
    "_excluded-people.md",
    "_operating-rules.md",
]


def load_company_rules(company_dir: str | Path | None) -> str:
    """Return concatenated rule content from the company repo root.

    Missing files are silently skipped so onboarding stays lightweight.
    Comment lines are stripped to save prompt tokens.
    """
    if not company_dir:
        return ""

    directory = Path(company_dir).expanduser()
    if not directory.exists():
        return ""

    blocks: list[str] = []
    for name in COMPANY_RULE_FILES:
        path = directory / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        # Strip comment-only lines to keep the prompt lean
        cleaned = "\n".join(
            line for line in text.splitlines() if not line.strip().startswith("<!--")
        )
        blocks.append(f"## {name}\n\n{cleaned.strip()}")

    if not blocks:
        return ""

    return "\n\n".join(["# Company brain rules", ""] + blocks)


def resolve_company_memory_dir(config: dict[str, Any] | None) -> Path | None:
    """Resolve company memory directory from Hermes config or default path."""
    cfg = config or {}
    custom = cfg.get("custom") or {}
    if "company_memory_dir" in custom:
        return Path(custom["company_memory_dir"]).expanduser()

    default = Path.home() / "hermes-workspace" / "company-memory"
    if default.exists():
        return default

    return None
