"""Autonomous prompt optimization loop for the Memora CEO node.

Spawns an OpenCode subagent to mutate prompts when evaluation scores
fall below the accuracy threshold.
"""

from __future__ import annotations

import json
import logging
import py_compile
import subprocess
from pathlib import Path

from memora import swarm_manager

logger = logging.getLogger("memora.optimizer")

THRESHOLD = 0.95
STATE_PATH = Path.home() / ".hermes" / "optimizer_state.json"
FAILURE_LIMIT = 3


def _load_failure_count() -> int:
    """Return the stored failure count, defaulting to 0."""
    if not STATE_PATH.exists():
        return 0
    try:
        data = json.loads(STATE_PATH.read_text())
        return int(data.get("failure_count", 0))
    except (json.JSONDecodeError, OSError, ValueError):
        return 0


def _save_failure_count(count: int) -> None:
    """Persist ``count`` to the state JSON file."""
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps({"failure_count": count}, indent=2))
    except OSError:
        logger.warning("Could not write optimizer state to %s", STATE_PATH)


def _escalate_to_backend_eng() -> None:
    """Create a Kanban task assigned to ``backend-eng``."""
    logger.warning(
        "Optimizer failed %d consecutive times; escalating to backend-eng.",
        FAILURE_LIMIT,
    )
    if swarm_manager.kanban_create is None:
        logger.warning("kanban_create unavailable; cannot escalate.")
        return
    try:
        swarm_manager.kanban_create(
            title="[optimizer] Consecutive failures exceeded threshold",
            body=(
                "The Memora optimizer has failed 3 consecutive times "
                "and requires manual intervention."
            ),
            tags=["optimizer", "escalation", "backend-eng"],
            assignee="backend-eng",
        )
    except Exception as exc:  # pragma: no cover
        logger.error("Kanban escalation failed: %s", exc)


def run_optimization_loop(current_score: float) -> None:
    """Run the OpenCode optimization loop if score is below threshold.

    If ``current_score < THRESHOLD``, this function:
    1. Constructs an OpenCode prompt referencing the golden dataset and
       ``src/memora/prompts.py``.
    2. Runs ``opencode run`` via subprocess to mutate the prompt file.
    3. Re-runs ``scripts/run_evals.py`` to verify improvement.
    4. On failure, increments the failure count stored in
       ``~/.hermes/optimizer_state.json``.  After 3 consecutive
       failures a Kanban task is escalated to ``backend-eng``.
    5. On success, resets the failure count to 0.

    Args:
        current_score: The current evaluation accuracy (0.0–1.0).
    """
    if current_score >= THRESHOLD:
        logger.info(
            "Score %.3f meets threshold %.3f; skipping optimization.",
            current_score,
            THRESHOLD,
        )
        _save_failure_count(0)
        return

    prompts_path = Path("src/memora/prompts.py")
    if not prompts_path.exists():
        logger.warning(
            "Prompts file not found at %s; skipping optimization.",
            prompts_path,
        )
        return

    failure_count = _load_failure_count()

    opencode_prompt = (
        f"The Kanban routing accuracy is {current_score:.2f}. "
        "Here is the golden dataset. "
        f"Modify {prompts_path} to fix the failing test cases."
    )

    logger.info(
        "Score %.3f below threshold %.3f; spawning OpenCode optimization...",
        current_score,
        THRESHOLD,
    )

    result = subprocess.run(
        f'echo "" | opencode run {opencode_prompt}',
        capture_output=True,
        text=True,
        shell=True,
    )

    if result.returncode != 0:
        logger.error("OpenCode optimization failed: %s", result.stderr)
        failure_count += 1
        _save_failure_count(failure_count)
        if failure_count >= FAILURE_LIMIT:
            _escalate_to_backend_eng()
        return

    logger.info("OpenCode optimization completed. Re-evaluating...")

    try:
        py_compile.compile("src/memora/prompts.py", doraise=True)
    except py_compile.PyCompileError:
        logger.error("AST Check Failed")
        subprocess.run(
            ["git", "checkout", "--", str(prompts_path)],
            capture_output=True,
        )
        failure_count += 1
        _save_failure_count(failure_count)
        if failure_count >= FAILURE_LIMIT:
            _escalate_to_backend_eng()
        return

    eval_result = subprocess.run(
        ["python", "scripts/run_evals.py"],
        capture_output=True,
        text=True,
    )

    if eval_result.returncode != 0:
        logger.error(
            "Post-optimization evaluation failed; reverting %s",
            prompts_path,
        )
        subprocess.run(
            ["git", "checkout", "--", str(prompts_path)],
            capture_output=True,
        )
        failure_count += 1
        _save_failure_count(failure_count)
        if failure_count >= FAILURE_LIMIT:
            _escalate_to_backend_eng()
        return

    logger.info("Post-optimization evaluation succeeded.")
    _save_failure_count(0)
