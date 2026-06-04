"""Mirror queued facts to local markdown files.

Each category gets its own ``.md`` file.  Entries are appended monotonically.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def write(memory_dir: Path | None, category: str, content: str, *, session_id: str = "unknown") -> None:
    """Append a single fact to the category markdown file.

    Args:
        memory_dir: Root directory for mirrored markdown files.
        category: Fact category (slugified for filename).
        content: Fact text.
        session_id: Source session ID for attribution.
    """
    if memory_dir is None:
        return
    safe_category = category.lower().replace(" ", "_")
    file_path = memory_dir / f"{safe_category}.md"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    entry = f"- [{timestamp}] [{session_id}] {content}\n"
    with open(file_path, "a", encoding="utf-8") as f:
        f.write(entry)
