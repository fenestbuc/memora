#!/usr/bin/env python3
"""Wiki ingester — processes sessions into an LLM Wiki."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict

# Organizations and projects commonly discussed in your domain.
# Customize these to match your own startup, products, partners, etc.
_KNOWN_PROJECTS = set()
_KNOWN_ORGS = set()

# Common English phrases that should NOT become entity pages
_STOP_PHRASES = {
    "this is", "you can", "we must", "i want", "it is", "the first",
    "if you", "do not", "it was", "he is", "she is", "they are",
    "there is", "that is", "what is", "how to", "how do", "to do",
    "in order", "for example", "as well", "at least", "in fact",
    "of course", "in the", "on the", "at the", "to the", "for the",
    "with the", "from the", "by the", "and the", "is a", "are a",
    "is the", "are the", "it has", "we have", "i have", "you have",
    "they have", "he has", "she has", "will be", "should be",
    "would be", "could be", "may be", "might be", "must be",
    "needs to", "wants to", "tries to", "going to", "able to",
}


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def init_wiki(wiki_root: str) -> None:
    """Create wiki directory structure and base files."""
    root = Path(wiki_root)
    for sub in ("raw", "entities", "concepts", "projects", "decisions"):
        (root / sub).mkdir(parents=True, exist_ok=True)

    if not (root / "SCHEMA.md").exists():
        (root / "SCHEMA.md").write_text(_SCHEMA_TEMPLATE)
    if not (root / "index.md").exists():
        (root / "index.md").write_text(_INDEX_TEMPLATE)
    if not (root / "log.md").exists():
        (root / "log.md").write_text(_LOG_TEMPLATE)


def ingest_session(wiki_root: str, session_path: str) -> List[str]:
    """Read a session file and update/create wiki pages. Returns list of changed files."""
    root = Path(wiki_root)
    text = Path(session_path).read_text(errors="replace")
    session_id = Path(session_path).stem
    changed = []

    # Extract entities (capitalized multi-word phrases)
    entities = set(re.findall(r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+", text))
    # Normalize and filter: remove stop phrases, short strings, UI labels
    def _is_stop_phrase(entity: str) -> bool:
        words = entity.split()
        lo = entity.lower()
        if lo in _STOP_PHRASES:
            return True
        # Check first two words against stop phrases
        if len(words) >= 2 and f"{words[0]} {words[1]}".lower() in _STOP_PHRASES:
            return True
        return False

    entities = {e for e in entities if len(e) > 4 and not e.startswith(("User", "Assistant")) and not _is_stop_phrase(e)}

    # Extract known projects
    lower = text.lower()
    for proj in _KNOWN_PROJECTS:
        if proj in lower:
            entities.add(proj.title())

    # Create/update entity pages
    for entity in entities:
        slug = _slug(entity)
        page = root / "entities" / f"{slug}.md"
        is_new = not page.exists()
        _update_entity_page(page, entity, text, session_id)
        if is_new:
            changed.append(str(page.relative_to(root)))
            _add_to_index(root, "Entities", f"[[{slug}|{entity}]]", f"Mentioned in sessions")

    # Create/update project pages for known products
    for proj in _KNOWN_PROJECTS:
        if proj in lower:
            slug = _slug(proj)
            page = root / "projects" / f"{slug}.md"
            is_new = not page.exists()
            _update_project_page(page, proj, text, session_id)
            if is_new:
                changed.append(str(page.relative_to(root)))
                _add_to_index(root, "Projects", f"[[{slug}|{proj.title()}]]", f"Product/project")

    # Extract key facts (sentences with "is" or "are")
    facts = re.findall(r"[^.!?]*\b(?:is|are|was|were)\b[^.!?]*[.!?]", text, re.I)
    for fact in facts[:5]:
        fact = fact.strip()
        if len(fact) > 20:
            _append_log(root, f"Fact from {session_id}: {fact[:120]}")

    if changed:
        _append_log(root, f"Ingested {session_id}: {len(changed)} new pages")
    return changed


def _update_entity_page(page: Path, name: str, source_text: str, session_id: str) -> None:
    today = _today()
    if page.exists():
        content = page.read_text()
        # Update existing
        if "## Sources" not in content:
            content += f"\n\n## Sources\n- Session: {session_id}\n"
        else:
            if session_id not in content:
                content = content.replace("## Sources", f"## Sources\n- Session: {session_id}")
        # Update updated date in frontmatter
        content = re.sub(r"updated: \d{4}-\d{2}-\d{2}", f"updated: {today}", content)
    else:
        content = f"""---
title: {name}
created: {today}
updated: {today}
type: entity
tags: [person]
---

# {name}

Mentioned in conversations. Extracted from session transcript.

## Sources
- Session: {session_id}
"""
    page.write_text(content)


def _update_project_page(page: Path, name: str, source_text: str, session_id: str) -> None:
    today = _today()
    if page.exists():
        content = page.read_text()
        content = re.sub(r"updated: \d{4}-\d{2}-\d{2}", f"updated: {today}", content)
        if session_id not in content:
            content = content.replace("## Sources", f"## Sources\n- Session: {session_id}")
    else:
        # Try to extract description sentence mentioning this project
        pattern = re.compile(rf"[^.!?]*{re.escape(name)}[^.!?]*[.!?]", re.I)
        desc = ""
        for m in pattern.finditer(source_text):
            sent = m.group(0).strip()
            if len(sent) > 10:
                desc = sent
                break

        content = f"""---
title: {name.title()}
created: {today}
updated: {today}
type: project
tags: [product]
---

# {name.title()}

{desc or "Project/product"}

## Sources
- Session: {session_id}
"""
    page.write_text(content)


def _add_to_index(root: Path, section: str, link: str, summary: str) -> None:
    idx = root / "index.md"
    if not idx.exists():
        return
    content = idx.read_text()
    section_header = f"## {section}"
    entry = f"- {link} — {summary}"

    if section_header not in content:
        content += f"\n\n{section_header}\n{entry}\n"
    elif entry not in content:
        # Insert after section header
        parts = content.split(section_header, 1)
        if len(parts) > 1:
            # Handle case where section header has no body (e.g. "## Entities" at end of file)
            remainder = parts[1]
            if remainder.startswith("\n"):
                remainder = remainder[1:]  # strip leading newline after header
            content = parts[0] + section_header + "\n" + entry + "\n" + remainder
        else:
            content += entry + "\n"

    # Update header count
    content = re.sub(r"Total pages: \d+", f"Total pages: {_count_pages(root)}", content)
    content = re.sub(r"Last updated: \d{4}-\d{2}-\d{2}", f"Last updated: {_today()}", content)
    idx.write_text(content)


def _append_log(root: Path, line: str) -> None:
    log = root / "log.md"
    if not log.exists():
        return
    today = _today()
    entry = f"## [{today}] {line}\n"
    content = log.read_text()
    if today not in content[:200]:
        entry = f"\n## {today}\n\n{entry}"
    log.write_text(content + entry)


def _count_pages(root: Path) -> int:
    count = 0
    for sub in ("entities", "concepts", "projects", "decisions"):
        count += len(list((root / sub).glob("*.md")))
    return count


def lint_wiki(wiki_root: str) -> List[Dict[str, str]]:
    """Scan wiki pages for broken [[wikilinks]]. Returns list of broken link dicts."""
    root = Path(wiki_root)
    broken = []

    # Build set of existing page filenames (stem -> path)
    existing = set()
    for sub in ("entities", "concepts", "projects", "decisions"):
        for page in (root / sub).glob("*.md"):
            existing.add(page.stem)

    # Scan all pages for wikilinks
    wikilink_re = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
    for sub in ("entities", "concepts", "projects", "decisions"):
        for page in (root / sub).glob("*.md"):
            content = page.read_text(errors="replace")
            for match in wikilink_re.finditer(content):
                target = match.group(1).strip()
                target_slug = _slug(target)
                if target_slug not in existing:
                    broken.append({
                        "source": str(page.relative_to(root)),
                        "target": target,
                        "target_slug": target_slug,
                    })
    return broken


_SCHEMA_TEMPLATE = """# Wiki Schema

## Domain
Your startup / company knowledge base.

## Conventions
- File names: lowercase, hyphens, no spaces
- Every page has YAML frontmatter with title, created, updated, type, tags
- Use [[wikilinks]] for cross-references
- Update index.md and log.md on every change

## Frontmatter
```yaml
---
title: Page Title
created: YYYY-MM-DD
updated: YYYY-MM-DD
type: entity | concept | project | decision
tags: [from taxonomy]
---
```

## Tag Taxonomy
- person, company, investor, product, project, partnership
- decision, milestone, regulatory, competitive, technology, metric

## Update Policy
- New info: append, update frontmatter date
- Pivots: archive old decision, create new, link old -> new
- Contradictions: note both positions with dates, flag for review
"""

_INDEX_TEMPLATE = """# Wiki Index

> Content catalog for your knowledge base.
> Last updated: 2026-01-01 | Total pages: 0

## Entities

## Projects

## Concepts

## Decisions
"""

_LOG_TEMPLATE = """# Wiki Log

> Chronological record of wiki actions. Append-only.
> Format: `## [YYYY-MM-DD] action | details`

## [2026-01-01] create | Wiki initialized
- Domain: your startup / company
"""


if __name__ == "__main__":
    import sys
    wiki = sys.argv[1] if len(sys.argv) > 1 else str(Path.home() / "memora-workspace" / "wiki")
    init_wiki(wiki)
    print(f"Wiki initialized at {wiki}")
