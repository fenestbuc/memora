"""Live E2E roundtrip for the Memora RAG worker.

Exercises /health, /embed, /memory/add, /search, /chat, and /delete against the
deployed worker using credentials from the environment. Each run creates unique
facts prefixed with ``e2e-test-ragroundtrip-`` and cleans them up afterward.

Run with:
    RAG_WORKER_URL=https://... RAG_AUTH_TOKEN=*** python -m pytest tests/test_rag_roundtrip.py -v

The test is skipped automatically when live credentials are not available.
"""

from __future__ import annotations

import os
import time
import unittest
import uuid

from memora.http_client import HttpClient, HttpConfig

LIVE_URL = os.environ.get("RAG_WORKER_URL")
LIVE_TOKEN = os.environ.get("RAG_AUTH_TOKEN")
RUN_LIVE_E2E = os.environ.get("RUN_LIVE_RAG_E2E", "0") == "1"


def _token_is_real(token: str | None) -> bool:
    if not token:
        return False
    return "YOUR" not in token and "..." not in token


@unittest.skipUnless(
    RUN_LIVE_E2E and LIVE_URL and _token_is_real(LIVE_TOKEN),
    "set RUN_LIVE_RAG_E2E=1 with live credentials to run this test",
)
class TestRagWorkerEndpointRoundtrip(unittest.TestCase):
    """End-to-end roundtrip against the live Cloudflare RAG worker."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.run_id = uuid.uuid4().hex[:12]
        cls.fact_id = f"e2e-test-ragroundtrip-{cls.run_id}"
        cls.sentinel = f"snt-{cls.run_id}"
        cls.content = (
            f"The E2E roundtrip sentinel phrase is '{cls.sentinel}'. "
            f"The NavDhan E2E roundtrip test fact identifier is {cls.fact_id}. "
            "This fact is used to validate live RAG endpoints."
        )
        cls.client = HttpClient(
            HttpConfig(base_url=LIVE_URL or "", token=LIVE_TOKEN or "", timeout=120.0)
        )
        cls._cleanup_old_facts()

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.client.post("/delete", {"ids": [cls.fact_id]})
        except Exception as exc:  # pragma: no cover - best-effort cleanup
            print(f"tearDown cleanup failed for {cls.fact_id}: {exc}")

    @classmethod
    def _cleanup_old_facts(cls) -> None:
        """Remove stale facts left behind by earlier runs or aborted attempts."""
        try:
            listed = cls.client.post(
                "/memory/list",
                {"search": "e2e-test-ragroundtrip-", "limit": 200},
            )
            ids = [f["id"] for f in listed.get("facts", []) if f["id"] != cls.fact_id]
            if ids:
                cls.client.post("/delete", {"ids": ids})
        except Exception as exc:  # pragma: no cover - best-effort cleanup
            print(f"setUp cleanup of old facts failed: {exc}")

    def test_01_health_returns_ok(self) -> None:
        """GET /health should report the worker is up."""
        data = self.client.get("/health")
        self.assertEqual(data["status"], "ok")
        self.assertIn("embedding", data["models"])
        self.assertIn("llm", data["models"])

    def test_02_embed_returns_vector(self) -> None:
        """POST /embed should return a 1024-dim vector for the text."""
        data = self.client.post("/embed", {"text": [self.content]})
        self.assertEqual(data["count"], 1)
        self.assertEqual(len(data["embeddings"]), 1)
        self.assertGreater(len(data["embeddings"][0]), 0)
        self.assertEqual(data["model"], "@cf/baai/bge-m3")

    def test_03_memory_add_creates_fact(self) -> None:
        """POST /memory/add should persist the fact and report success."""
        data = self.client.post(
            "/memory/add",
            {"id": self.fact_id, "category": "e2e_test", "content": self.content},
        )
        self.assertTrue(data["success"])
        self.assertEqual(data["id"], self.fact_id)
        self.assertTrue(data["vector_sync"])
        self._wait_for_fact()

    def test_04_search_finds_added_fact(self) -> None:
        """POST /search should return the added fact once it is vectorized."""
        self._wait_for_fact()
        result = self._search_for_fact()
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], self.fact_id)

    def test_05_chat_uses_added_fact(self) -> None:
        """POST /chat should include the added fact in its sources."""
        self._wait_for_fact()
        query = f"What is the E2E roundtrip sentinel phrase '{self.sentinel}'?"
        data = self.client.post("/chat", {"query": query, "top_k": 5, "rerank": False})
        self.assertIsInstance(data["answer"], str)
        self.assertTrue(data["answer"])
        source_ids = {s.get("id") for s in data.get("sources", [])}
        self.assertIn(self.fact_id, source_ids)

    def test_06_delete_removes_fact(self) -> None:
        """POST /delete should remove the fact from the database."""
        self._wait_for_fact()
        data = self.client.post("/delete", {"ids": [self.fact_id]})
        self.assertTrue(data["success"])
        listed = self.client.post("/memory/list", {"search": self.fact_id, "limit": 10})
        self.assertEqual(listed["total"], 0)

    def _search_for_fact(self) -> dict | None:
        """Search globally without scope filters to avoid Vectorize metadata-filter lag."""
        data = self.client.post(
            "/search",
            {"query": self.content, "top_k": 50, "rerank": False},
        )
        for result in data.get("results", []):
            if result.get("id") == self.fact_id:
                return result
        return None

    def _wait_for_fact(self, timeout: float = 150.0, interval: float = 3.0) -> None:
        """Poll /search until the new fact is indexed, varying top_k to avoid stale cache hits."""
        deadline = time.time() + timeout
        attempt = 0
        while time.time() < deadline:
            data = self.client.post(
                "/search",
                {
                    "query": self.content,
                    "top_k": 10 + (attempt % 10),
                    "rerank": False,
                },
            )
            for result in data.get("results", []):
                if result.get("id") == self.fact_id:
                    return
            attempt += 1
            time.sleep(interval)
        self.fail(f"Fact {self.fact_id} was not returned by /search within {timeout}s")
