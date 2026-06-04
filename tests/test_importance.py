"""Tests for importance scoring."""

from __future__ import annotations

import pytest

from memora.importance import _parse_score, _heuristic_importance


class TestParseScore:
    def test_extracts_float(self) -> None:
        assert _parse_score("Score: 0.75") == 0.75

    def test_missing_returns_default(self) -> None:
        assert _parse_score("No score here") == 0.5

    def test_out_of_range_skipped(self) -> None:
        # Values outside [0, 1] are skipped; when no valid tokens exist, default 0.5 returned
        assert _parse_score("1.5") == 0.5
        assert _parse_score("-0.3") == 0.5

    def test_valid_token_extracted(self) -> None:
        assert _parse_score("0.95") == 0.95


class TestHeuristicImportance:
    def test_critical_keyword_boosts(self) -> None:
        score = _heuristic_importance("This is critical for the business", "memory")
        assert score > 0.5

    def test_trivial_content_low_score(self) -> None:
        score = _heuristic_importance("ok thanks", "memory")
        assert score <= 0.5

    def test_length_bonus(self) -> None:
        short = _heuristic_importance("ok", "memory")
        long_text = _heuristic_importance(
            "This is a very detailed explanation of why we should implement the new feature with proper testing and documentation. " * 10,
            "memory",
        )
        assert long_text > short
