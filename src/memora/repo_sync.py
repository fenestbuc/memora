"""Synchronize the Memora worker into a reviewable company-brain repository."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from .http_client import HttpClient, HttpConfig
from .wiki_builder import build_wiki

EXCLUDED_CATEGORIES = {
    "memory",
    "user",
    "preferences",
    "test",
    "e2e",
    "e2e_test",
    "eval_golden",
    "hermes",
    "hermes-quirks",
    "hermes-technical",
    "project-conventions",
    "security",
    "skill",
    "tooling",
    "workflow",
}

_PRIVATE_PREFIXES = (
    "User:",
    "Assistant:",
    "Delegated task:",
    "Delegation result:",
)

_SECRET_PATTERNS = [
    re.compile(
        r"\b(?:api[_ -]?key|api[_ -]?token|auth[_ -]?token|access[_ -]?token|client[_ -]?secret|password|api_hash)\s*[:=]",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:ntn_|secret_|lin_api_|cfut_|gh[pousr]_|github_pat_|sk-)[A-Za-z0-9_-]{8,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
]


def _run_git(repo_dir: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_dir,
        check=check,
        capture_output=True,
        text=True,
    )


def _normalize_content(content: str) -> str:
    return re.sub(r"\s+", " ", content).strip()


def _is_exportable_fact(fact: dict[str, Any]) -> bool:
    category = str(fact.get("category") or "memory").strip().lower()
    content = _normalize_content(str(fact.get("content") or ""))
    scope = fact.get("scope")

    if category in EXCLUDED_CATEGORIES or len(content) < 20:
        return False
    if content.startswith(_PRIVATE_PREFIXES):
        return False
    if any(pattern.search(content) for pattern in _SECRET_PATTERNS):
        return False
    # Legacy ownerless facts are the original company corpus. Explicitly
    # personal facts are private and must never be pushed to the shared repo.
    if scope == "personal":
        return False
    return True


def _collect_export_facts(
    fetch_page: Callable[[], dict[str, Any]],
    *,
    max_pages: int = 100,
) -> dict[str, list[dict[str, Any]]]:
    """Collect, filter, and deduplicate pages returned by the worker."""
    by_key: dict[tuple[str, str], dict[str, Any]] = {}

    for _ in range(max_pages):
        page = fetch_page()
        facts = page.get("facts", [])
        if not facts:
            break
        for fact in facts:
            if not _is_exportable_fact(fact):
                continue
            category = str(fact.get("category") or "memory").strip().lower()
            normalized = _normalize_content(str(fact.get("content") or ""))
            key = (category, normalized.casefold())
            current = by_key.get(key)
            if current is None or str(fact.get("updated_at") or fact.get("created_at") or "") >= str(
                current.get("updated_at") or current.get("created_at") or ""
            ):
                item = dict(fact)
                item["content"] = normalized
                by_key[key] = item

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in by_key.values():
        grouped[str(item.get("category") or "memory").strip().lower()].append(item)
    for items in grouped.values():
        items.sort(key=lambda fact: (
            str(fact.get("updated_at") or fact.get("created_at") or ""),
            str(fact.get("id") or ""),
        ), reverse=True)
    return dict(sorted(grouped.items()))


def _fetch_company_facts(client: HttpClient) -> dict[str, list[dict[str, Any]]]:
    limit = 500
    offset = 0

    def fetch_page() -> dict[str, Any]:
        nonlocal offset
        result = client.post("/memory/list", {"limit": limit, "offset": offset})
        offset += limit
        return result

    return _collect_export_facts(fetch_page)


def _write_jsonl(repo_path: Path, facts_by_category: dict[str, list[dict[str, Any]]]) -> int:
    facts_dir = repo_path / "facts"
    facts_dir.mkdir(exist_ok=True, parents=True)

    expected: set[Path] = set()
    total = 0
    for category, items in facts_by_category.items():
        path = facts_dir / f"{category}.jsonl"
        expected.add(path)
        lines = []
        for item in items:
            export_item = {
                "id": item.get("id"),
                "content": item.get("content"),
                "category": category,
                "source_session": item.get("source_session"),
                "source_file": item.get("source_file"),
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
                "scope": item.get("scope") or "company-legacy",
            }
            lines.append(json.dumps(export_item, ensure_ascii=False, sort_keys=True))
        path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        total += len(items)

    for stale in facts_dir.glob("*.jsonl"):
        if stale not in expected:
            stale.unlink()
    return total


def sync_repo(repo_dir: str, *, push: bool = True) -> dict[str, Any]:
    repo_path = Path(repo_dir).expanduser().resolve()
    if not (repo_path / ".git").exists():
        raise FileNotFoundError(f"Not a Git repository: {repo_path}")

    print(f"Starting Memora Company Repo Sync into {repo_path}...")
    print("Pulling latest changes from remote...")
    _run_git(repo_path, "pull", "--rebase", "origin", "main")

    base_url = os.environ.get("RAG_WORKER_URL", "").rstrip("/")
    token = os.environ.get("RAG_AUTH_TOKEN", "")
    if not base_url or not token:
        raise RuntimeError("RAG_WORKER_URL and RAG_AUTH_TOKEN must be configured")

    print("Fetching exportable company facts from RAG worker...")
    client = HttpClient(HttpConfig(base_url=base_url, token=token, timeout=60.0))
    facts_by_category = _fetch_company_facts(client)
    total = _write_jsonl(repo_path, facts_by_category)
    print(f"Wrote {total} filtered, deduplicated facts across {len(facts_by_category)} categories.")

    print("Building comprehensive Markdown Wiki...")
    build_wiki(str(repo_path))

    _run_git(repo_path, "add", "facts", "wiki", "README.md")
    status = _run_git(repo_path, "status", "--porcelain").stdout.strip()
    committed = False
    pushed = False
    if status:
        _run_git(repo_path, "commit", "-m", "chore(memora): refresh comprehensive company brain")
        committed = True
        if push:
            _run_git(repo_path, "push", "origin", "main")
            pushed = True
            print("Successfully pushed comprehensive wiki to company memory repo.")
    else:
        print("No new changes to commit.")

    return {
        "facts": total,
        "categories": len(facts_by_category),
        "committed": committed,
        "pushed": pushed,
    }


def main() -> None:
    repo = sys.argv[1] if len(sys.argv) > 1 else str(Path.home() / "hermes-workspace" / "company-memory")
    sync_repo(repo)


if __name__ == "__main__":
    main()
