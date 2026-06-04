"""Extract preference-like facts from conversation messages.

Keyword heuristics + imperative-command detection.  Zero dependencies.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

_KEYWORDS = (
    "prefer", "want", "need", "must not", "always", "never",
    "decided", "decision", "should", "should not", "important",
    "critical", "do not", "avoid", "ensure", "make sure",
)

_COMMAND_VERBS = (
    "use", "send", "format", "schedule", "set", "enable", "disable",
    "include", "exclude", "follow", "apply", "implement",
)

_URL_RE = re.compile(r"^https?://\S+$")
_CODE_BLOCK_RE = re.compile(r"^```")


def extract_facts(messages: List[Dict[str, Any]]) -> list[str]:
    """Return a list of fact strings extracted from *messages*.

    Filters out short, URL-only, and code-block messages.
    """
    facts: list[str] = []
    for msg in messages:
        content = msg.get("content", "")
        if not content:
            continue
        stripped = content.strip()
        if len(stripped) < 30:
            continue
        if _URL_RE.match(stripped):
            continue
        if _CODE_BLOCK_RE.search(stripped):
            continue

        lower = stripped.lower()

        # Keyword-based extraction
        if any(k in lower for k in _KEYWORDS):
            facts.append(f"Key fact: {stripped[:800]}")
            continue

        # Imperative command extraction
        first_sentence = re.split(r"[.!?]", stripped)[0].strip()
        first_word = first_sentence.split()[0].lower() if first_sentence.split() else ""
        if first_word in _COMMAND_VERBS and len(stripped) > 15:
            facts.append(f"Key fact: {stripped[:800]}")

    return facts
