"""Build a navigable Markdown wiki from exported Memora JSONL facts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _display_source(fact: dict[str, Any]) -> str:
    source = fact.get("source_file") or fact.get("source_session") or "Memora"
    source = str(source).strip()
    if source.startswith("/"):
        source = Path(source).name
    return source or "Memora"


def _fact_date(fact: dict[str, Any]) -> str:
    value = fact.get("updated_at") or fact.get("created_at") or ""
    return str(value)[:10] or "Undated"


def build_wiki(repo_dir: str) -> dict[str, int]:
    """Render category pages, a wiki index, and the repository README."""
    repo_path = Path(repo_dir)
    facts_dir = repo_path / "facts"
    wiki_dir = repo_path / "wiki"
    wiki_dir.mkdir(exist_ok=True, parents=True)

    if not facts_dir.exists():
        raise FileNotFoundError(f"Facts directory {facts_dir} not found")

    facts_by_category: dict[str, list[dict[str, Any]]] = {}
    for jsonl_file in sorted(facts_dir.glob("*.jsonl")):
        rows: list[dict[str, Any]] = []
        for line_number, line in enumerate(jsonl_file.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                fact = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL in {jsonl_file}:{line_number}: {exc}") from exc
            if str(fact.get("content") or "").strip():
                rows.append(fact)
        if rows:
            rows.sort(
                key=lambda fact: str(fact.get("updated_at") or fact.get("created_at") or ""),
                reverse=True,
            )
            facts_by_category[jsonl_file.stem] = rows

    expected_pages = {wiki_dir / f"{category}.md" for category in facts_by_category}
    expected_pages.add(wiki_dir / "index.md")
    for stale in wiki_dir.glob("*.md"):
        if stale not in expected_pages:
            stale.unlink()

    index_lines = [
        "# Company Brain Index",
        "",
        "A human-readable view of durable Kubar Labs knowledge synchronized from Memora.",
        "",
        "## Domains",
        "",
    ]

    total = 0
    for category, facts in facts_by_category.items():
        title = category.replace("_", " ").replace("-", " ").title()
        page = wiki_dir / f"{category}.md"
        lines = [
            f"# {title}",
            "",
            f"{len(facts)} durable facts, newest first.",
            "",
        ]
        for fact in facts:
            fact_id = str(fact.get("id") or "unknown")
            lines.extend(
                [
                    f"## {_fact_date(fact)}",
                    "",
                    str(fact.get("content") or "").strip(),
                    "",
                    f"Source: {_display_source(fact)}  ",
                    f"Fact ID: `{fact_id}`",
                    "",
                ]
            )
        page.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        index_lines.append(f"- [{title}]({category}.md), {len(facts)} facts")
        total += len(facts)

    index_lines.extend(
        [
            "",
            "## Data policy",
            "",
            "- This repository contains durable company knowledge, not raw conversations.",
            "- Explicitly personal facts, test data, and agent transcripts are excluded.",
            "- JSONL in `facts/` is the machine-readable source. Markdown in `wiki/` is generated.",
        ]
    )
    (wiki_dir / "index.md").write_text("\n".join(index_lines).rstrip() + "\n", encoding="utf-8")

    readme_lines = [
        "# Kubar Labs Company Brain",
        "",
        f"{total} durable facts across {len(facts_by_category)} domains, synchronized from Memora.",
        "",
        "Start with the [Company Brain Index](wiki/index.md).",
        "",
        "## Structure",
        "",
        "- `facts/`: machine-readable JSONL grouped by knowledge domain.",
        "- `wiki/`: generated Markdown pages for human review and navigation.",
        "- Root rule files, when present, define filing and output policy for every Memora-powered agent.",
        "",
        "## Sync",
        "",
        "Run `memora-sync /home/yash/hermes-workspace/company-memory` from an authenticated Memora environment.",
        "The sync filters private and low-signal records, deduplicates content, rebuilds the wiki, commits, and pushes.",
    ]
    (repo_path / "README.md").write_text("\n".join(readme_lines).rstrip() + "\n", encoding="utf-8")

    print(f"Wiki generated successfully in {wiki_dir}")
    return {"categories": len(facts_by_category), "facts": total}


if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else str(Path.home() / "hermes-workspace" / "company-memory")
    build_wiki(target)
