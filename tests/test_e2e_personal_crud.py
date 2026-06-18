"""E2E test for personal-scoped fact CRUD against the live RAG worker.

Requires:
  RAG_WORKER_URL
  RAG_AUTH_TOKEN

This test uses owner_id=TestCrud and ids prefixed with e2e-test-personal-crud-.
It leaves no test data behind because the final cleanup deletes all facts
matching that prefix for the TestCrud owner.

Run with:
  python -m pytest tests/test_e2e_personal_crud.py -v
"""

from __future__ import annotations

import os
import random
import string
import time
import uuid
from collections.abc import Iterator

import pytest

from memora.http_client import HttpClient, HttpConfig


pytestmark = pytest.mark.e2e


TEST_OWNER = "TestCrud"
TEST_PREFIX = "e2e-test-personal-crud-"


@pytest.fixture
def client() -> HttpClient:
    base_url = os.environ.get("RAG_WORKER_URL", "").rstrip("/")
    token = os.environ.get("RAG_AUTH_TOKEN", "")
    if not base_url or not token:
        raise pytest.skip("RAG_WORKER_URL or RAG_AUTH_TOKEN not configured")
    return HttpClient(HttpConfig(base_url=base_url, token=token))


@pytest.fixture
def fact_id() -> str:
    return f"{TEST_PREFIX}{uuid.uuid4().hex[:12]}"


def _unique_content() -> str:
    return (
        f"E2E personal CRUD test fact created at {time.time():.6f} "
        f"with nonce {''.join(random.choices(string.ascii_letters, k=8))}."
    )


def _cleanup(client: HttpClient) -> list[str]:
    """Delete every test fact for TEST_OWNER that has ids starting with TEST_PREFIX.

    Returns the list of deleted ids.
    """
    remaining = _list_test_facts(client)
    ids_to_delete = [f["id"] for f in remaining if f["id"].startswith(TEST_PREFIX)]
    if ids_to_delete:
        resp = client.post("/memory/delete", {"ids": ids_to_delete})
        assert resp.get("success") is True, f"Cleanup delete failed: {resp}"
    return ids_to_delete


def _list_test_facts(client: HttpClient) -> list[dict]:
    resp = client.post(
        "/memory/list",
        {"owner_id": TEST_OWNER, "scope": "personal", "limit": 500},
    )
    assert "facts" in resp, f"Unexpected list response: {resp}"
    return resp["facts"]


class TestPersonalCrud:
    @pytest.mark.xfail(
        reason="Live RAG worker lacks deployed owner_id/scope metadata filter fix in rag-worker/src/routes/search.js",
        strict=False,
    )
    def test_add_search_update_delete(self, client: HttpClient, fact_id: str) -> None:
        original_content = _unique_content()
        updated_content = f"UPDATED: {original_content}"

        # Cleanup any stale facts before starting (defensive).
        _cleanup(client)

        # 1. ADD
        add_resp = client.post(
            "/memory/add",
            {
                "id": fact_id,
                "content": original_content,
                "category": "e2e_test",
                "owner_id": TEST_OWNER,
                "scope": "personal",
                "importance_score": 0.75,
            },
        )
        assert add_resp.get("success") is True, f"Add failed: {add_resp}"
        assert add_resp.get("id") == fact_id

        # 2. SEARCH (semantic)
        # Vector sync is async; poll briefly for the embedding to be searchable.
        search_resp: dict = {}
        for _ in range(10):
            search_resp = client.post(
                "/search",
                {
                    "query": original_content,
                    "top_k": 5,
                    "owner_id": TEST_OWNER,
                    "scope": "personal",
                    "use_reranking": False,
                },
            )
            ids = [r["id"] for r in search_resp.get("results", [])]
            if fact_id in ids:
                break
            time.sleep(1)
        assert fact_id in [r["id"] for r in search_resp.get("results", [])], (
            f"Search did not return added fact: {search_resp}"
        )

        # 3. UPDATE
        update_resp = client.post(
            "/memory/update",
            {"id": fact_id, "content": updated_content, "category": "e2e_test_updated"},
        )
        assert update_resp.get("success") is True, f"Update failed: {update_resp}"

        # 4. LIST and verify updated fact
        list_facts = _list_test_facts(client)
        matching = [f for f in list_facts if f["id"] == fact_id]
        assert len(matching) == 1, f"Expected exactly one fact in list, got {matching}"
        assert matching[0]["content"] == updated_content
        assert matching[0]["category"] == "e2e_test_updated"
        assert matching[0]["owner_id"] == TEST_OWNER
        assert matching[0]["scope"] == "personal"

        # 5. DELETE
        delete_resp = client.post("/memory/delete", {"ids": [fact_id]})
        assert delete_resp.get("success") is True, f"Delete failed: {delete_resp}"

        # 6. CONFIRM DELETE via list
        list_facts_after = _list_test_facts(client)
        assert not any(f["id"] == fact_id for f in list_facts_after)

    def test_cleanup_is_exhaustive(self, client: HttpClient, fact_id: str) -> None:
        """Ensure the cleanup helper removes every e2e-test fact it touches."""
        client.post(
            "/memory/add",
            {
                "id": fact_id,
                "content": _unique_content(),
                "category": "e2e_cleanup",
                "owner_id": TEST_OWNER,
                "scope": "personal",
            },
        )

        deleted_ids = _cleanup(client)
        assert fact_id in deleted_ids

        list_facts = _list_test_facts(client)
        assert not any(f["id"].startswith(TEST_PREFIX) for f in list_facts)


@pytest.fixture(autouse=True)
def cleanup_after_test(client: HttpClient) -> Iterator[None]:
    """Always remove test facts after a test runs."""
    yield
    _cleanup(client)
