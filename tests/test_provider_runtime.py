"""Runtime-contract tests for the active Memora provider."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from memora.http_client import HttpClient
from memora.provider import MemoraProvider
from memora.queue import FactQueue


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