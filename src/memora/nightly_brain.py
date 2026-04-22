#!/usr/bin/env python3
"""Nightly brain maintenance — runs indexer, wiki ingest, and conflict detection.

Produces a summary JSON report for observability.
"""

import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

# Default workspace path; override with first CLI arg
_workspace = str(Path.home() / "memora-workspace")
_scripts = Path.home() / "memora-workspace" / "scripts"
sys.path.insert(0, str(_scripts))

from memora.brain_indexer import scan_workspace, index_sessions
from memora.wiki_ingester import init_wiki, ingest_session, lint_wiki
from memora.conflict_detector import detect_conflicts

_MAX_RETRIES = 2


def _run_step(name: str, fn, *args, retries: int = _MAX_RETRIES):
    """Run a step with retry. Returns (result_dict, error_or_none)."""
    for attempt in range(retries + 1):
        try:
            result = fn(*args)
            return {"status": "ok", "result": result, "attempts": attempt + 1}, None
        except Exception as e:
            if attempt == retries:
                return {"status": "failed", "error": str(e), "attempts": attempt + 1}, e
    return {"status": "failed", "error": "unknown", "attempts": retries + 1}, None


def main(workspace_path: str | None = None):
    workspace = workspace_path or _workspace
    print("=== Nightly Brain Maintenance ===")
    start = datetime.now(timezone.utc)
    report = {
        "started": start.isoformat(),
        "workspace": workspace,
        "steps": {},
    }

    # 1. Index workspace
    print("[1/4] Indexing workspace...")
    r1, e1 = _run_step("scan_workspace", scan_workspace, workspace)
    report["steps"]["scan_workspace"] = r1
    if e1:
        traceback.print_exc()

    r2, e2 = _run_step("index_sessions", index_sessions, workspace)
    report["steps"]["index_sessions"] = r2
    if e2:
        traceback.print_exc()

    # 2. Ingest sessions into wiki
    print("[2/4] Ingesting sessions into wiki...")
    wiki = Path(workspace) / "wiki"
    init_wiki(str(wiki))

    sessions_dir = Path(workspace) / "sessions"
    ingested = 0
    skipped = 0
    if sessions_dir.exists():
        for sess in sorted(sessions_dir.glob("*.md")):
            r, e = _run_step("ingest", ingest_session, str(wiki), str(sess), retries=0)
            if r["status"] == "ok":
                ingested += 1
            else:
                skipped += 1
                print(f"  Skip {sess.name}: {r.get('error')}")

    report["steps"]["wiki_ingest"] = {"ingested": ingested, "skipped": skipped}

    # 3. Detect conflicts
    print("[3/4] Detecting conflicts...")
    r3, e3 = _run_step("detect_conflicts", detect_conflicts, workspace)
    report["steps"]["detect_conflicts"] = r3
    if e3:
        traceback.print_exc()

    # 4. Lint for broken wikilinks
    print("[4/4] Linting wiki...")
    broken = lint_wiki(str(wiki))
    report["steps"]["lint_wiki"] = {"broken_links": broken, "count": len(broken)}
    if broken:
        print(f"  Found {len(broken)} broken wikilink(s)")

    # Write report
    report["finished"] = datetime.now(timezone.utc).isoformat()
    report_dir = Path(workspace) / ".second-brain" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"nightly_{start.strftime('%Y%m%d_%H%M%S')}.json"
    report_path.write_text(json.dumps(report, indent=2))
    print(f"  Report: {report_path}")

    # Summary
    failed = [k for k, v in report["steps"].items() if v.get("status") == "failed"]
    if failed:
        print(f"  FAILED steps: {failed}")
        sys.exit(1)
    print("  All clear.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
