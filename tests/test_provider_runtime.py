"""Runtime-contract tests for the active Memora provider."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from memora.http_client import HttpClient
from memora.provider import MemoraProvider
from memora.queue import FactQueue
from memora.repo_sync import _collect_export_facts
from memora.wiki_builder import build_wiki


class _HttpStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def post(self, path: str, body: dict) -> dict:
        self.calls.append((path, body))
        if len(self.calls) == 1:
            return {"results": []}
        return {"results": [{"id": "legacy-fact", "text": "Legacy ownerless fact"}]}


def test_search_retries_legacy_ownerless_facts(tmp_path: Path) -> None:
    provider = MemoraProvider()
    provider._owner_id = "Vaibhav"
    http = _HttpStub()
    provider._http = cast(HttpClient, http)
    provider._l1_cache.clear()

    result = json.loads(
        provider.handle_tool_call(
            "memora_search",
            {"query": "Vaibhav Sharma Kubar Labs founder", "top_k": 5},
        )
    )

    assert result["results"][0]["id"] == "legacy-fact"
    assert http.calls[0][1]["owner_id"] == "Vaibhav"
    assert "owner_id" not in http.calls[1][1]


def test_explicit_scope_does_not_fallback(tmp_path: Path) -> None:
    provider = MemoraProvider()
    provider._owner_id = "Vaibhav"
    http = _HttpStub()
    provider._http = cast(HttpClient, http)
    provider._l1_cache.clear()

    result = json.loads(
        provider.handle_tool_call(
            "memora_search",
            {"query": "company-only", "scope": "company"},
        )
    )

    assert result == {"results": []}
    assert len(http.calls) == 1


class _QueueStub:
    def __init__(self) -> None:
        self.added: list[tuple[str, str]] = []

    def add(self, category: str, content: str) -> bool:
        self.added.append((category, content))
        return True


def test_sync_turn_does_not_store_raw_transcript_snippets() -> None:
    provider = MemoraProvider()
    queue = _QueueStub()
    provider._queue = cast(FactQueue, queue)
    provider._auto_ingest = True

    provider.sync_turn(
        "User pasted a long operational transcript that should not be memory.",
        "Assistant emitted another long transcript that should not be memory.",
    )

    assert queue.added == []


def test_company_export_filters_private_and_low_signal_categories() -> None:
    pages = [
        {
            "facts": [
                {"id": "b1", "category": "business", "content": "Kubar charges lenders a success fee.", "scope": None},
                {"id": "m1", "category": "memory", "content": "Assistant: raw transcript", "scope": "personal"},
                {"id": "u1", "category": "user", "content": "Private profile detail", "scope": "personal"},
                {"id": "t1", "category": "test", "content": "Synthetic test fact", "scope": "company"},
            ]
        },
        {"facts": []},
    ]

    facts = _collect_export_facts(lambda: pages.pop(0), max_pages=3)

    assert list(facts) == ["business"]
    assert facts["business"][0]["id"] == "b1"


def test_company_export_deduplicates_normalized_content() -> None:
    pages = [
        {
            "facts": [
                {"id": "p1", "category": "projects", "content": "NavDhan is the credit layer.", "updated_at": "2026-01-01"},
                {"id": "p2", "category": "projects", "content": "  NavDhan is the credit layer.  ", "updated_at": "2026-02-01"},
            ]
        },
        {"facts": []},
    ]

    facts = _collect_export_facts(lambda: pages.pop(0), max_pages=3)

    assert len(facts["projects"]) == 1
    assert facts["projects"][0]["id"] == "p2"


def test_company_export_rejects_credential_bearing_content() -> None:
    pages = [
        {
            "facts": [
                {"id": "i1", "category": "integrations", "content": "Notion API token: ntn_live_secret_value", "scope": None},
                {"id": "i2", "category": "integrations", "content": "Notion integration is active and used for CMS sync.", "scope": None},
            ]
        },
        {"facts": []},
    ]

    facts = _collect_export_facts(lambda: pages.pop(0), max_pages=3)

    assert [fact["id"] for fact in facts["integrations"]] == ["i2"]


def test_wiki_builder_creates_index_and_sanitized_category_pages(tmp_path: Path) -> None:
    facts = tmp_path / "facts"
    facts.mkdir()
    (facts / "business.jsonl").write_text(
        json.dumps(
            {
                "id": "b1",
                "category": "business",
                "content": "Kubar charges lenders a success fee.",
                "source_file": "/home/yash/private/business.md",
                "updated_at": "2026-08-17 10:00:00",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    summary = build_wiki(str(tmp_path))

    assert summary == {"categories": 1, "facts": 1}
    assert (tmp_path / "wiki" / "index.md").exists()
    page = (tmp_path / "wiki" / "business.md").read_text()
    assert "/home/yash" not in page
    assert "business.md" in page