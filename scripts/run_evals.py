#!/usr/bin/env python3
"""Standalone wrapper for the Memora evaluation runner.

Usage:
    python scripts/run_evals.py --golden data/eval_golden.jsonl
"""

import sys
from pathlib import Path

# Ensure src/ is on path when run directly from repo root
_repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo_root / "src"))

from memora.onboarding import load_profile
from memora.optimizer import run_optimization_loop
from memora.run_evals import run_evaluations

if __name__ == "__main__":
    exit_code, report = run_evaluations()

    profile = load_profile()
    if profile and profile.get("role") == "CEO" and report is not None:
        run_optimization_loop(report.swarm_accuracy)

    sys.exit(exit_code)
