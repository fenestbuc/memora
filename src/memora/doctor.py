"""Health-check CLI for Memora company brain.

Usage:
    memora-doctor
    memora-doctor --json

Exit code 0 when healthy, 1 when thresholds are breached.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .company_rules import resolve_company_memory_dir
from .http_client import HttpClient, HttpConfig

# Thresholds
_PENDING_VECTOR_SYNC_WARN = 50
_REPO_SYNC_LAG_WARN_SECONDS = 4 * 3600  # 4 hours
_QUEUE_DEPTH_WARN = 100
_FAILED_QUEUE_WARN = 10


def _load_queue_counts(hermes_home: Path) -> dict[str, int]:
    """Count queued and failed facts across all agent identity queue DBs."""
    queued = 0
    failed = 0
    for db_path in hermes_home.glob("memora_queue_*.db"):
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            with conn:
                row = conn.execute("SELECT COUNT(*) FROM queue").fetchone()
                queued += row[0] if row else 0
                row = conn.execute("SELECT COUNT(*) FROM failed_queue").fetchone()
                failed += row[0] if row else 0
        except Exception:
            continue
    return {"queued": queued, "failed": failed}


def _repo_sync_lag_seconds(company_dir: Path | None) -> int | None:
    """Return seconds since the last commit in the local company repo."""
    if not company_dir or not (company_dir / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%ct"],
            cwd=company_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        last_ts = int(result.stdout.strip())
        return int(datetime.now(timezone.utc).timestamp()) - last_ts
    except Exception:
        return None


def run_doctor(*, json_output: bool = False) -> int:
    """Run health checks and print a report."""
    hermes_home = Path.home() / ".hermes"
    base_url = os.environ.get("RAG_WORKER_URL", "").rstrip("/")
    token = os.environ.get("RAG_AUTH_TOKEN", "")

    checks: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "worker": {"ok": False, "error": "RAG_WORKER_URL not configured"},
        "stats": {"ok": False},
        "queue": {"ok": True, "queued": 0, "failed": 0},
        "repo_sync_lag_seconds": None,
    }

    healthy = True

    if base_url and token:
        client = HttpClient(HttpConfig(base_url=base_url, token=token))

        try:
            health = client.get("/health")
            checks["worker"] = {"ok": True, **health}
        except Exception as e:
            checks["worker"] = {"ok": False, "error": str(e)}
            healthy = False

        try:
            stats = client.get("/memory/stats")
            checks["stats"] = {"ok": True, **stats}
            pending = stats.get("pending_vector_sync", 0)
            if pending > _PENDING_VECTOR_SYNC_WARN:
                checks["stats"]["warning"] = (
                    f"pending_vector_sync ({pending}) exceeds warning threshold ({_PENDING_VECTOR_SYNC_WARN})"
                )
                healthy = False
        except Exception as e:
            checks["stats"] = {"ok": False, "error": str(e)}
            healthy = False
    else:
        healthy = False

    queue_counts = _load_queue_counts(hermes_home)
    checks["queue"].update(queue_counts)
    if queue_counts["queued"] > _QUEUE_DEPTH_WARN:
        checks["queue"]["warning"] = (
            f"local queue depth ({queue_counts['queued']}) exceeds threshold ({_QUEUE_DEPTH_WARN})"
        )
        healthy = False
    if queue_counts["failed"] > _FAILED_QUEUE_WARN:
        checks["queue"]["warning"] = (
            f"local failed queue ({queue_counts['failed']}) exceeds threshold ({_FAILED_QUEUE_WARN})"
        )
        healthy = False

    company_dir = resolve_company_memory_dir(None)
    lag = _repo_sync_lag_seconds(company_dir)
    checks["repo_sync_lag_seconds"] = lag
    if lag is not None and lag > _REPO_SYNC_LAG_WARN_SECONDS:
        checks["repo_sync_warning"] = (
            f"company repo last commit was {lag // 3600} hours ago"
        )
        healthy = False

    if json_output:
        print(json.dumps(checks, indent=2))
    else:
        print("Memora doctor report")
        print("=" * 40)
        print(f"Worker: {'ok' if checks['worker']['ok'] else 'NOT OK'}")
        if not checks["worker"]["ok"]:
            print(f"  Error: {checks['worker'].get('error')}")
        else:
            print(f"  Version: {checks['worker'].get('version', 'unknown')}")
            print(f"  Models: {checks['worker'].get('models', {})}")

        print(f"Stats: {'ok' if checks['stats']['ok'] else 'NOT OK'}")
        if checks["stats"]["ok"]:
            print(f"  Total facts: {checks['stats'].get('total', 'unknown')}")
            print(f"  Pending vector sync: {checks['stats'].get('pending_vector_sync', 'unknown')}")
            if "warning" in checks["stats"]:
                print(f"  Warning: {checks['stats']['warning']}")
        else:
            print(f"  Error: {checks['stats'].get('error')}")

        print(f"Local queue: {checks['queue']['queued']} queued, {checks['queue']['failed']} failed")
        if "warning" in checks["queue"]:
            print(f"  Warning: {checks['queue']['warning']}")

        if checks["repo_sync_lag_seconds"] is not None:
            hours = checks["repo_sync_lag_seconds"] // 3600
            print(f"Company repo sync lag: {hours}h")
            if "repo_sync_warning" in checks:
                print(f"  Warning: {checks['repo_sync_warning']}")
        else:
            print("Company repo sync lag: unknown (repo not found)")

        print("=" * 40)
        print("Overall:", "healthy" if healthy else "issues detected")

    return 0 if healthy else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Check Memora company brain health")
    parser.add_argument("--json", action="store_true", help="Output JSON report")
    args = parser.parse_args()
    raise SystemExit(run_doctor(json_output=args.json))


if __name__ == "__main__":
    main()
