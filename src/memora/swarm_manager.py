"""Swarm manager — wrapper around kanban_create for autonomous task dispatch.

When new facts arrive via MCP or RAG, this module spawns Kanban delegate tasks
(e.g. an ``analyst`` agent to synthesize a Notion document).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Placeholder for the external kanban_create API.
# In production this is imported from the Hermes kanban dispatcher.
try:
    from hermes_cli.kanban import create as kanban_create
except ImportError:  # pragma: no cover
    kanban_create = None  # type: ignore[misc,assignment]


def trigger(
    source: str,
    content: str,
    category: str = "memory",
    scope: str = "personal",
    agent_role: str = "analyst",
) -> dict[str, Any] | None:
    """Spawn a kanban delegate task when a new fact is ingested.

    Args:
        source: Where the fact came from (e.g. ``rag``, ``mcp_notion``,
            ``sync_turn``).
        content: The fact content that triggered the swarm task.
        category: Fact category tag.
        scope: ``personal`` or ``company``.
        agent_role: Role of the agent to spawn (e.g. ``analyst``,
            ``reviewer``).

    Returns:
        The ``kanban_create`` response dict, or ``None`` if
        ``kanban_create`` is unavailable.
    """
    if kanban_create is None:
        logger.warning("kanban_create unavailable; cannot dispatch swarm task")
        return None

    title = f"[{agent_role}] Synthesize new {scope} fact from {source}"
    body = (
        f"**Source:** {source}\n"
        f"**Category:** {category}\n"
        f"**Scope:** {scope}\n\n"
        f"**Fact:**\n{content[:2000]}"
    )

    try:
        result = kanban_create(
            title=title,
            body=body,
            tags=[source, category, scope, agent_role],
        )
        logger.info("Dispatched kanban task for %s fact from %s", scope, source)
        return result
    except Exception as exc:  # pragma: no cover
        logger.error("kanban_create failed: %s", exc)
        return {"error": str(exc)}
