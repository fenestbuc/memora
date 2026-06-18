"""Standalone E2E test for the /think synthesis endpoint.

Seeds a small set of facts, asks a synthesis question, verifies citations and
honest gap reporting, then asks a nonsense question and cleans up.

Run with live worker credentials in the environment:

    export RAG_WORKER_URL=https://your-worker.example.workers.dev
    export RAG_AUTH_TOKEN=your-token
    pytest tests/test_memora_think_synthesis_e2e.py -v
"""

from __future__ import annotations

import os
import time
from collections.abc import Sequence
from typing import Any

import pytest

from memora.http_client import HttpClient, HttpConfig
from memora.tool_dispatcher import dispatch

E2E_PREFIX = "e2e-test-think"
OWNER_ID = "e2e-test-think-owner"
POLL_MAX_SECONDS = 180
POLL_INTERVAL = 2
MIN_RELEVANT_SOURCES = 2


def _client() -> HttpClient:
    base_url = os.environ.get("RAG_WORKER_URL", "").strip()
    token = os.environ.get("RAG_AUTH_TOKEN", "").strip()
    if not base_url or not token:
        raise pytest.skip("RAG_WORKER_URL and RAG_AUTH_TOKEN must be set")
    return HttpClient(HttpConfig(base_url=base_url, token=token, timeout=60.0))


def _seed_facts() -> list[dict[str, Any]]:
    return [
        {
            "content": "Project Falcon is building an AI underwriting copilot for regional banks.",
            "category": "projects",
            "id": f"{E2E_PREFIX}-001",
            "scope": "personal",
        },
        {
            "content": "Project Falcon's pilot lender is SBI, and the integration is expected to go live in Q3 2026.",
            "category": "projects",
            "id": f"{E2E_PREFIX}-002",
            "scope": "personal",
        },
        {
            "content": "Project Falcon aims to reduce MSME loan turnaround time from 14 days to under 48 hours.",
            "category": "projects",
            "id": f"{E2E_PREFIX}-003",
            "scope": "personal",
        },
        # Fuzzy / potentially conflicting statement
        {
            "content": "Project Falcon may also explore a secondary partnership with PNB later in 2026, although this is not yet confirmed.",
            "category": "projects",
            "id": f"{E2E_PREFIX}-004",
            "scope": "personal",
        },
    ]


def _cleanup(client: HttpClient, ids: Sequence[str]) -> None:
    if not ids:
        return
    path, body = dispatch("memora_delete", {"ids": list(ids)}, owner_id=OWNER_ID)
    try:
        client.post(path, body)
    except Exception as exc:  # noqa: BLE001
        print(f"[cleanup warning] failed to delete {ids}: {exc}")


def _add_fact(client: HttpClient, fact: dict[str, Any]) -> None:
    path, body = dispatch("memora_add", fact, owner_id=OWNER_ID)
    result = client.post(path, body)
    if not result.get("success"):
        raise RuntimeError(f"Failed to add fact {fact['id']}: {result}")


def _ask_think(client: HttpClient, query: str) -> dict[str, Any]:
    path, body = dispatch("memora_think", {"query": query, "top_k": 5}, owner_id=OWNER_ID)
    return client.post(path, body)


def _ask_search(client: HttpClient, query: str) -> list[dict[str, Any]]:
    path, body = dispatch("memora_search", {"query": query, "top_k": 5}, owner_id=OWNER_ID)
    return client.post(path, body).get("results", [])


def _wait_for_facts(client: HttpClient, ids: Sequence[str]) -> None:
    """Poll /search until at least MIN_RELEVANT_SOURCES seeded facts appear."""
    deadline = time.time() + POLL_MAX_SECONDS
    while time.time() < deadline:
        results = _ask_search(client, "Project Falcon SBI turnaround lender")
        found = {r.get("id") for r in results if r.get("id", "").startswith(E2E_PREFIX)}
        if len(found) >= MIN_RELEVANT_SOURCES:
            return
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"Seeded facts did not become searchable within {POLL_MAX_SECONDS}s")


def _collect_leftover_ids(client: HttpClient) -> list[str]:
    try:
        list_path, list_body = dispatch(
            "memora_list",
            {"search": E2E_PREFIX, "limit": 50},
            owner_id=OWNER_ID,
        )
        leftover = client.post(list_path, list_body)
        return [f.get("id") for f in leftover.get("facts", []) if f.get("id", "").startswith(E2E_PREFIX)]
    except Exception as exc:  # noqa: BLE001
        print(f"[cleanup warning] list failed: {exc}")
        return []


@pytest.mark.e2e
def test_memora_think_synthesis_and_gaps() -> None:
    client = _client()
    facts = _seed_facts()
    ids = [f["id"] for f in facts]

    try:
        for fact in facts:
            _add_fact(client, fact)

        # Wait until vector search can see the seeded facts.
        _wait_for_facts(client, ids)

        # --- 1. Synthesis query ------------------------------------------------
        query = (
            "What is Project Falcon, who is the pilot lender, and what "
            "turnaround-time improvement does it target?"
        )
        think_resp = _ask_think(client, query)

        assert "answer" in think_resp, f"missing answer field: {think_resp}"
        answer = think_resp["answer"]
        assert answer and isinstance(answer, str), "answer must be a non-empty string"
        assert len(answer) > 20, "answer looks too short"

        sources = think_resp.get("sources", [])
        assert isinstance(sources, list), "sources must be a list"
        source_ids = {s.get("id") for s in sources}
        assert len(source_ids) >= MIN_RELEVANT_SOURCES, (
            f"expected at least {MIN_RELEVANT_SOURCES} sources, got {source_ids}"
        )

        # The seeded ids about the pilot lender and turnaround are the most
        # relevant to the question; they should appear in the cited sources.
        must_cite = {f"{E2E_PREFIX}-001", f"{E2E_PREFIX}-002", f"{E2E_PREFIX}-003"}
        assert source_ids & must_cite, (
            f"expected at least one of {must_cite} in sources, got {source_ids}"
        )

        # A gaps section is required, either as structured gaps or inside the
        # answer text (the worker is asked to emit "## Gaps").
        structured_gaps = think_resp.get("gaps")
        assert structured_gaps is not None, "gaps field is missing from response"
        assert isinstance(structured_gaps, list), "gaps must be a list"
        assert len(structured_gaps) > 0 or "## gaps" in answer.lower(), (
            "response must include a gaps section either in answer or as structured gaps"
        )

        # --- 2. Nonsense query with no relevant facts -------------------------
        nonsense_resp = _ask_think(client, "xyzqwerty12345 nonsense query")
        assert "answer" in nonsense_resp
        assert not nonsense_resp.get("sources"), (
            "nonsense query should return no sources"
        )
        gaps = nonsense_resp.get("gaps", [])
        assert isinstance(gaps, list) and len(gaps) > 0, (
            "nonsense query must return a non-empty honest gaps list"
        )

    finally:
        _cleanup(client, ids)
        # Also list and delete any leftover prefixed facts as a safety net.
        leftover_ids = _collect_leftover_ids(client)
        if leftover_ids:
            _cleanup(client, leftover_ids)
