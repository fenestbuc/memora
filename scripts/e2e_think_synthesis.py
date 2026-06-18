#!/usr/bin/env python3
"""End-to-end test for the /think synthesis endpoint.

Seeds 3-4 facts (with ids prefixed e2e-test-think-), asks a synthesis
question that requires combining them, and verifies that the response
contains an answer, cited sources, and a gaps section.  It then asks a
nonsense query with no relevant facts and verifies the gaps section is
honest.  Seeded facts are deleted in a finally block.

Requires environment variables:
    RAG_WORKER_URL      Live RAG worker base URL
    RAG_AUTH_TOKEN      Bearer token for the worker
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

# Make src/memora importable when running this script directly.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from memora.http_client import HttpClient, HttpConfig
from memora.tool_dispatcher import dispatch

WORKER_URL = os.environ.get("RAG_WORKER_URL", "").rstrip("/")
AUTH_TOKEN = os.environ.get("RAG_AUTH_TOKEN", "")
RUN_ID = uuid.uuid4().hex[:8]
E2E_PREFIX = "e2e-test-think"
OWNER_ID = f"{E2E_PREFIX}-owner-{RUN_ID}"
POLL_MAX_SECONDS = 180
POLL_INTERVAL = 2
MIN_RELEVANT_SOURCES = 2
TIMEOUT = 60.0


def _client() -> HttpClient:
    if not WORKER_URL or not AUTH_TOKEN:
        raise RuntimeError("RAG_WORKER_URL and RAG_AUTH_TOKEN must be set")
    return HttpClient(HttpConfig(base_url=WORKER_URL, token=AUTH_TOKEN, timeout=TIMEOUT))


def _seed_facts() -> list[dict[str, Any]]:
    return [
        {
            "id": f"{E2E_PREFIX}-{RUN_ID}-001",
            "content": "Project Falcon is building an AI underwriting copilot for regional banks.",
            "category": "projects",
            "scope": "personal",
        },
        {
            "id": f"{E2E_PREFIX}-{RUN_ID}-002",
            "content": "Project Falcon's pilot lender is SBI, and the integration is expected to go live in Q3 2026.",
            "category": "projects",
            "scope": "personal",
        },
        {
            "id": f"{E2E_PREFIX}-{RUN_ID}-003",
            "content": "Project Falcon aims to reduce MSME loan turnaround time from 14 days to under 48 hours.",
            "category": "projects",
            "scope": "personal",
        },
        {
            "id": f"{E2E_PREFIX}-{RUN_ID}-004",
            "content": "Project Falcon may also explore a secondary partnership with PNB later in 2026, although this is not yet confirmed.",
            "category": "projects",
            "scope": "personal",
        },
    ]


def _cleanup(client: HttpClient, ids: list[str]) -> None:
    if not ids:
        return
    path, body = dispatch("memora_delete", {"ids": ids}, owner_id=OWNER_ID)
    try:
        client.post(path, body)
    except Exception as exc:
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


def _wait_for_facts(client: HttpClient, ids: list[str]) -> None:
    print(f"Polling /search until at least {MIN_RELEVANT_SOURCES} seeded facts appear...")
    deadline = time.time() + POLL_MAX_SECONDS
    while time.time() < deadline:
        results = _ask_search(client, "Project Falcon SBI turnaround lender")
        found = {r.get("id") for r in results if r.get("id", "").startswith(f"{E2E_PREFIX}-{RUN_ID}")}
        if len(found) >= MIN_RELEVANT_SOURCES:
            print(f"  OK: {len(found)} seeded facts searchable")
            return
        print(f"  ... found {len(found)}/{len(ids)}, retrying in {POLL_INTERVAL}s")
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"Seeded facts did not become searchable within {POLL_MAX_SECONDS}s")


def _collect_leftover_ids(client: HttpClient) -> list[str]:
    try:
        list_path, list_body = dispatch(
            "memora_list",
            {"search": f"{E2E_PREFIX}-{RUN_ID}", "limit": 50},
            owner_id=OWNER_ID,
        )
        leftover = client.post(list_path, list_body)
        return [f.get("id") for f in leftover.get("facts", []) if f.get("id", "").startswith(E2E_PREFIX)]
    except Exception as exc:
        print(f"[cleanup warning] list failed: {exc}")
        return []


def _check_synthesis(client: HttpClient, ids: list[str]) -> None:
    print("Step 1: synthesis query")
    query = (
        "What is Project Falcon, who is the pilot lender, and what "
        "turnaround-time improvement does it target?"
    )
    resp = _ask_think(client, query)

    answer = resp.get("answer")
    if not answer or not isinstance(answer, str) or len(answer) <= 20:
        raise AssertionError(f"answer missing or too short: {answer!r}")
    print(f"  OK: answer returned ({len(answer)} chars)")

    sources = resp.get("sources", [])
    if not isinstance(sources, list):
        raise AssertionError("sources must be a list")
    source_ids = {s.get("id") for s in sources}
    if len(source_ids) < MIN_RELEVANT_SOURCES:
        raise AssertionError(f"expected at least {MIN_RELEVANT_SOURCES} sources, got {source_ids}")

    must_cite = {ids[0], ids[1], ids[2]}
    if not source_ids & must_cite:
        raise AssertionError(f"expected at least one of {must_cite} in sources, got {source_ids}")
    print(f"  OK: sources include relevant seeded ids ({source_ids & set(ids)})")

    gaps = resp.get("gaps")
    if gaps is None or not isinstance(gaps, list):
        raise AssertionError("gaps field missing or not a list")
    if len(gaps) == 0 and "## gaps" not in answer.lower():
        raise AssertionError("response must include a gaps section in answer or structured gaps")
    print(f"  OK: gaps present ({len(gaps)} structured, ## Gaps in answer: {'## gaps' in answer.lower()})")


def _check_gaps_for_nonsense(client: HttpClient) -> None:
    print("Step 2: nonsense query")
    resp = _ask_think(client, "xyzqwerty12345 nonsense query")
    if resp.get("sources"):
        raise AssertionError("nonsense query should return no sources")

    gaps = resp.get("gaps", [])
    if not isinstance(gaps, list) or len(gaps) == 0:
        raise AssertionError(f"nonsense query must return a non-empty honest gaps list, got {gaps}")
    print(f"  OK: honest gaps returned for nonsense query: {gaps}")


def main() -> int:
    client = _client()
    facts = _seed_facts()
    ids = [f["id"] for f in facts]

    try:
        print(f"Seeding {len(facts)} facts with owner={OWNER_ID}")
        for fact in facts:
            _add_fact(client, fact)

        _wait_for_facts(client, ids)
        _check_synthesis(client, ids)
        _check_gaps_for_nonsense(client)

    except Exception as exc:
        print(f"FAILED: {exc}")
        _cleanup(client, ids)
        _cleanup(client, _collect_leftover_ids(client))
        return 1

    finally:
        print("Cleaning up seeded facts")
        _cleanup(client, ids)
        leftover_ids = _collect_leftover_ids(client)
        if leftover_ids:
            _cleanup(client, leftover_ids)

    print("\nAll /think E2E checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
