#!/usr/bin/env python3
"""Conflict detector — scans sessions vs stored preferences, flags contradictions."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List


def detect_conflicts(workspace_str: str, max_session_age_days: int = 7) -> Dict[str, Any]:
    """Scan recent sessions against stored memory, return conflicts."""
    workspace = Path(workspace_str)
    conflicts_dir = workspace / ".second-brain" / "conflicts"
    conflicts_dir.mkdir(parents=True, exist_ok=True)

    # Load stored preferences from memory/*.md
    stored_prefs = _load_stored_preferences(workspace)

    # Scan recent sessions
    sessions_dir = workspace / "sessions"
    found_conflicts = []

    if sessions_dir.exists():
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_session_age_days)
        for path in sessions_dir.glob("*.md"):
            try:
                mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
                if mtime < cutoff:
                    continue
            except OSError:
                continue

            text = path.read_text(errors="replace")
            session_prefs = _extract_preferences(text)

            for pref_key, new_val in session_prefs.items():
                if pref_key in stored_prefs:
                    old_val = stored_prefs[pref_key]
                    if _is_contradiction(old_val, new_val):
                        found_conflicts.append({
                            "entity": pref_key,
                            "old": old_val,
                            "new": new_val,
                            "source": path.name,
                            "detected": datetime.now(timezone.utc).isoformat(),
                        })

    result = {
        "detected": datetime.now(timezone.utc).isoformat(),
        "total_conflicts": len(found_conflicts),
        "conflicts": found_conflicts,
    }

    pending = conflicts_dir / "pending.json"
    pending.write_text(json.dumps(result, indent=2))
    return result


def _load_stored_preferences(workspace: Path) -> Dict[str, str]:
    """Read memory/*.md and extract preference statements."""
    prefs = {}
    mem_dir = workspace / "memory"
    if not mem_dir.exists():
        return prefs

    for path in mem_dir.glob("*.md"):
        text = path.read_text(errors="replace")
        for line in text.splitlines():
            line = line.strip()
            # Match "X prefers Y" or "X does not want Z" patterns
            if re.search(r"\b(?:prefers?|does not want|dislikes|must not|always|never)\b", line, re.I):
                key = _extract_subject(line)
                if key:
                    prefs[key] = line.strip("- ").strip()
    return prefs


def _extract_preferences(text: str) -> Dict[str, str]:
    """Extract preference statements from session text."""
    prefs = {}
    lines = text.splitlines()
    for i, line in enumerate(lines):
        line = line.strip()
        # Look for assistant "Noted" confirmations
        if re.search(r"\b(?:Noted|Understood|Confirmed|I've recorded)\b", line, re.I):
            # Take previous user utterance as the preference
            if i > 0:
                prev = lines[i - 1].strip()
                key = _extract_subject(prev)
                if key:
                    prefs[key] = prev.strip("- ").strip()
        # Direct preference statements
        if re.search(r"\b(?:prefers?|does not want|dislikes|must not|always|never)\b", line, re.I):
            key = _extract_subject(line)
            if key:
                prefs[key] = line.strip("- ").strip()
    return prefs


def _extract_subject(line: str) -> str:
    """Extract the subject of a preference statement."""
    m = re.match(r"(?:User|Assistant)[:\-]?\s*(?:[A-Z][a-z]+).*?(?:prefers?|does not|dislikes|must not|always|never)", line, re.I)
    if m:
        # Extract the name from the match
        name_match = re.search(r"(?:User|Assistant)[:\-]?\s*([A-Z][a-z]+)", line, re.I)
        return name_match.group(1) if name_match else "User"
    # Fallback: first capitalized word
    m = re.search(r"[A-Z][a-z]+", line)
    return m.group(0) if m else ""


def _is_contradiction(old: str, new: str) -> bool:
    """Heuristic: are two statements contradictory?"""
    if old == new:
        return False
    # Simple keyword opposition
    opposites = [
        ("minimal", "complex"),
        ("simple", "complicated"),
        ("light", "dark"),
        ("yes", "no"),
        ("enabled", "disabled"),
        ("on", "off"),
    ]
    old_l = old.lower()
    new_l = new.lower()
    for a, b in opposites:
        if (a in old_l and b in new_l) or (b in old_l and a in new_l):
            return True
    # If old and new share <30% words and are both substantial, flag
    old_words = set(old_l.split())
    new_words = set(new_l.split())
    if len(old_words) > 0:
        overlap = len(old_words & new_words) / len(old_words)
        if overlap < 0.3 and len(new) > 15 and len(old) > 15:
            return True
    return False


if __name__ == "__main__":
    import sys
    ws = sys.argv[1] if len(sys.argv) > 1 else str(Path.home() / "hermes-workspace")
    result = detect_conflicts(ws)
    print(json.dumps(result, indent=2))
