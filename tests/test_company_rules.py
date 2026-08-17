"""Tests for company rule loading and provider integration."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

from memora.company_rules import load_company_rules, resolve_company_memory_dir
from memora.plugin import MemoraProvider


BRAIN_RULES = "# Filing\n\nUse categories."
COMPANY_MEMORY_RULES = "# Company memory\n\nUse company scope for shared facts."
OUTPUT_RULES = "# Output\n\nCite sources."


def test_load_company_rules_reads_known_files(tmp_path: Path) -> None:
    (tmp_path / "_brain-filing-rules.md").write_text(BRAIN_RULES, encoding="utf-8")
    (tmp_path / "_company-memory-rules.md").write_text(COMPANY_MEMORY_RULES, encoding="utf-8")
    (tmp_path / "_output-rules.md").write_text(OUTPUT_RULES, encoding="utf-8")
    (tmp_path / "_excluded-people.md").write_text("Alice", encoding="utf-8")

    rules = load_company_rules(tmp_path)

    assert "# Company brain rules" in rules
    assert "Use categories" in rules
    assert "Use company scope for shared facts" in rules
    assert "Cite sources" in rules
    assert "Alice" in rules


def test_load_company_rules_company_memory_file_is_optional(tmp_path: Path) -> None:
    (tmp_path / "_brain-filing-rules.md").write_text(BRAIN_RULES, encoding="utf-8")

    rules = load_company_rules(tmp_path)

    assert "_brain-filing-rules.md" in rules
    assert "_company-memory-rules.md" not in rules


def test_load_company_rules_skips_missing(tmp_path: Path) -> None:
    (tmp_path / "_brain-filing-rules.md").write_text("# Filing", encoding="utf-8")

    rules = load_company_rules(tmp_path)

    assert "_brain-filing-rules.md" in rules
    assert "_output-rules.md" not in rules


def test_load_company_rules_returns_empty_for_missing_dir(tmp_path: Path) -> None:
    rules = load_company_rules(tmp_path / "does-not-exist")
    assert rules == ""


def test_load_company_rules_returns_empty_for_none() -> None:
    assert load_company_rules(None) == ""


def test_resolve_company_memory_dir_from_config(tmp_path: Path) -> None:
    config = {"custom": {"company_memory_dir": str(tmp_path)}}
    assert resolve_company_memory_dir(config) == tmp_path


def test_resolve_company_memory_dir_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    default = tmp_path / "hermes-workspace" / "company-memory"
    default.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert resolve_company_memory_dir({}) == default


def test_resolve_company_memory_dir_no_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert resolve_company_memory_dir({}) is None


def test_memora_provider_system_prompt_includes_company_rules() -> None:
    """Unit test: system_prompt_block loads rules without network calls."""
    tmpdir = Path(tempfile.mkdtemp())
    try:
        (tmpdir / "_brain-filing-rules.md").write_text(BRAIN_RULES, encoding="utf-8")
        (tmpdir / "_company-memory-rules.md").write_text(COMPANY_MEMORY_RULES, encoding="utf-8")

        provider = MemoraProvider()
        provider._company_memory_dir = tmpdir

        prompt = provider.system_prompt_block()

        assert "Use categories" in prompt
        assert "Use company scope for shared facts" in prompt
        assert "memora_add" in prompt
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_memora_provider_system_prompt_falls_back_when_no_rules() -> None:
    provider = MemoraProvider()
    provider._company_memory_dir = None

    prompt = provider.system_prompt_block()

    assert "memora_add" in prompt
    assert "# Company brain rules" not in prompt


@pytest.mark.e2e
def test_memora_provider_initializes_with_company_rules_from_config() -> None:
    """E2E test: initialize resolves company dir and includes rules in system prompt.

    Uses the live RAG worker from RAG_WORKER_URL / RAG_AUTH_TOKEN.
    """
    url = os.environ.get("RAG_WORKER_URL", "")
    token = os.environ.get("RAG_AUTH_TOKEN", "")
    if not url or not token or "YOUR_" in token or "..." in token:
        pytest.skip("Live RAG worker credentials not available")

    hermes_home = Path(tempfile.mkdtemp())
    company_dir = Path(tempfile.mkdtemp())
    provider = MemoraProvider()
    try:
        (company_dir / "_brain-filing-rules.md").write_text(BRAIN_RULES, encoding="utf-8")
        (company_dir / "_company-memory-rules.md").write_text(COMPANY_MEMORY_RULES, encoding="utf-8")

        provider.initialize(
            session_id="test_company_rules_e2e",
            hermes_home=str(hermes_home),
            agent_identity="test_company_rules",
            config={"custom": {"company_memory_dir": str(company_dir)}},
        )

        assert provider._company_memory_dir == company_dir

        prompt = provider.system_prompt_block()
        assert "Use categories" in prompt
        assert "Use company scope for shared facts" in prompt
        assert "# Company brain rules" in prompt
    finally:
        provider.shutdown()
        shutil.rmtree(hermes_home, ignore_errors=True)
        shutil.rmtree(company_dir, ignore_errors=True)


@pytest.mark.e2e
def test_memora_provider_uses_default_company_memory_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    """E2E test: default company memory dir is resolved from config when not provided."""
    url = os.environ.get("RAG_WORKER_URL", "")
    token = os.environ.get("RAG_AUTH_TOKEN", "")
    if not url or not token or "YOUR_" in token or "..." in token:
        pytest.skip("Live RAG worker credentials not available")

    hermes_home = Path(tempfile.mkdtemp())
    default_company = hermes_home / "hermes-workspace" / "company-memory"
    default_company.mkdir(parents=True)
    (default_company / "_brain-filing-rules.md").write_text(BRAIN_RULES, encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: hermes_home)

    provider = MemoraProvider()
    try:
        provider.initialize(
            session_id="test_company_rules_default",
            hermes_home=str(hermes_home),
            agent_identity="test_company_rules_default",
            config={},
        )

        assert provider._company_memory_dir == default_company
        assert "Use categories" in provider.system_prompt_block()
    finally:
        provider.shutdown()
        shutil.rmtree(hermes_home, ignore_errors=True)
