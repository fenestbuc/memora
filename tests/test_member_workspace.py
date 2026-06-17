"""Tests for member workspace scaffolding."""

from __future__ import annotations

import json

import pytest

from memora.member_workspace import build_member_files


def test_build_member_files_scaffolds_workspace() -> None:
    profile = {"first_name": "Alice", "role": "Sales", "company_github_repo": "https://example.com/repo"}
    files = build_member_files(profile)

    assert "members/sales-alice.json" in files
    assert "members/sales-alice/USER.md" in files
    assert "members/sales-alice/concepts/README.md" in files
    assert "members/sales-alice/customers/README.md" in files
    assert "members/sales-alice/meetings/README.md" in files
    assert "members/sales-alice/sources/README.md" in files

    metadata = json.loads(files["members/sales-alice.json"])
    assert metadata["first_name"] == "Alice"
    assert metadata["role"] == "Sales"

    user_md = files["members/sales-alice/USER.md"]
    assert "Alice" in user_md
    assert "Sales" in user_md


def test_build_member_files_slugifies_spaces() -> None:
    profile = {"first_name": "Bob Smith", "role": "Product Manager", "company_github_repo": ""}
    files = build_member_files(profile)

    assert "members/product-manager-bob-smith/USER.md" in files
    assert any(p.endswith(".json") for p in files)
