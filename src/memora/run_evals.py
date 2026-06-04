#!/usr/bin/env python3
"""Standalone Memora evaluation runner.

Executes the full evaluation suite (CEO Digest quality, Swarm trigger
accuracy, and comprehensive RAG metrics) and emits a structured report
to stdout and an optional JSON file.

Usage:
    python -m memora.run_evals --golden data/eval_golden.jsonl
    memora-evals --help
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from memora.evaluations import run_full_evaluation
from memora.plugin import MemoraProvider

logger = logging.getLogger("memora.run_evals")

_DEFAULT_GOLDEN = [
    {
        "query": "What is NavDhan's primary business model?",
        "relevant_ids": ["fact_navdhan_b2b"],
    },
    {
        "query": "Who are the key investors for Kubar Labs?",
        "relevant_ids": ["fact_kubar_investors"],
    },
]


def _load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_jsonl(path: str) -> List[Dict[str, Any]]:
    """Load a JSONL file (line-delimited JSON objects) into a list."""
    records: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _save_json(path: str, data: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the full Memora evaluation suite.",
    )
    parser.add_argument(
        "--golden",
        type=str,
        default="",
        help="Path to golden dataset JSONL file (list of query/relevant_ids).",
    )
    parser.add_argument(
        "--digest",
        type=str,
        default="",
        help="CEO digest text to evaluate. Alternatively --digest-file.",
    )
    parser.add_argument(
        "--digest-file",
        type=str,
        default="",
        help="Path to a file containing the CEO digest text.",
    )
    parser.add_argument(
        "--prs",
        type=str,
        default="",
        help="Path to open PRs JSON file for CEO digest eval.",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default="",
        help="Path to write the JSON report (e.g. reports/eval_20240101.json).",
    )
    parser.add_argument(
        "--worker-url",
        type=str,
        default=os.environ.get("RAG_WORKER_URL", ""),
        help="RAG worker base URL (default $RAG_WORKER_URL).",
    )
    parser.add_argument(
        "--token",
        type=str,
        default=os.environ.get("RAG_AUTH_TOKEN", ""),
        help="RAG worker auth token (default $RAG_AUTH_TOKEN).",
    )
    parser.add_argument(
        "--k",
        type=int,
        default=10,
        help="Cut-off rank for RAG metrics (default 10).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress non-error console output.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    return parser


def run_evaluations(
    argv: List[str] | None = None,
) -> tuple[int, EvaluationReport | None]:
    """Run the full evaluation suite and return the exit code plus report.

    Args:
        argv: Optional CLI arguments. ``None`` falls back to ``sys.argv[1:]``.

    Returns:
        A tuple of ``(exit_code, report)``.  *report* may be ``None`` when
        CLI argument parsing or dataset loading fails before evaluation runs.
    """
    parser = _build_argument_parser()
    args = parser.parse_args(argv)

    level = logging.DEBUG if args.verbose else (logging.WARNING if args.quiet else logging.INFO)
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")

    # Load golden dataset
    golden_dataset: List[Dict[str, Any]] | None = None
    if args.golden:
        try:
            golden_dataset = _load_jsonl(args.golden)
            if not isinstance(golden_dataset, list):
                logger.error("Golden dataset must be a JSON list")
                return 1, None
        except Exception as exc:
            logger.error("Failed to load golden dataset: %s", exc)
            return 1, None
    else:
        logger.info("No --golden provided; using built-in minimal dataset")
        golden_dataset = list(_DEFAULT_GOLDEN)

    # Load CEO digest
    ceo_digest_text = args.digest
    if args.digest_file:
        try:
            ceo_digest_text = Path(args.digest_file).read_text(encoding="utf-8")
        except Exception as exc:
            logger.error("Failed to read digest file: %s", exc)
            return 1, None

    # Load open PRs
    open_prs: List[dict] | None = None
    if args.prs:
        try:
            open_prs = _load_json(args.prs)
            if not isinstance(open_prs, list):
                logger.error("PR file must contain a JSON list")
                return 1, None
        except Exception as exc:
            logger.error("Failed to load PRs file: %s", exc)
            return 1, None

    # Configure provider
    provider = MemoraProvider()
    if args.worker_url:
        provider.worker_url = args.worker_url.rstrip("/")
    if args.token:
        provider.worker_token = args.token

    # Validate connectivity when URL/token supplied
    if provider.worker_url and provider.worker_token:
        logger.debug("Using RAG worker: %s", provider.worker_url)
    else:
        logger.warning(
            "RAG worker URL or token not configured; RAG metrics may be skipped."
        )

    logger.info("Running Memora evaluation suite...")
    report = run_full_evaluation(
        provider=provider,
        golden_dataset=golden_dataset,
        ceo_digest_text=ceo_digest_text,
        open_prs=open_prs,
        trigger_fn=None,  # use default swarm_manager introspection
    )

    # Emit report
    report_dict = report.to_dict()
    summary = report.summary()

    if not args.quiet:
        print(summary)

    if args.output:
        _save_json(args.output, report_dict)
        if not args.quiet:
            print(f"\nReport written to {args.output}")

    # Exit non-zero if there were evaluation errors
    if report.errors:
        logger.error("Evaluation completed with %d error(s)", len(report.errors))
        return 1, report

    return 0, report


def main(argv: List[str] | None = None) -> int:
    """Thin wrapper around :func:`run_evaluations` for CLI entry points.

    Returns only the exit code so existing callers remain compatible.
    """
    exit_code, _ = run_evaluations(argv)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
