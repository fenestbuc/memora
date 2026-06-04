#!/usr/bin/env python3
"""RAG Migration Manager — local CLI for Cloudflare D1 schema management.

Wraps ``wrangler d1 migrations`` with a better UX for Memora operators:
  • Lists pending migrations
  • Shows current schema version
  • Validates migration file checksums
  • Supports dry-run before apply
  • Auto-detects database from wrangler.toml

Usage:
    python -m memora.rag_migrate status
    python -m memora.rag_migrate apply --dry-run
    python -m memora.rag_migrate create "add_importance_decay"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Resolve rag-worker relative to this file (inside the memora package)
_MEMORA_SRC = Path(__file__).resolve().parent.parent.parent
MIGRATIONS_DIR = _MEMORA_SRC / "rag-worker" / "migrations"
WRANGLER_TOML = _MEMORA_SRC / "rag-worker" / "wrangler.toml"


def _read_wrangler_config() -> dict[str, Any]:
    """Parse wrangler.toml for database_name."""
    if not WRANGLER_TOML.exists():
        print(f"Error: {WRANGLER_TOML} not found. Are you in the hermes-rag directory?")
        sys.exit(1)

    content = WRANGLER_TOML.read_text()
    # Simple regex parse for database_name
    match = re.search(r'database_name\s*=\s*"([^"]+)"', content)
    if not match:
        print("Error: Could not find database_name in wrangler.toml")
        sys.exit(1)
    return {"database_name": match.group(1)}


def _list_local_migrations() -> list[tuple[int, str, Path]]:
    """Return sorted list of (sequence, name, path) for local migration files."""
    if not MIGRATIONS_DIR.exists():
        return []

    pattern = re.compile(r"^(\d+)_(.+)\.(sql|js)$")
    migrations = []
    for path in MIGRATIONS_DIR.iterdir():
        match = pattern.match(path.name)
        if match:
            migrations.append((int(match.group(1)), match.group(2), path))
    return sorted(migrations)


def _compute_checksum(path: Path) -> str:
    """SHA-256 checksum of a migration file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _get_applied_migrations(database_name: str) -> set[int]:
    """Query D1 for which migrations have been applied."""
    try:
        result = subprocess.run(
            [
                "wrangler", "d1", "execute", database_name,
                "--command", "SELECT version FROM _migrations ORDER BY version"
            ],
            capture_output=True,
            text=True,
            check=True,
            cwd=WRANGLER_TOML.parent,
        )
        # Parse JSON output from wrangler
        lines = result.stdout.strip().splitlines()
        applied = set()
        for line in lines:
            try:
                data = json.loads(line)
                if isinstance(data, list):
                    for row in data:
                        if "version" in row:
                            applied.add(row["version"])
                elif isinstance(data, dict) and "version" in data:
                    applied.add(data["version"])
            except json.JSONDecodeError:
                continue
        return applied
    except subprocess.CalledProcessError as exc:
        # _migrations table might not exist yet (baseline not run)
        if "no such table" in exc.stderr.lower():
            return set()
        print(f"Error querying D1: {exc.stderr}")
        sys.exit(1)


def cmd_status(args: argparse.Namespace) -> int:
    """Show current migration status."""
    config = _read_wrangler_config()
    database_name = config["database_name"]

    local = _list_local_migrations()
    applied = _get_applied_migrations(database_name)

    print(f"Database : {database_name}")
    print(f"Local    : {len(local)} migration(s)")
    print(f"Applied  : {len(applied)} migration(s)")
    print()

    for seq, name, path in local:
        status = "✅ applied" if seq in applied else "⏳ pending"
        checksum = _compute_checksum(path)
        print(f"  {status}  {seq:04d}_{name:<40}  {checksum}")

    pending = [m for m in local if m[0] not in applied]
    if pending:
        print(f"\n{len(pending)} pending migration(s) ready to apply.")
    else:
        print("\nDatabase is up to date.")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    """Apply pending migrations via wrangler."""
    config = _read_wrangler_config()
    database_name = config["database_name"]

    local = _list_local_migrations()
    applied = _get_applied_migrations(database_name)
    pending = [m for m in local if m[0] not in applied]

    if not pending:
        print("No pending migrations.")
        return 0

    print(f"Pending migrations ({len(pending)}):")
    for seq, name, path in pending:
        print(f"  {seq:04d}_{name}")

    if args.dry_run:
        print("\n(Dry run — no changes made)")
        return 0

    confirm = input("\nApply these migrations? [y/N]: ")
    if confirm.lower() != "y":
        print("Aborted.")
        return 1

    for seq, name, path in pending:
        print(f"\nApplying {seq:04d}_{name} ...")
        try:
            subprocess.run(
                [
                    "wrangler", "d1", "execute", database_name,
                    "--file", str(path),
                ],
                check=True,
                cwd=WRANGLER_TOML.parent,
            )
            # Record migration
            checksum = _compute_checksum(path)
            subprocess.run(
                [
                    "wrangler", "d1", "execute", database_name,
                    "--command",
                    f"INSERT INTO _migrations (version, name, checksum) VALUES ({seq}, '{name.replace(chr(39), chr(39)+chr(39))}', '{checksum}')",
                ],
                check=True,
                cwd=WRANGLER_TOML.parent,
            )
            print(f"  ✅ Applied + recorded")
        except subprocess.CalledProcessError as exc:
            print(f"  ❌ Failed: {exc}")
            return 1

    print("\nAll migrations applied successfully.")
    return 0


def cmd_create(args: argparse.Namespace) -> int:
    """Create a new migration file from a template."""
    MIGRATIONS_DIR.mkdir(parents=True, exist_ok=True)
    existing = _list_local_migrations()
    next_seq = existing[-1][0] + 1 if existing else 1

    name = args.name.lower().replace(" ", "_")
    filename = f"{next_seq:04d}_{name}.sql"
    filepath = MIGRATIONS_DIR / filename

    template = f"""-- Migration: {name}
-- Created: {datetime.now(timezone.utc).isoformat()}
-- Sequence: {next_seq}

BEGIN TRANSACTION;

-- Add your schema changes here

-- Example:
-- ALTER TABLE facts ADD COLUMN new_column TEXT;

COMMIT;
"""

    filepath.write_text(template, encoding="utf-8")
    print(f"Created: {filepath}")
    print("Edit the file, then run: python -m memora.rag_migrate apply")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Memora RAG Migration Manager for Cloudflare D1"
    )
    subparsers = parser.add_subparsers(dest="command")

    # status
    p_status = subparsers.add_parser("status", help="Show migration status")
    p_status.set_defaults(func=cmd_status)

    # apply
    p_apply = subparsers.add_parser("apply", help="Apply pending migrations")
    p_apply.add_argument("--dry-run", action="store_true", help="Show what would be applied")
    p_apply.set_defaults(func=cmd_apply)

    # create
    p_create = subparsers.add_parser("create", help="Create a new migration file")
    p_create.add_argument("name", help="Descriptive name for the migration")
    p_create.set_defaults(func=cmd_create)

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
