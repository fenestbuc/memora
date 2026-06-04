"""Feedback interceptor for routing corrections.

Captures when a kanban task is reassigned so Memora can learn
which agent should have been selected originally.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def capture_routing_correction(
    args: Dict[str, Any],
    jsonl_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Record a routing correction from a kanban_reassign tool call.

    Args:
        args: Tool arguments. Expected keys:
            - ``task_id`` (str): Identifier of the kanban task.
            - ``original_agent`` (str, optional): Agent the task was originally
              routed to.
            - ``target_agent`` (str, optional): Agent the task was reassigned to.
            - ``reason`` (str, optional): Human or LLM-provided reason for the
              correction.
        jsonl_path: Optional path to a JSONL file. When provided, the
            correction is appended as a single JSON line to prevent
            git merge conflicts on concurrent team feedback.

    Returns:
        A normalized dict with ``task_id``, ``original_agent``,
        ``target_agent``, ``reason``, and ``captured_at``.
    """
    task_id = args.get("task_id", "")
    original_agent = args.get("original_agent") or args.get("from_agent", "")
    target_agent = args.get("target_agent") or args.get("to_agent", "")
    reason = args.get("reason", "")

    if not task_id:
        logger.warning("Routing correction received without task_id")

    correction = {
        "task_id": task_id,
        "original_agent": original_agent,
        "target_agent": target_agent,
        "reason": reason,
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }

    if jsonl_path:
        Path(jsonl_path).parent.mkdir(parents=True, exist_ok=True)
        with open(jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(correction, ensure_ascii=False) + "\n")
        logger.debug("Appended routing correction to %s", jsonl_path)

    logger.info(
        "Captured routing correction for task %s: %s -> %s",
        task_id,
        original_agent or "(none)",
        target_agent or "(none)",
    )
    return correction
