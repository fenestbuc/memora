#!/usr/bin/env python3
"""End-to-end roundtrip test for the Memora RAG worker.

Exercises the live worker endpoints:
  1. POST /embed
  2. POST /ingest
  3. POST /search
  4. POST /rerank
  5. POST /chat
  6. POST /delete

Requires environment variables RAG_WORKER_URL and RAG_AUTH_TOKEN.
The test fact id is always prefixed with "e2e-test-ragroundtrip-" and is
deleted at the end, along with any other facts matching that prefix.
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

# Make src/memora importable when running this script directly.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from memora.http_client import HttpClient, HttpConfig

WORKER_URL = os.environ.get("RAG_WORKER_URL", "").rstrip("/")
AUTH_TOKEN = os.environ.get("RAG_AUTH_TOKEN", "")

FACT_ID = f"e2e-test-ragroundtrip-{uuid.uuid4().hex[:12]}"
# Structure the fact as a Q&A pair so the natural-language search query is
# embedded directly in the stored text. This makes vector search find the fact
# quickly despite Vectorize's indexing latency in a large corpus.
SEARCH_QUERY = "What embeddings does the NavDhan platform use for semantic search?"
FACT_CONTENT = (
    f"Question: {SEARCH_QUERY} "
    f"Answer: The NavDhan platform uses BGE-M3 embeddings served through Cloudflare Workers AI for semantic search. "
    f"(E2E roundtrip test fact, id {FACT_ID})"
)
CHAT_QUERY = "Which embedding model does the NavDhan platform use?"
KEYWORDS = ["BGE-M3", "NavDhan", "Cloudflare Workers AI"]
TIMEOUT = 60.0
POLL_MAX_SECONDS = 120
POLL_INTERVAL = 5


def _client() -> HttpClient:
    if not WORKER_URL or not AUTH_TOKEN:
        raise RuntimeError("RAG_WORKER_URL and RAG_AUTH_TOKEN must be set")
    return HttpClient(
        HttpConfig(base_url=WORKER_URL, token=AUTH_TOKEN, timeout=TIMEOUT)
    )


def _list_test_facts(client: HttpClient) -> list[str]:
    """Return ids of existing facts that match the e2e-test-ragroundtrip prefix."""
    resp = client.post("/memory/list", {"category": "e2e_test", "limit": 500})
    return [
        f["id"]
        for f in resp.get("facts", [])
        if isinstance(f.get("id"), str) and f["id"].startswith("e2e-test-ragroundtrip-")
    ]


def _delete_ids(client: HttpClient, ids: list[str]) -> None:
    """Best-effort delete of up to 200 ids per request."""
    if not ids:
        return
    for chunk in (ids[i : i + 200] for i in range(0, len(ids), 200)):
        try:
            client.post("/delete", {"ids": chunk})
        except Exception as e:
            print(f"  delete warning for {len(chunk)} ids: {e}")


def setup(client: HttpClient) -> None:
    """Remove stale test facts from earlier runs."""
    stale = _list_test_facts(client)
    if stale:
        print(f"Cleaning up {len(stale)} stale test fact(s)...")
        _delete_ids(client, stale)


def test_embed(client: HttpClient) -> None:
    print("Step 1: POST /embed")
    resp = client.post("/embed", {"text": ["E2E roundtrip sample text"]})
    embeddings = resp.get("embeddings")
    assert isinstance(embeddings, list) and len(embeddings) > 0, "embeddings missing"
    vector = embeddings[0]
    assert isinstance(vector, list) and len(vector) > 0, "embedding vector empty"
    assert all(isinstance(v, (int, float)) for v in vector), "embedding not numeric"
    print(f"  OK: received {len(embeddings)} embedding(s), dimension {len(vector)}")


def test_ingest(client: HttpClient) -> None:
    print(f"Step 2: POST /ingest with id={FACT_ID}")
    resp = client.post(
        "/ingest",
        {
            "documents": [
                {
                    "id": FACT_ID,
                    "text": FACT_CONTENT,
                    "metadata": {
                        "category": "e2e_test",
                        "scope": "company",
                        "owner_id": "e2e-tester",
                        "source": "scripts/e2e_rag_roundtrip.py",
                    },
                }
            ]
        },
    )
    assert resp.get("success") is True, f"ingest failed: {resp}"
    print(f"  OK: inserted={resp.get('inserted')}, vectorized={resp.get('vectorized')}")

    # Verify the D1 write succeeded before relying on vector search.
    deadline = time.time() + 30
    while time.time() < deadline:
        resp = client.post("/memory/list", {"category": "e2e_test", "limit": 100})
        facts = resp.get("facts", [])
        if any(f.get("id") == FACT_ID for f in facts):
            print("  OK: fact persisted to D1")
            return
        time.sleep(1)
    raise AssertionError("fact was not persisted to D1 within timeout")


def _wait_for_search_result(client: HttpClient) -> dict:
    print(f"Step 3a: poll /search until fact appears (max {POLL_MAX_SECONDS}s)")
    deadline = time.time() + POLL_MAX_SECONDS
    while time.time() < deadline:
        resp = client.post("/search", {"query": SEARCH_QUERY, "top_k": 5})
        results = resp.get("results", [])
        for result in results:
            if result.get("id") == FACT_ID:
                print(f"  OK: found fact in search after polling")
                return resp
        remaining = int(deadline - time.time())
        print(f"  ... not in search yet ({remaining}s left), retrying in {POLL_INTERVAL}s")
        time.sleep(POLL_INTERVAL)
    raise AssertionError("fact did not appear in search results within timeout")


def test_search(client: HttpClient) -> None:
    resp = _wait_for_search_result(client)
    results = resp.get("results", [])
    assert any(r.get("id") == FACT_ID for r in results), "fact id missing from search"
    fact_result = next(r for r in results if r.get("id") == FACT_ID)
    text = fact_result.get("text") or fact_result.get("content") or ""
    assert FACT_CONTENT in text, "fact content missing from search result"
    # /search runs reranking by default; the fact should be the top-ranked result.
    top = max(results, key=lambda r: r.get("rerank_score") or r.get("vector_score") or -1)
    assert top.get("id") == FACT_ID, "test fact is not the top reranked result"
    print(
        f"  OK: search returned fact as top result "
        f"(vector_score={fact_result.get('vector_score')}, rerank_score={fact_result.get('rerank_score')})"
    )


def test_rerank(client: HttpClient) -> None:
    print("Step 4: POST /rerank")
    documents = [
        "The quick brown fox jumps over the lazy dog.",
        "Python is a popular programming language for data science.",
        "The capital of France is Paris, known for the Eiffel Tower.",
        FACT_CONTENT,
    ]
    resp = client.post("/rerank", {"query": CHAT_QUERY, "documents": documents})
    results = resp.get("results", [])
    assert len(results) == len(documents), f"rerank returned wrong count: {len(results)}"
    assert all(isinstance(r.get("score"), (int, float)) for r in results), "rerank result missing numeric score"
    fact_index = documents.index(FACT_CONTENT)
    assert results[fact_index].get("score", -1) >= 0, "rerank score for fact is negative"
    print(f"  OK: rerank returned {len(results)} scored documents (fact score={results[fact_index].get('score')})")


def test_chat(client: HttpClient) -> None:
    print("Step 5: POST /chat")
    resp = client.post(
        "/chat",
        {
            "query": SEARCH_QUERY,
            "top_k": 50,
            "system": "Answer the question using the provided context. Keep it concise.",
            "rerank": False,
        },
    )
    answer = resp.get("answer", "")
    sources = resp.get("sources", [])
    assert answer, "chat answer empty"
    assert any(s.get("id") == FACT_ID for s in sources), "chat sources do not include test fact"
    answer_lower = answer.lower()
    if not any(kw.lower() in answer_lower for kw in KEYWORDS):
        raise AssertionError(f"chat answer does not reference any expected keyword: {answer}")
    print(f"  OK: chat answered ({len(answer)} chars) and cites fact")


def test_delete(client: HttpClient) -> None:
    print("Step 6: POST /delete")
    resp = client.post("/delete", {"ids": [FACT_ID]})
    assert resp.get("success") is True, f"delete failed: {resp}"
    print(f"  OK: deleted={resp.get('deleted')}")


def cleanup(client: HttpClient) -> None:
    try:
        stale = _list_test_facts(client)
        if stale:
            _delete_ids(client, stale)
            print(f"  cleanup: removed {len(stale)} test fact(s)")
    except Exception as e:
        print(f"  cleanup warning: {e}")


def main() -> int:
    client = _client()
    setup(client)
    try:
        test_embed(client)
        test_ingest(client)
        test_search(client)
        test_rerank(client)
        test_chat(client)
        test_delete(client)
    except Exception as e:
        print(f"FAILED: {e}")
        cleanup(client)
        return 1
    cleanup(client)
    print("\nAll E2E roundtrip checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
