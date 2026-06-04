"""Swarm manager — pluggable Kanban dispatch for autonomous task creation.

Supports Hermes native Kanban as the primary backend and Linear as an
optional fallback for open-source users who do not run Hermes.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Backend discovery
# ---------------------------------------------------------------------------

try:
    from hermes_cli.kanban import create as _hermes_kanban_create
except ImportError:  # pragma: no cover
    _hermes_kanban_create = None  # type: ignore[misc,assignment]


def _get_configured_backend() -> str:
    """Return the Kanban backend configured by the user.

    Priority:
    1. ``MEMORA_KANBAN_BACKEND`` environment variable.
    2. Default to ``hermes`` when ``kanban_create`` is available.
    3. Fall back to ``none`` (tasks are logged but not dispatched).
    """
    env = os.environ.get("MEMORA_KANBAN_BACKEND", "").lower()
    if env in ("hermes", "linear", "none"):
        return env
    if kanban_create is not None:
        return "hermes"
    return "none"


# ---------------------------------------------------------------------------
# Linear API wrapper
# ---------------------------------------------------------------------------

def _linear_create_issue(
    title: str,
    body: str,
    tags: list[str],
) -> dict[str, Any] | None:
    """Create a Linear issue via the public REST API.

    Requires ``LINEAR_API_KEY`` and optionally ``LINEAR_TEAM_ID`` in the
    environment (or ``~/.hermes/config.yaml``).
    """
    token = os.environ.get("LINEAR_API_KEY", "")
    team_id = os.environ.get("LINEAR_TEAM_ID", "")

    if not token:
        logger.warning("Linear API key not configured; set LINEAR_API_KEY")
        return None

    query = """
    mutation CreateIssue($input: IssueCreateInput!) {
        issueCreate(input: $input) {
            success
            issue { id identifier url title }
        }
    }
    """

    variables: dict[str, Any] = {
        "input": {
            "title": title,
            "description": body,
            "labelIds": tags,  # Linear labels can be passed by name if mapped
        }
    }
    if team_id:
        variables["input"]["teamId"] = team_id

    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(
        "https://api.linear.app/graphql",
        data=payload,
        headers={
            "Authorization": token,
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        logger.error("Linear API call failed: %s", exc)
        return {"error": str(exc)}

    issue_data = data.get("data", {}).get("issueCreate", {})
    if issue_data.get("success"):
        issue = issue_data["issue"]
        logger.info("Created Linear issue %s: %s", issue["identifier"], issue["url"])
        return {"issue_id": issue["id"], "identifier": issue["identifier"], "url": issue["url"]}
    else:
        errors = data.get("errors", [])
        logger.error("Linear issue creation failed: %s", errors)
        return {"error": errors}


# ---------------------------------------------------------------------------
# Unified dispatch
# ---------------------------------------------------------------------------

def trigger(
    source: str,
    content: str,
    category: str = "memory",
    scope: str = "personal",
    agent_role: str = "analyst",
) -> dict[str, Any] | None:
    """Spawn a Kanban / Linear task when a new fact is ingested.

    Args:
        source: Where the fact came from (e.g. ``rag``, ``mcp_notion``,
            ``sync_turn``).
        content: The fact content that triggered the swarm task.
        category: Fact category tag.
        scope: ``personal`` or ``company``.
        agent_role: Role of the agent to spawn (e.g. ``analyst``,
            ``reviewer``).

    Returns:
        The backend response dict, or ``None`` if no backend is available.
    """
    backend = _get_configured_backend()

    title = f"[{agent_role}] Synthesize new {scope} fact from {source}"
    body = (
        f"**Source:** {source}\n"
        f"**Category:** {category}\n"
        f"**Scope:** {scope}\n\n"
        f"**Fact:**\n{content[:2000]}"
    )
    tags = [source, category, scope, agent_role]

    if backend == "hermes":
        # Use the module-level binding so tests can patch it easily.
        if kanban_create is None:
            logger.warning(
                "Hermes kanban configured but hermes_cli.kanban is not importable"
            )
            return None
        try:
            result = kanban_create(title=title, body=body, tags=tags)
            logger.info("Dispatched Hermes kanban task for %s fact from %s", scope, source)
            return result
        except Exception as exc:  # pragma: no cover
            logger.error("Hermes kanban_create failed: %s", exc)
            return {"error": str(exc)}

    elif backend == "linear":
        result = _linear_create_issue(title=title, body=body, tags=tags)
        if result and "error" not in result:
            logger.info("Dispatched Linear issue for %s fact from %s", scope, source)
        return result

    else:
        logger.warning(
            "No Kanban backend configured (MEMORA_KANBAN_BACKEND=%s). "
            "Set to 'hermes' or 'linear', or install Hermes CLI.",
            backend,
        )
        return None


# Expose for tests and backward-compat
kanban_create: Callable[..., dict[str, Any] | None] | None = _hermes_kanban_create
