"""LLM Triage Gate for Swarm Management.

Prevents kanban ticket bloat by asking a fast LLM (or local heuristic)
whether a fact is actionable before triggering a swarm delegate task.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

# Fast local heuristic keywords that indicate actionability.
_ACTIONABLE_KEYWORDS = (
    "action",
    "actionable",
    "decide",
    "decision",
    "decided",
    "todo",
    "task",
    "must",
    "should",
    "need to",
    "plan to",
    "urgent",
    "critical",
    "priority",
    "deadline",
    "due",
    "create",
    "implement",
    "deploy",
    "fix",
    "bug",
    "issue",
    "schedule",
    "meeting",
    "sync",
    "review",
    "follow up",
    "follow-up",
    "followup",
    "assign",
    "delegate",
)


def _triage_url() -> str:
    """Return the configured triage endpoint URL, if any."""
    return os.environ.get("HERMES_TRIAGE_URL", "")


def should_trigger_swarm(content: str) -> bool:
    """Return True if *content* is actionable enough to spawn a swarm task.

    Priority:
    1. If ``HERMES_TRIAGE_URL`` is set, POST the content to the endpoint
       and parse the JSON response for an ``actionable`` boolean.
    2. On any network / parse error, fall back to a fast keyword heuristic.
    3. If no URL is configured, use the heuristic directly.

    Args:
        content: The fact text to evaluate.

    Returns:
        ``True`` when the content warrants a kanban delegate task.
    """
    url = _triage_url()
    if url:
        try:
            return _call_triage_api(url, content)
        except Exception as exc:
            logger.debug("Triage API failed, falling back to heuristic: %s", exc)
            return _heuristic_actionable(content)

    return _heuristic_actionable(content)


def _call_triage_api(url: str, content: str) -> bool:
    """POST to the triage endpoint and return the ``actionable`` flag.

    Args:
        url: The triage endpoint URL.
        content: The fact text to evaluate.

    Returns:
        The ``actionable`` boolean from the response.

    Raises:
        urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError,
        KeyError: on any network or parse failure so the caller can fall back.
    """
    payload = json.dumps({"content": content}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "memora-triage/1.0",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        return bool(result.get("actionable", False))


def _heuristic_actionable(content: str) -> bool:
    """Fast keyword-based heuristic for actionability.

    Args:
        content: The fact text to evaluate.

    Returns:
        ``True`` if any actionable keyword is found in the content.
    """
    lower = content.lower()
    return any(kw in lower for kw in _ACTIONABLE_KEYWORDS)
