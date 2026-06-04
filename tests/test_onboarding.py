"""Tests for the interactive onboarding flow (Phase 1, Task 1).

Run with: pytest tests/test_onboarding.py -v
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from memora.onboarding import load_profile, run_onboarding


class TestOnboarding:
    """TDD for interactive onboarding prompt, local save, and GitHub push."""

    def test_onboarding_prompts_and_saves_json(self, tmp_path: Path) -> None:
        """Onboarding should prompt for inputs and save memora.json."""
        inputs = iter(["Alice", "CEO", "https://github.com/acme/corp"])

        with patch("builtins.input", side_effect=inputs):
            with patch("memora.onboarding.subprocess.run"):
                with patch("memora.onboarding.generate_company_pr"):
                    profile = run_onboarding(hermes_home=str(tmp_path))

        assert profile["first_name"] == "Alice"
        assert profile["role"] == "CEO"
        assert profile["company_github_repo"] == "https://github.com/acme/corp"

        memora_json = tmp_path / "memora.json"
        assert memora_json.exists()
        saved = json.loads(memora_json.read_text(encoding="utf-8"))
        assert saved == profile

    def test_onboarding_creates_member_file_and_pushes(self, tmp_path: Path) -> None:
        """Onboarding should push a members/{role}-{name}.json declaration."""
        inputs = iter(["Bob", "Engineering", "https://github.com/acme/corp"])

        with patch("builtins.input", side_effect=inputs):
            with patch("memora.onboarding.subprocess.run"):
                with patch("memora.onboarding.generate_company_pr") as mock_pr:
                    run_onboarding(hermes_home=str(tmp_path))

        assert mock_pr.call_count == 1
        call_kwargs = mock_pr.call_args.kwargs

        assert call_kwargs["filename"] == "members/engineering-bob.json"
        assert call_kwargs["title"] == "Add member: Engineering - Bob"

        # Verify the content is valid JSON with the profile data
        content = call_kwargs["content"]
        parsed = json.loads(content)
        assert parsed["first_name"] == "Bob"
        assert parsed["role"] == "Engineering"
        assert parsed["company_github_repo"] == "https://github.com/acme/corp"

    def test_onboarding_rejects_empty_repo_url(self, tmp_path: Path) -> None:
        """Empty repo URL should print instructions and exit."""
        inputs = iter(["Alice", "CEO", ""])

        with patch("builtins.input", side_effect=inputs):
            with pytest.raises(SystemExit) as exc_info:
                run_onboarding(hermes_home=str(tmp_path))
        assert exc_info.value.code == 1
        assert not (tmp_path / "memora.json").exists()

    def test_onboarding_loops_until_name_and_role_provided(
        self, tmp_path: Path
    ) -> None:
        """Empty first_name or role should re-prompt until filled."""
        inputs = iter(["", "Alice", "", "CEO", "https://github.com/acme/corp"])

        with patch("builtins.input", side_effect=inputs):
            with patch("memora.onboarding.subprocess.run"):
                with patch("memora.onboarding.generate_company_pr"):
                    profile = run_onboarding(hermes_home=str(tmp_path))

        assert profile["first_name"] == "Alice"
        assert profile["role"] == "CEO"

    def test_onboarding_cleans_up_json_on_git_failure(self, tmp_path: Path) -> None:
        """Subprocess errors should remove memora.json and exit with an auth hint."""
        inputs = iter(["Alice", "CEO", "https://github.com/acme/corp"])
        memora_json = tmp_path / "memora.json"

        with patch("builtins.input", side_effect=inputs):
            with patch(
                "memora.onboarding._push_member_declaration",
                side_effect=subprocess.CalledProcessError(1, "git clone"),
            ):
                with pytest.raises(SystemExit) as exc_info:
                    run_onboarding(hermes_home=str(tmp_path))

        assert exc_info.value.code == 1
        assert not memora_json.exists()

    def test_onboarding_cleans_up_json_on_real_git_failure(
        self, tmp_path: Path
    ) -> None:
        """If git clone actually fails during _push_member_declaration, clean up."""
        inputs = iter(["Alice", "CEO", "https://github.com/acme/corp"])

        def fake_subprocess(cmd, **kwargs):
            if cmd[0] == "git" and cmd[1] == "clone":
                raise subprocess.CalledProcessError(128, cmd)
            return None

        with patch("builtins.input", side_effect=inputs):
            with patch(
                "memora.onboarding.subprocess.run", side_effect=fake_subprocess
            ):
                with pytest.raises(SystemExit) as exc_info:
                    run_onboarding(hermes_home=str(tmp_path))

        assert exc_info.value.code == 1
        assert not (tmp_path / "memora.json").exists()


class TestLoadProfile:
    """Unit tests for load_profile helper."""

    def test_load_profile_existing(self, tmp_path: Path) -> None:
        """load_profile should return parsed JSON when memora.json exists."""
        profile = {"first_name": "Charlie", "role": "GTM"}
        memora_json = tmp_path / "memora.json"
        memora_json.write_text(json.dumps(profile), encoding="utf-8")

        result = load_profile(hermes_home=str(tmp_path))
        assert result == profile

    def test_load_profile_missing(self, tmp_path: Path) -> None:
        """load_profile should return None when memora.json is missing."""
        result = load_profile(hermes_home=str(tmp_path))
        assert result is None
