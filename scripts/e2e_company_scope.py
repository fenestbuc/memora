#!/usr/bin/env python3
"""End-to-end test for company-scoped memory.

Adds scoped facts to the live RAG worker and verifies that:
- scope=personal returns only personal facts for an owner
- scope=company returns only company facts
- explicit scope=personal behaves the same as the default
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from typing import Any


def _req(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    base = os.environ["RAG_WORKER_URL"].rstrip("/")
    token = os.environ["RAG_AUTH_TOKEN"]
    data = json.dumps(body).encode("utf-8") if body else None
    url = f"{base}{path}"
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "memora-e2e/0.5.1",
        },
        method=method,
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def add_fact(fact_id: str, content: str, owner_id: str, scope: str | None = None) -> None:
    body: dict[str, Any] = {
        "id": fact_id,
        "content": content,
        "category": "e2e_test",
        "owner_id": owner_id,
    }
    if scope is not None:
        body["scope"] = scope
    result = _req("POST", "/memory/add", body)
    if not result.get("success"):
        raise RuntimeError(f"Failed to add fact {fact_id}: {result}")
    print(f"[ADD] {fact_id} scope={scope or 'default'} vector_sync={result.get('vector_sync')}")


def sync_pending() -> None:
    result = _req("POST", "/memory/sync", {"batch_size": 50})
    print(f"[SYNC] synced={result.get('synced', 0)}")


def search_facts(query: str, owner_id: str, scope: str, max_wait: int = 30) -> list[dict[str, Any]]:
    deadline = time.time() + max_wait
    while True:
        result = _req(
            "POST",
            "/search",
            {"query": query, "owner_id": owner_id, "scope": scope, "top_k": 10, "rerank": False},
        )
        results = result.get("results", [])
        if results or time.time() >= deadline:
            return results
        print(f"[SEARCH retry] scope={scope} no results yet, waiting...")
        time.sleep(3)


def delete_facts(ids: list[str]) -> None:
    if not ids:
        return
    result = _req("POST", "/memory/delete", {"ids": ids})
    print(f"[DELETE] deleted={result.get('deleted', 0)}")


def result_ids(results: list[dict[str, Any]]) -> list[str]:
    # D1 hydration may put the id in the 'id' field; vector metadata uses vector_id.
    ids: list[str] = []
    for r in results:
        fact_id = r.get("id") or r.get("metadata", {}).get("id") or r.get("vector_id")
        if fact_id:
            ids.append(str(fact_id))
    return sorted(ids)


def main() -> int:
    suffix = uuid.uuid4().hex[:12]
    owner_id = f"e2e-test-company-scope-owner-{suffix}"
    query = "E2E company scope test fact"

    personal_default_id = f"e2e-test-company-scope-personal-default-{suffix}"
    personal_explicit_id = f"e2e-test-company-scope-personal-explicit-{suffix}"
    company_id = f"e2e-test-company-scope-company-{suffix}"

    cleanup_ids: list[str] = []

    try:
        # Step 1: Add a personal fact (default scope) and a company fact.
        add_fact(
            personal_default_id,
            f"E2E company scope test fact for personal default: the owner with suffix {suffix} prefers tea.",
            owner_id,
        )
        cleanup_ids.append(personal_default_id)

        add_fact(
            company_id,
            f"E2E company scope test fact for company: the team with suffix {suffix} has a shared quarterly budget.",
            owner_id,
            scope="company",
        )
        cleanup_ids.append(company_id)

        sync_pending()
        time.sleep(2)

        # Step 2/3: Personal search should return only the personal fact.
        personal_results = search_facts(query, owner_id, "personal")
        personal_ids = result_ids(personal_results)
        print(f"[SEARCH personal] ids={personal_ids}")
        if personal_ids != sorted([personal_default_id]):
            print(f"[FAIL] Expected only {personal_default_id}, got {personal_ids}")
            return 1
        print(f"[PASS] Personal search is isolated ({len(personal_results)} result)")

        # Company search should return only the company fact.
        company_results = search_facts(query, owner_id, "company")
        company_ids = result_ids(company_results)
        print(f"[SEARCH company] ids={company_ids}")
        if company_ids != sorted([company_id]):
            print(f"[FAIL] Expected only {company_id}, got {company_ids}")
            return 1
        print(f"[PASS] Company search is isolated ({len(company_results)} result)")

        # Step 4: Add an explicit personal fact and verify it behaves like default.
        add_fact(
            personal_explicit_id,
            f"E2E company scope test fact for explicit personal: the owner with suffix {suffix} wears blue sneakers.",
            owner_id,
            scope="personal",
        )
        cleanup_ids.append(personal_explicit_id)

        sync_pending()
        time.sleep(2)

        personal_results2 = search_facts(query, owner_id, "personal")
        personal_ids2 = result_ids(personal_results2)
        print(f"[SEARCH personal explicit] ids={personal_ids2}")
        if personal_ids2 != sorted([personal_default_id, personal_explicit_id]):
            print(f"[FAIL] Expected {sorted([personal_default_id, personal_explicit_id])}, got {personal_ids2}")
            return 1
        print(f"[PASS] Explicit scope=personal behaves like default ({len(personal_results2)} results)")

        company_results2 = search_facts(query, owner_id, "company")
        company_ids2 = result_ids(company_results2)
        print(f"[SEARCH company explicit] ids={company_ids2}")
        if company_ids2 != sorted([company_id]):
            print(f"[FAIL] Expected {sorted([company_id])}, got {company_ids2}")
            return 1
        print(f"[PASS] Company scope still isolated after explicit personal fact")

        print("\n[E2E] All company-scope tests PASSED")
        return 0
    finally:
        # Step 5: Clean up all test facts.
        delete_facts(cleanup_ids)


if __name__ == "__main__":
    sys.exit(main())
