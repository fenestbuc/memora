"""LLM-based importance scoring for Memora facts.

Calls the RAG worker's chat endpoint to score the long-term importance
of a fact on a 0.0–1.0 scale.  Results are cached to avoid duplicate API
calls within a single process lifetime.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_URL = os.environ.get("RAG_WORKER_URL", "")
_DEFAULT_TOKEN = os.environ.get("RAG_AUTH_TOKEN", "")

_IMPORTANCE_PROMPT = """\
Rate the long-term importance of the following fact on a scale of 0.0 to 1.0.

Key criteria:
- 0.9–1.0: Fundamental business decisions, permanent preferences, critical strategy
- 0.7–0.8: Important project details, key stakeholder info, significant learnings
- 0.5–0.6: Useful context, temporary plans, minor preferences
- 0.3–0.4: Tangential details, draft ideas, one-off observations
- 0.0–0.2: Filler, noise, ephemeral chat, greetings

Fact category: {category}
Fact content: {content}

Respond with ONLY a single number between 0.0 and 1.0, no explanation."""


@lru_cache(maxsize=1024)
def _cached_score(fact_hash: str, score: float) -> float:
    """Dummy cache wrapper — real caching is handled by lru_cache on the caller."""
    return score


def compute_importance(
    content: str,
    category: str = "memory",
    base_url: str | None = None,
    token: str | None = None,
) -> float:
    """Score a fact's long-term importance using the configured LLM.

    Falls back to rule-based heuristic if the LLM call fails or
    RAG_WORKER_URL is not configured.

    Args:
        content: The fact text.
        category: Fact category tag.
        base_url: RAG worker URL (defaults to RAG_WORKER_URL env var).
        token: RAG auth token (defaults to RAG_AUTH_TOKEN env var).

    Returns:
        Importance score in range [0.05, 1.0].
    """
    url = (base_url or _DEFAULT_URL).rstrip("/")
    auth = token or _DEFAULT_TOKEN

    if not url or not auth:
        logger.debug("No RAG worker configured; using heuristic importance")
        return _heuristic_importance(content, category)

    # Use content hash for cache key to avoid unbounded key sizes
    content_hash = hashlib.sha256(f"{category}:{content}".encode()).hexdigest()[:16]
    return _compute_importance_cached(content_hash, content, category, url, auth)


@lru_cache(maxsize=1024)
def _compute_importance_cached(
    _content_hash: str,
    content: str,
    category: str,
    url: str,
    auth: str,
) -> float:
    """Cached inner function.  Uses SHA-256 hash as the bounded cache key."""
    prompt = _IMPORTANCE_PROMPT.format(category=category, content=content[:2000])

    try:
        req = urllib.request.Request(
            f"{url}/chat",
            data=json.dumps({
                "query": prompt,
                "system": "You are a fact importance evaluator. Respond with ONLY a number between 0.0 and 1.0.",
                "top_k": 0,
            }).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {auth}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        answer = str(data.get("answer", "")).strip()
        score = _parse_score(answer)

    except Exception as exc:
        logger.warning("LLM importance scoring failed (%s); using heuristic", exc)
        score = _heuristic_importance(content, category)

    return score


def _parse_score(answer: str) -> float:
    """Extract a float from LLM response text."""
    # Remove common wrappers
    answer = answer.replace("**", "").replace("`", "").strip()
    # Try to find the first number
    for token in answer.split():
        try:
            val = float(token)
            if 0.0 <= val <= 1.0:
                return round(max(0.05, min(1.0, val)), 3)
        except ValueError:
            continue
    logger.debug("Could not parse score from: %r", answer)
    return 0.5


def _heuristic_importance(content: str, category: str) -> float:
    """Rule-based fallback when LLM is unavailable.

    Scoring rules:
      • Preference keywords (always, prefer, never)      +0.25
      • Decision keywords (decided, must, critical)      +0.20
      • Weak signals (maybe, draft, consider)            −0.20
      • URL-only content                                 −0.30
      • Category = user (preferences)                    +0.15
      • Category = draft / scratchpad                    −0.25
      • Content length > 500 (substantial)               +0.05
    """
    lower = content.lower()
    score = 0.5

    strong_signals = ["prefer", "always", "never", "must not"]
    if any(s in lower for s in strong_signals):
        score += 0.25

    decision_signals = ["decided", "decision", "critical", "must ", "ensure"]
    if any(s in lower for s in decision_signals):
        score += 0.20

    weak_signals = ["maybe", "draft", "consider", "possibly"]
    if any(s in lower for s in weak_signals):
        score -= 0.20

    if content.startswith("http") and " " not in content:
        score -= 0.30

    cat_lower = category.lower()
    if cat_lower in ("user", "preference"):
        score += 0.15
    elif cat_lower in ("draft", "scratchpad", "temp"):
        score -= 0.25

    if len(content) > 500:
        score += 0.05

    return round(max(0.05, min(1.0, score)), 3)


def reset_cache() -> None:
    """Clear the in-memory importance scoring cache."""
    _compute_importance_cached.cache_clear()
