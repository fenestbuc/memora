"""E2E tests for the deferred vector sync path.

These tests hit the live deployed RAG worker (RAG_WORKER_URL + RAG_AUTH_TOKEN)
and verify that:
  1. memora.daemon.memory_sync_loop calls POST /memory/sync periodically.
  2. POST /memory/sync processes rows that are queued with pending_vector_sync.
  3. HttpClient sends an empty JSON body ({}) instead of omitting it.

Any test facts created here are deleted in a finally block.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import pytest

from memora.daemon import memory_sync_loop
from memora.http_client import HttpClient, HttpConfig


class TestHttpClientEmptyBody:
    """Regression: HttpClient.post must send '{}' for an empty dict body."""

    def test_post_empty_dict_sends_json_body(self, monkeypatch: pytest.MonkeyPatch):
        calls = []
        def _fake_request(self, url, *, method="GET", data=None):
            calls.append((url, method, data))
            return {"ok": True}

        monkeypatch.setattr(HttpClient, "_request", _fake_request)
        client = HttpClient(HttpConfig(base_url="https://example.com", token="tok"))
        result = client.post("/memory/sync", {})

        assert result == {"ok": True}
        assert len(calls) == 1
        url, method, data = calls[0]
        assert method == "POST"
        assert data == b"{}"
        assert url == "https://example.com/memory/sync"

    def test_post_none_body_sends_no_data(self, monkeypatch: pytest.MonkeyPatch):
        calls = []
        def _fake_request(self, url, *, method="GET", data=None):
            calls.append((url, method, data))
            return {"ok": True}

        monkeypatch.setattr(HttpClient, "_request", _fake_request)
        client = HttpClient(HttpConfig(base_url="https://example.com", token="tok"))
        client.post("/memory/sync", None)

        assert calls[0][2] is None


def _live_client() -> HttpClient:
    base_url = os.environ.get("RAG_WORKER_URL", "").rstrip("/")
    token = os.environ.get("RAG_AUTH_TOKEN", "")
    if not base_url or not token:
        pytest.skip("RAG_WORKER_URL and RAG_AUTH_TOKEN are required for live E2E tests")
    return HttpClient(HttpConfig(base_url=base_url, token=token))


@pytest.mark.e2e
def test_memory_sync_endpoint_processes_pending_vector_sync_rows() -> None:
    """Live worker: /memory/sync flushes a fact that is stuck in pending_vector_sync."""
    client = _live_client()
    fact_id = f"e2e-deferred-vector-sync-{uuid.uuid4()}"

    try:
        stats_before = client.get("/memory/stats")
        pending_before = stats_before.get("pending_vector_sync", 0)

        # Add a normal fact; the vector is written synchronously.
        add_result = client.post("/memory/add", {
            "id": fact_id,
            "category": "test",
            "content": "E2E deferred vector sync test fact",
            "scope": "company",
            "owner_id": "e2e",
        })
        assert add_result.get("success") is True
        assert add_result.get("vector_sync") is True

        # Force the vector write to fail for this fact by making metadata too large.
        big_source_file = "x" * 11_000
        update_big = client.post("/memory/update", {
            "id": fact_id,
            "content": "E2E deferred vector sync test fact - large metadata",
            "source_file": big_source_file,
        })
        assert update_big.get("success") is True
        assert update_big.get("vector_sync") is False

        # Shrink the metadata so a subsequent sync succeeds.
        # Note: handleMemoryUpdate does not reset pending_vector_sync on success,
        # so the row remains queued for the sync job.
        update_small = client.post("/memory/update", {
            "id": fact_id,
            "content": "E2E deferred vector sync test fact - final",
            "source_file": "",
        })
        assert update_small.get("success") is True
        assert update_small.get("vector_sync") is True

        stats_queued = client.get("/memory/stats")
        assert stats_queued["pending_vector_sync"] == pending_before + 1

        # The actual behaviour under test: /memory/sync processes pending rows.
        sync_result = client.post("/memory/sync", {})
        assert sync_result.get("success") is True
        assert sync_result.get("synced") == 1

        stats_after = client.get("/memory/stats")
        assert stats_after["pending_vector_sync"] == pending_before
    finally:
        # Clean up the test fact regardless of test outcome.
        try:
            client.post("/memory/delete", {"id": fact_id})
        except HTTPError:
            pass


def test_daemon_memory_sync_loop_calls_sync_periodically() -> None:
    """Daemon loop hits POST /memory/sync repeatedly when the interval is short."""
    env = {
        "RAG_WORKER_URL": "https://worker.test",
        "RAG_AUTH_TOKEN": "token",
        "MEMORA_SYNC_INTERVAL_SECONDS": "0.01",
    }

    mock_post = MagicMock(return_value={"synced": 1})
    mock_client_cls = MagicMock(return_value=MagicMock(post=mock_post))

    async def _run():
        with patch.dict("os.environ", env, clear=False):
            with patch("memora.http_client.HttpClient", mock_client_cls):
                task = asyncio.create_task(memory_sync_loop())
                # Let the loop fire a few times.
                await asyncio.sleep(0.08)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    asyncio.run(_run())

    mock_client_cls.assert_called_once()
    assert mock_post.call_count >= 2
    for call in mock_post.call_args_list:
        assert call.args == ("/memory/sync", {})
