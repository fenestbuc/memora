#!/usr/bin/env python3
"""Weekly Memora Digest — creates a GitHub issue summarising second-brain activity.

Run via cron weekly:
    0 9 * * 0 cd ~/memora-workspace && python -m memora.weekly_digest
"""

from __future__ import annotations

import glob
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

logger = None  # lazy import to avoid circularity


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _github_api(method: str, path: str, body: dict = None) -> dict:
    """Call GitHub REST API."""
    token = _env("GITHUB_TOKEN")
    owner = _env("GITHUB_OWNER")
    repo = _env("GITHUB_REPO")
    if not token or not owner or not repo:
        raise RuntimeError("GITHUB_TOKEN, GITHUB_OWNER, GITHUB_REPO required")

    url = f"https://api.github.com/repos/{owner}/{repo}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
        method=method,
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _close_previous_digests() -> int:
    """Find and close open weekly-digest issues. Returns count closed."""
    try:
        result = _github_api(
            "GET",
            "/issues?state=open&labels=memora-digest&per_page=10",
        )
        closed = 0
        for issue in result:
            _github_api(
                "PATCH",
                f"/issues/{issue['number']}",
                {"state": "closed", "state_reason": "completed"},
            )
            closed += 1
        return closed
    except Exception as e:
        print(f"[warn] Could not close previous digests: {e}")
        return 0


def _load_reports(workspace: str) -> List[dict]:
    """Load nightly JSON reports from the past 7 days."""
    report_dir = Path(workspace) / ".second-brain" / "reports"
    if not report_dir.exists():
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    reports = []
    for path in sorted(report_dir.glob("nightly_*.json")):
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if mtime >= cutoff:
                reports.append(json.loads(path.read_text()))
        except Exception:
            continue
    return reports


def _load_baseline(workspace: str) -> dict:
    """Load last week's baseline for delta computation."""
    baseline_path = Path(workspace) / ".second-brain" / "reports" / "weekly_baseline.json"
    if baseline_path.exists():
        try:
            return json.loads(baseline_path.read_text())
        except Exception:
            pass
    return {"total_facts": 0, "total_pages": 0, "total_conflicts": 0, "total_broken": 0}


def _save_baseline(workspace: str, baseline: dict) -> None:
    baseline_path = Path(workspace) / ".second-brain" / "reports" / "weekly_baseline.json"
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = baseline_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(baseline, indent=2))
    tmp.rename(baseline_path)


def _aggregate(reports: List[dict]) -> dict:
    """Aggregate nightly reports into weekly totals."""
    total_ingested = 0
    total_skipped = 0
    total_conflicts = 0
    broken_links = []

    for r in reports:
        steps = r.get("steps", {})
        wi = steps.get("wiki_ingest", {})
        total_ingested += wi.get("ingested", 0)
        total_skipped += wi.get("skipped", 0)

        dc = steps.get("detect_conflicts", {})
        if dc.get("status") == "ok":
            res = dc.get("result", {})
            total_conflicts += res.get("total_conflicts", 0)

        lw = steps.get("lint_wiki", {})
        for link in lw.get("broken_links", []):
            if link not in broken_links:
                broken_links.append(link)

    return {
        "ingested": total_ingested,
        "skipped": total_skipped,
        "conflicts": total_conflicts,
        "broken_links": broken_links,
    }


def _fetch_rag_stats() -> dict:
    """Query RAG worker for current memory stats."""
    url = _env("RAG_WORKER_URL", "")
    token = _env("RAG_AUTH_TOKEN", "")
    if not url or not token or "YOUR_" in token:
        return {"total": "N/A", "by_category": []}

    try:
        req = urllib.request.Request(
            f"{url.rstrip('/')}/memory/stats",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"total": f"unavailable ({e})", "by_category": []}


def _format_body(week_label: str, start: str, end: str, agg: dict, baseline: dict, stats: dict) -> str:
    """Format markdown issue body."""
    # Delta computation
    def delta(current: int, previous: int) -> str:
        diff = current - previous
        if diff > 0:
            return f"+{diff}"
        elif diff < 0:
            return f"{diff}"
        return "—"

    body = f"""# Weekly Memora Digest — {week_label}

_Activity period: {start} to {end}_

## Activity Summary

| Metric | This Week | vs Last Week |
|--------|-----------|-------------|
| Wiki sessions ingested | {agg['ingested']} | {delta(agg['ingested'], baseline.get('ingested', 0))} |
| Sessions skipped | {agg['skipped']} | {delta(agg['skipped'], baseline.get('skipped', 0))} |
| Conflicts detected | {agg['conflicts']} | {delta(agg['conflicts'], baseline.get('conflicts', 0))} |
| Broken wikilinks | {len(agg['broken_links'])} | {delta(len(agg['broken_links']), baseline.get('broken', 0))} |

## RAG Memory Stats

- **Total indexed facts:** {stats.get('total', 'N/A')}
"""

    by_cat = stats.get("by_category", [])
    if by_cat:
        body += "- **Top categories:**\n"
        for row in by_cat[:5]:
            cat = row.get("category", "unknown")
            count = row.get("count", 0)
            body += f"  - {cat}: {count}\n"

    if agg["conflicts"] > 0:
        body += "\n## Conflicts Detected\n"
        body += f"_{agg['conflicts']} contradiction(s) found this week._\n"
        body += "See `.second-brain/conflicts/pending.json` for details.\n"

    if agg["broken_links"]:
        body += "\n## Broken Wikilinks\n"
        shown = agg["broken_links"][:10]
        for link in shown:
            src = link.get("source", "unknown")
            tgt = link.get("target", "unknown")
            body += f"- `{src}` links to `[[{tgt}]]`\n"
        remaining = len(agg["broken_links"]) - len(shown)
        if remaining > 0:
            body += f"- ... and {remaining} more\n"

    body += """
---
_Generated by memora weekly digest_
"""
    return body


def main(workspace_path: str | None = None):
    workspace = workspace_path or _env("MEMORA_WORKSPACE", str(Path.home() / "memora-workspace"))
    print(f"=== Weekly Memora Digest ({workspace}) ===")

    reports = _load_reports(workspace)
    if not reports:
        print("No nightly reports found for the past 7 days. Skipping.")
        return

    # Load baseline
    baseline = _load_baseline(workspace)
    agg = _aggregate(reports)

    # Fetch RAG stats
    stats = _fetch_rag_stats()

    # Week label
    now = datetime.now(timezone.utc)
    week_label = now.strftime("%Y-W%W")
    start = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    end = now.strftime("%Y-%m-%d")

    # Close previous digests
    closed = _close_previous_digests()
    if closed:
        print(f"Closed {closed} previous digest(s)")

    # Create new issue
    title = f"Weekly Memora Digest — {week_label}"
    body = _format_body(week_label, start, end, agg, baseline, stats)

    try:
        issue = _github_api("POST", "/issues", {"title": title, "body": body, "labels": ["memora-digest"]})
        print(f"Created issue: {issue['html_url']}")
    except Exception as e:
        print(f"[warn] Could not create GitHub issue: {e}")
        print("--- Issue body (would have been posted) ---")
        print(body)
        return

    # Save new baseline
    _save_baseline(workspace, {
        "week": week_label,
        "ingested": agg["ingested"],
        "skipped": agg["skipped"],
        "conflicts": agg["conflicts"],
        "broken": len(agg["broken_links"]),
        "total_facts": stats.get("total", 0),
        "saved_at": datetime.now(timezone.utc).isoformat(),
    })
    print("Updated weekly baseline.")


if __name__ == "__main__":
    import sys
    main(sys.argv[1] if len(sys.argv) > 1 else None)
