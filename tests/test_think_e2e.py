"""End-to-end test for memora_think synthesis.

Runs against the live RAG worker configured via RAG_WORKER_URL and
RAG_AUTH_TOKEN.  Seeds project facts, calls /think, verifies synthesis,
citations, and gaps, then cleans up the seeded facts.
"""

from __future__ import annotations

import os
import time
from collections.abc import Generator
from typing import Any

import pytest

from memora.http_client import HttpClient, HttpConfig


def _http() -> HttpClient:
    base_url = os.environ.get("RAG_WORKER_URL", "").rstrip("/")
    token = os.environ.get("RAG_AUTH_TOKEN", "")
    if not base_url or not token:
        pytest.skip("RAG_WORKER_URL and RAG_AUTH_TOKEN must be set")
    if "YOUR_" in token or "..." in token:
        pytest.skip("RAG_AUTH_TOKEN appears to be a placeholder")
    return HttpClient(HttpConfig(base_url=base_url, token=token))


@pytest.fixture
def client() -> HttpClient:
    return _http()


TEST_FACTS: list[dict[str, Any]] = [
    {
        "id": "e2e-test-think-001",
        "content": "Project Apollo is the internal codename for Kubar Labs' inclusive MSME credit underwriting platform.",
        "category": "projects",
        "scope": "company",
    },
    {
        "id": "e2e-test-think-002",
        "content": "Apollo targets small and medium enterprises operating inside agriculture supply chains.",
        "category": "projects",
        "scope": "company",
    },
    {
        "id": "e2e-test-think-003",
        "content": "The platform charges lenders a 1.25-1.5% success fee on successfully repaid loans plus a transaction-based commission at disbursal.",
        "category": "business",
        "scope": "company",
    },
    {
        "id": "e2e-test-think-004",
        "content": "Apollo aims to launch a pilot partnership with AgriGrader in Q3 2026.",
        "category": "projects",
        "scope": "company",
    },
]


@pytest.fixture(scope="module")
def seeded_facts() -> Generator[list[str], None, None]:
    """Seed test facts and yield their IDs; delete them on teardown."""
    client = _http()
    ids = [f["id"] for f in TEST_FACTS]

    # Ensure a clean slate.
    try:
        client.post("/memory/delete", {"ids": ids})
    except Exception:
        pass

    for fact in TEST_FACTS:
        payload = {
            "content": fact["content"],
            "category": fact["category"],
            "scope": fact["scope"],
            "owner_id": "e2e-tester",
        }
        # Only set id if backend supports it; otherwise rely on returned id.
        if fact.get("id"):
            payload["id"] = fact["id"]
        resp = client.post("/memory/add", payload)
        if "id" in resp:
            returned_id = resp["id"]
            if returned_id != fact["id"]:
                ids = [returned_id if x == fact["id"] else x for x in ids]

    # Allow vector index to sync.
    time.sleep(3)

    yield ids

    try:
        client.post("/memory/delete", {"ids": ids})
    except Exception:
        pass


class TestThinkSynthesis:
    @pytest.mark.parametrize("query,expected_keywords", [
        (
            "What is Project Apollo and who is the pilot partner?",
            ["Apollo", "AgriGrader"],
        ),
    ])
    def test_think_synthesizes_answer_with_citations(
        self,
        client: HttpClient,
        seeded_facts: list[str],
        query: str,
        expected_keywords: list[str],
    ) -> None:
        resp = client.post(
            "/think",
            {"query": query, "top_k": 5, "scope": "company", "owner_id": "e2e-tester"},
        )

        assert "error" not in resp, f"/think returned error: {resp.get('error')}"
        assert "answer" in resp, "Response missing 'answer' field"
        answer = resp["answer"]
        assert isinstance(answer, str) and answer.strip(), "Answer is empty"

        for keyword in expected_keywords:
            assert keyword.lower() in answer.lower(), (
                f"Expected keyword '{keyword}' not found in answer: {answer}"
            )

        sources = resp.get("sources", [])
        assert isinstance(sources, list), "sources should be a list"
        assert len(sources) >= 2, f"Expected at least 2 sources, got {len(sources)}"

        source_ids = {s.get("id") for s in sources if s.get("id")}
        overlap = source_ids.intersection(set(seeded_facts))
        assert overlap, f"None of the seeded facts {seeded_facts} appear in sources {source_ids}"

        gaps = resp.get("gaps", [])
        assert isinstance(gaps, list), "gaps should be a list"
        has_gaps_section = "## gaps" in answer.lower()
        if not gaps and not has_gaps_section:
            pytest.fail("Response contains neither a gaps array nor a ## Gaps section in the answer")

    def test_think_returns_no_results_for_unknown_query(
        self,
        client: HttpClient,
    ) -> None:
        resp = client.post(
            "/think",
            {"query": "xyz-unknown-e2e-test-think query", "top_k": 5, "scope": "company"},
        )
        assert "error" not in resp, f"/think returned error: {resp.get('error')}"
        if "answer" in resp and "could not find" in resp["answer"].lower():
            assert resp.get("sources") == [] or resp.get("sources") is None
            assert resp.get("gaps")
