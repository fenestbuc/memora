"""MCP sync module for polling external knowledge sources (e.g., Notion).

Provides lightweight polling wrappers that detect changes in third-party
platforms and surface them as Memora facts.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def poll_notion_database(
    notion_token: str,
    database_id: str,
    last_poll_ts: float | None = None,
) -> List[Dict[str, Any]]:
    """Poll a Notion database for recently updated pages.

    Args:
        notion_token: Notion integration bearer token.
        database_id: Identifier of the Notion database to query.
        last_poll_ts: Optional Unix timestamp. When provided, only pages
            edited after this time are returned.

    Returns:
        A list of page dictionaries, each containing ``id``, ``title``,
        ``last_edited_time``, and ``url``.
    """
    url = f"https://api.notion.com/v1/databases/{database_id}/query"
    headers = {
        "Authorization": f"Bearer {notion_token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }

    payload: Dict[str, Any] = {"page_size": 100}
    if last_poll_ts is not None:
        iso_time = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(last_poll_ts))
        payload["filter"] = {
            "timestamp": "last_edited_time",
            "last_edited_time": {"after": iso_time},
        }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.warning("Notion poll failed: %s", exc)
        return []

    pages = []
    for page in result.get("results", []):
        props = page.get("properties", {})
        title = ""
        for prop_val in props.values():
            if prop_val.get("type") == "title":
                title_items = prop_val.get("title", [])
                title = "".join(t.get("plain_text", "") for t in title_items)
                break

        pages.append(
            {
                "id": page.get("id"),
                "title": title,
                "last_edited_time": page.get("last_edited_time"),
                "url": page.get("url"),
            }
        )

    return pages
