"""Tests for company rule loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from memora.company_rules import load_company_rules, resolve_company_memory_dir


def test_load_company_rules_reads_known_files(tmp_path: Path) -> None:
    (tmp_path / "_brain-filing-rules.md").write_text("# Filing\n\nUse categories.", encoding="utf-8")
    (tmp_path / "_output-rules.md").write_text("# Output\n\nCite sources.", encoding="utf-8")
    (tmp_path / "_excluded-people.md").write_text("Alice", encoding="utf-8")

    rules = load_company_rules(tmp_path)

    assert "# Company brain rules" in rules
    assert "Use categories" in rules
    assert "Cite sources" in rules
    assert "Alice" in rules


def test_load_company_rules_skips_missing(tmp_path: Path) -> None:
    (tmp_path / "_brain-filing-rules.md").write_text("# Filing", encoding="utf-8")

    rules = load_company_rules(tmp_path)

    assert "_brain-filing-rules.md" in rules
    assert "_output-rules.md" not in rules


def test_load_company_rules_returns_empty_for_missing_dir(tmp_path: Path) -> None:
    rules = load_company_rules(tmp_path / "does-not-exist")
    assert rules == ""


def test_resolve_company_memory_dir_from_config(tmp_path: Path) -> None:
    config = {"custom": {"company_memory_dir": str(tmp_path)}}
    assert resolve_company_memory_dir(config) == tmp_path


def test_resolve_company_memory_dir_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    default = tmp_path / "hermes-workspace" / "company-memory"
    default.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert resolve_company_memory_dir({}) == default
