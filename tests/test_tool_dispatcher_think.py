"""Tests for memora_think dispatch."""

from memora.tool_dispatcher import dispatch


def test_dispatch_memora_think() -> None:
    path, body = dispatch("memora_think", {"query": "What about acme?", "top_k": 5}, owner_id="alice")
    assert path == "/think"
    assert body["query"] == "What about acme?"
    assert body["top_k"] == 5
    assert body["owner_id"] == "alice"


def test_dispatch_memora_think_respects_scope() -> None:
    path, body = dispatch("memora_think", {"query": "Q", "scope": "company"}, owner_id="bob")
    assert body["scope"] == "company"
