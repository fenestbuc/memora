"""Discord webhook proxy for local RAG queries.

Parses incoming Discord webhook payloads and routes message content through
the local Memora RAG provider, returning a formatted response suitable for
posting back to Discord.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict

logger = logging.getLogger(__name__)


def parse_discord_payload(body: str | bytes | Dict[str, Any]) -> Dict[str, Any]:
    """Parse a Discord webhook payload.

    Args:
        body: Raw JSON string, bytes, or a pre-parsed dictionary.

    Returns:
        A dictionary with ``content``, ``author``, and ``channel_id`` keys.
    """
    if isinstance(body, dict):
        data = body
    elif isinstance(body, bytes):
        data = json.loads(body.decode("utf-8"))
    else:
        data = json.loads(body)

    return {
        "content": data.get("content", ""),
        "author": data.get("author", {}).get("username", "unknown"),
        "channel_id": data.get("channel_id", ""),
    }


def proxy_query(
    payload: Dict[str, Any],
    search_fn: Callable[[str], str],
) -> str:
    """Proxy a Discord message through a local RAG search.

    Args:
        payload: Parsed Discord payload from :func:`parse_discord_payload`.
        search_fn: Callable that accepts a query string and returns a
            result string (e.g., ``MemoraProvider.prefetch``).

    Returns:
        A formatted response string ready to be sent back to Discord.
    """
    content = payload.get("content", "").strip()
    if not content:
        return "No message content to query."

    # Strip common bot mention prefixes so the RAG query is clean.
    content = content.lstrip("@Memora ").lstrip("@memora ")

    try:
        rag_result = search_fn(content)
    except Exception as exc:
        logger.warning("RAG query failed for Discord proxy: %s", exc)
        return f"Sorry, I couldn't retrieve memory right now. ({exc})"

    if not rag_result or not rag_result.strip():
        return "I don't have any relevant memories about that."

    return f"**Relevant memories:**\n{rag_result}"
