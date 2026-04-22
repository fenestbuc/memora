#!/usr/bin/env python3
"""Brain Indexer — scans hermes-workspace, maintains manifests, detects conflicts."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

# Trackable extensions and max file size
_TRACKABLE_EXTS = {".md", ".txt", ".py", ".js", ".json", ".yaml", ".yml", ".csv", ".sh"}
_DEFAULT_MAX_SIZE = 5 * 1024 * 1024  # 5MB
_EXCLUDE_DIRS = {"__pycache__", ".git", ".chroma", ".hermes", "_archive"}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _ensure_dirs(workspace: Path) -> Path:
    idx = workspace / ".second-brain" / "index"
    idx.mkdir(parents=True, exist_ok=True)
    return idx


def scan_workspace(workspace_str: str, max_size: int = _DEFAULT_MAX_SIZE) -> Dict[str, Any]:
    """Walk workspace and write files.json manifest. Returns manifest dict."""
    workspace = Path(workspace_str)
    idx = _ensure_dirs(workspace)
    files_json = idx / "files.json"

    # Load existing manifest for delta and mtime caching
    old_manifest = {"files": []}
    if files_json.exists():
        try:
            old_manifest = json.loads(files_json.read_text())
        except Exception:
            pass

    old_by_path = {f["path"]: f for f in old_manifest.get("files", [])}

    files = []
    for root, dirs, filenames in os.walk(workspace):
        # Skip excluded dirs
        dirs[:] = [d for d in dirs if d not in _EXCLUDE_DIRS and not d.startswith(".")]

        for filename in filenames:
            path = Path(root) / filename
            ext = path.suffix.lower()

            # Skip hidden, binary, or oversize
            if filename.startswith("."):
                continue
            if ext not in _TRACKABLE_EXTS:
                continue
            try:
                stat = path.stat()
                size = stat.st_size
            except OSError:
                continue
            if size > max_size:
                continue

            rel = str(path.relative_to(workspace))
            mtime_iso = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()

            # Reuse cached hash if mtime hasn't changed
            old_entry = old_by_path.get(rel)
            if old_entry and old_entry.get("modified") == mtime_iso and old_entry.get("size") == size:
                sha = old_entry["sha256"]
            else:
                sha = _sha256(path)

            files.append({
                "path": rel,
                "name": filename,
                "type": ext.lstrip(".") if ext else "",
                "size": size,
                "sha256": sha,
                "modified": mtime_iso,
            })

    # Detect delta
    old_paths = {f["sha256"]: f for f in old_manifest.get("files", [])}
    new_paths = {f["sha256"]: f for f in files}

    delta = {
        "added": [f for h, f in new_paths.items() if h not in old_paths],
        "removed": [f for h, f in old_paths.items() if h not in new_paths],
        "changed": [],  # same sha but different mod time would need tracking
    }

    manifest = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "total_files": len(files),
        "files": sorted(files, key=lambda f: f["path"]),
        "delta": delta,
    }

    files_json.write_text(json.dumps(manifest, indent=2))
    _append_changelog(workspace, f"scan_workspace: {len(delta['added'])} added, {len(delta['removed'])} removed")
    return manifest


def index_sessions(workspace_str: str) -> Dict[str, Any]:
    """Read sessions/ and write sessions.json with metadata."""
    workspace = Path(workspace_str)
    idx = _ensure_dirs(workspace)
    sessions_json = idx / "sessions.json"

    sessions_dir = workspace / "sessions"
    if not sessions_dir.exists():
        return {"total_sessions": 0, "sessions": []}

    sessions = []
    for path in sorted(sessions_dir.glob("*.md")):
        text = path.read_text(errors="replace")
        # Topic hint: first user message
        first_user = re.search(r"(?:User|user):\s*(.+)", text)
        topic = first_user.group(1).strip()[:60] if first_user else ""

        # Entity extraction: simple keyword patterns
        entities = list(set(re.findall(r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*", text)))
        entities = [e for e in entities if len(e) > 3][:20]

        sessions.append({
            "session_id": path.stem,
            "topic_hint": topic,
            "size": path.stat().st_size,
            "entities": entities,
        })

    result = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "total_sessions": len(sessions),
        "sessions": sessions,
    }
    sessions_json.write_text(json.dumps(result, indent=2))
    _append_changelog(workspace, f"index_sessions: {len(sessions)} sessions indexed")
    return result


def detect_conflicts(old_facts: Dict[str, Any], new_facts: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Compare old and new fact dicts, return list of contradictions."""
    conflicts = []
    for key, new_val in new_facts.items():
        if key in old_facts:
            old_val = old_facts[key]
            if old_val != new_val:
                # Simple heuristic: if values differ significantly, flag
                conflicts.append({
                    "entity": key,
                    "old": old_val,
                    "new": new_val,
                })
    return conflicts


def _append_changelog(workspace: Path, line: str) -> None:
    changelog = workspace / ".second-brain" / "index" / "changelog.md"
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    entry = f"## [{date}] {line}\n"

    if changelog.exists():
        content = changelog.read_text()
        if date not in content[:200]:
            entry = f"\n## {date}\n\n{entry}"
        changelog.write_text(entry + content)
    else:
        changelog.write_text(f"# Brain Changelog\n\n{entry}")


if __name__ == "__main__":
    import sys
    ws = sys.argv[1] if len(sys.argv) > 1 else str(Path.home() / "hermes-workspace")
    scan_workspace(ws)
    index_sessions(ws)
    print(f"Indexed {ws}")
