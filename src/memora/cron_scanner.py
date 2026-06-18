"""Per-person cron scanner for the Memora company brain.

Reads markdown files under ``crons/<role>-<name>/`` in the company memory repo.
Each file starts with a simple YAML-like frontmatter block:

---
schedule: "0 9 * * 1"
owner: alice
scope: company
prompt: "Summarize last week's customer activity."
---

Schedules use standard cron syntax with five fields:
`minute hour day month weekday` (0 or 7 = Sunday).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


@dataclass
class CronJob:
    path: Path
    owner: str
    schedule: str
    prompt: str
    scope: str = "company"
    role: str = ""


def _parse_frontmatter(text: str) -> dict[str, Any]:
    """Parse a minimal YAML-like frontmatter block."""
    text = text.strip()
    if not text.startswith("---"):
        return {}
    end = text.find("---", 3)
    if end == -1:
        return {}
    block = text[3:end].strip()
    data: dict[str, Any] = {}
    for line in block.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        data[key] = value
    return data


def _field_matches(value: int, pattern: str) -> bool:
    """Return True if *value* matches a single cron field pattern."""
    if pattern == "*":
        return True

    # Step syntax: */15 or 1-30/5
    if "/" in pattern:
        base, step = pattern.split("/", 1)
        step = int(step)
        if step == 0:
            return False
        if base == "*":
            return value % step == 0
        start, end = map(int, base.split("-"))
        if not (start <= value <= end):
            return False
        return (value - start) % step == 0

    # Range: 1-5
    if "-" in pattern:
        start, end = map(int, pattern.split("-", 1))
        return start <= value <= end

    # List: 1,3,5
    if "," in pattern:
        return value in {int(x) for x in pattern.split(",")}

    return value == int(pattern)


def _expand_weekday(pattern: str) -> set[int]:
    """Expand a cron weekday pattern into canonical 0=Sunday..6=Saturday values.

    Both 0 and 7 are accepted as Sunday.
    """
    if pattern == "*":
        return set(range(7))

    step = 1
    base = pattern
    if "/" in pattern:
        base, step_s = pattern.split("/", 1)
        step = int(step_s)
        if step == 0:
            return set()

    raw_values: set[int] = set()
    for token in base.split(","):
        if token == "*":
            raw_values.update(range(8))  # include 7 so Sunday is mapped
        elif "-" in token:
            start, end = map(int, token.split("-", 1))
            raw_values.update(range(start, end + 1))
        else:
            raw_values.add(int(token))

    # Project 7 to 0
    canonical = {v % 7 for v in raw_values}
    # Apply step on sorted canonical values
    if step > 1:
        sorted_values = sorted(canonical)
        canonical = {sorted_values[i] for i in range(0, len(sorted_values), step)}
    return canonical


def cron_matches(dt: datetime, expr: str) -> bool:
    """Return True if *dt* matches the five-field cron expression."""
    fields = expr.strip().split()
    if len(fields) != 5:
        return False
    minute, hour, day, month, weekday = fields
    cron_weekday = (dt.weekday() + 1) % 7

    return (
        _field_matches(dt.minute, minute)
        and _field_matches(dt.hour, hour)
        and _field_matches(dt.day, day)
        and _field_matches(dt.month, month)
        and cron_weekday in _expand_weekday(weekday)
    )


def iter_cron_jobs(company_dir: str | Path | None) -> Iterable[CronJob]:
    """Yield cron jobs defined in ``company_dir/crons/``."""
    if not company_dir:
        return

    crons_dir = Path(company_dir).expanduser() / "crons"
    if not crons_dir.exists():
        return

    for member_dir in crons_dir.iterdir():
        if not member_dir.is_dir():
            continue
        match = re.match(r"^([a-z]+)-([a-z0-9_-]+)$", member_dir.name)
        if not match:
            continue
        role, name = match.groups()
        for file_path in member_dir.glob("*.md"):
            text = file_path.read_text(encoding="utf-8")
            front = _parse_frontmatter(text)
            schedule = front.get("schedule")
            prompt = front.get("prompt")
            owner = front.get("owner", name)
            scope = front.get("scope", "company")
            if not schedule or not prompt:
                continue
            yield CronJob(
                path=file_path,
                owner=owner,
                role=role,
                schedule=schedule,
                prompt=prompt,
                scope=scope,
            )


def due_jobs(company_dir: str | Path | None, now: datetime | None = None) -> list[CronJob]:
    """Return all cron jobs whose schedule matches the current minute."""
    now = now or datetime.now(timezone.utc)
    return [job for job in iter_cron_jobs(company_dir) if cron_matches(now, job.schedule)]


def run_cron_job(job: CronJob, runner: Callable[[dict[str, Any]], Any]) -> Any:
    """Execute a single cron job by calling the provided runner."""
    payload = {
        "query": job.prompt,
        "top_k": 10,
        "owner_id": job.owner,
        "scope": job.scope,
    }
    return runner(payload)
