"""Dispatch memora_* tool calls to RAG worker endpoints.

Pure logic — no HTTP, no side effects.  Returns (path, body) tuples.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Dict, Tuple


def build_search_payload(args: dict[str, Any], owner_id: str) -> Tuple[str, dict[str, Any]]:
    body: dict[str, Any] = {
        "query": args["query"],
        "top_k": args.get("top_k", 10),
        "owner_id": owner_id,
    }
    if "use_reranking" in args:
        body["use_reranking"] = args["use_reranking"]
    if "parent_id" in args:
        body["parent_id"] = args["parent_id"]
    if "scope" in args:
        body["scope"] = args["scope"]
    return "/search", body


def build_add_payload(args: dict[str, Any], owner_id: str) -> Tuple[str, dict[str, Any]]:
    body: dict[str, Any] = {
        "content": args["content"],
        "category": args.get("category", "memory"),
        "owner_id": owner_id,
    }
    if "parent_id" in args:
        body["parent_id"] = args["parent_id"]
    if "id" in args:
        body["id"] = args["id"]
    return "/memory/add", body


def dispatch(
    tool_name: str, args: dict[str, Any], *, owner_id: str
) -> Tuple[str, dict[str, Any] | None]:
    """Return (path, body) for a given tool call.

    *body* is ``None`` for GET-style endpoints.
    Raises ``NotImplementedError`` for unknown tools.
    """
    if tool_name == "memora_search":
        return build_search_payload(args, owner_id)
    if tool_name == "memora_list":
        return "/memory/list", {k: v for k, v in args.items() if v is not None}
    if tool_name == "memora_add":
        return build_add_payload(args, owner_id)
    if tool_name == "memora_update":
        body = {"id": args["id"]}
        if "content" in args:
            body["content"] = args["content"]
        if "category" in args:
            body["category"] = args["category"]
        return "/memory/update", body
    if tool_name == "memora_delete":
        return "/memory/delete", {"ids": args.get("ids", [])}
    if tool_name == "memora_stats":
        return "/memory/stats", None
    if tool_name == "memora_think":
        body = {
            "query": args["query"],
            "top_k": args.get("top_k", 10),
            "owner_id": owner_id,
        }
        if "scope" in args:
            body["scope"] = args["scope"]
        return "/think", body
    raise NotImplementedError(f"Provider does not handle tool {tool_name}")


def search_cache_key(body: dict[str, Any]) -> str:
    """Deterministic cache key for a search payload."""
    hasher = hashlib.sha256()
    hasher.update(json.dumps(body, sort_keys=True).encode("utf-8"))
    return f"search:{hasher.hexdigest()}"
