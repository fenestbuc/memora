"""Tests for the interactive onboarding flow.

Run with: pytest tests/test_onboarding.py -v
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from memora.onboarding import load_profile, run_onboarding


@pytest.fixture(autouse=True)
def _mock_prerequisites(monkeypatch):
    """Suppress prerequisite checks so tests don't need extra inputs."""
    monkeypatch.setattr("memora.onboarding._check_prerequisites", lambda: [])


class TestOnboarding:
    """TDD for interactive onboarding prompt, local save, and GitHub push."""

    def test_onboarding_prompts_and_saves_json(self, tmp_path: Path) -> None:
        """Onboarding should prompt for inputs and save memora.json with defaults."""
        # Name, Role, Repo, Kanban choice (number), Tunnel choice (number), RAG URL (empty=skip)
        inputs = iter(["Alice", "CEO", "https://github.com/acme/corp", "1", "1", ""])

        with patch("builtins.input", side_effect=inputs):
            with patch("memora.onboarding.subprocess.run"):
                with patch("memora.onboarding.generate_company_pr"):
                    profile = run_onboarding(hermes_home=str(tmp_path))

        assert profile["first_name"] == "Alice"
        assert profile["role"] == "CEO"
        assert profile["company_github_repo"] == "https://github.com/acme/corp"
        assert profile["kanban_backend"] == "hermes"
        assert profile["tunnel_provider"] == "cloudflared"

        memora_json = tmp_path / "memora.json"
        assert memora_json.exists()
        saved = json.loads(memora_json.read_text(encoding="utf-8"))
        assert saved == profile

    def test_onboarding_selects_linear_and_ngrok(self, tmp_path: Path) -> None:
        """User should be able to select Linear and ngrok via number input."""
        inputs = iter(["Bob", "Engineering", "https://github.com/acme/corp", "2", "2", ""])

        with patch("builtins.input", side_effect=inputs):
            with patch("memora.onboarding.subprocess.run"):
                with patch("memora.onboarding.generate_company_pr"):
                    profile = run_onboarding(hermes_home=str(tmp_path))

        assert profile["kanban_backend"] == "linear"
        assert profile["tunnel_provider"] == "ngrok"

    def test_onboarding_creates_member_workspace_and_pushes(self, tmp_path: Path) -> None:
        """Onboarding should push a members/{role}-{name}/ workspace."""
        inputs = iter(["Bob", "Engineering", "https://github.com/acme/corp", "hermes", "cloudflared", ""])

        with patch("builtins.input", side_effect=inputs):
            with patch("memora.onboarding.subprocess.run"):
                with patch("memora.onboarding.generate_company_pr") as mock_pr:
                    run_onboarding(hermes_home=str(tmp_path))

        assert mock_pr.call_count == 1
        call_kwargs = mock_pr.call_args.kwargs

        files = call_kwargs["files"]
        assert any("members/engineering-bob/USER.md" in p for p in files)
        assert any("members/engineering-bob.json" in p for p in files)
        assert call_kwargs["title"] == "Add member: Engineering - Bob"

        # Verify the JSON metadata is valid and contains the profile data
        json_path = next(p for p in files if p.endswith(".json"))
        parsed = json.loads(files[json_path])
        assert parsed["first_name"] == "Bob"
        assert parsed["role"] == "Engineering"
        assert parsed["company_github_repo"] == "https://github.com/acme/corp"
        assert parsed["kanban_backend"] == "hermes"

        # Verify the workspace subfolders are scaffolded
        assert any("members/engineering-bob/concepts/README.md" in p for p in files)
        assert any("members/engineering-bob/customers/README.md" in p for p in files)
        assert any("members/engineering-bob/meetings/README.md" in p for p in files)
        assert any("members/engineering-bob/sources/README.md" in p for p in files)

    def test_onboarding_rejects_empty_repo_url(self, tmp_path: Path) -> None:
        """Empty repo URL should print instructions and exit."""
        inputs = iter(["Alice", "CEO", "", ""])

        with patch("builtins.input", side_effect=inputs):
            with pytest.raises(SystemExit) as exc_info:
                run_onboarding(hermes_home=str(tmp_path))
        assert exc_info.value.code == 1
        assert not (tmp_path / "memora.json").exists()

    def test_onboarding_loops_until_name_and_role_provided(
        self, tmp_path: Path
    ) -> None:
        """Empty first_name or role should re-prompt until filled."""
        inputs = iter(["", "Alice", "", "CEO", "https://github.com/acme/corp", "1", "1", ""])

        with patch("builtins.input", side_effect=inputs):
            with patch("memora.onboarding.subprocess.run"):
                with patch("memora.onboarding.generate_company_pr"):
                    profile = run_onboarding(hermes_home=str(tmp_path))

        assert profile["first_name"] == "Alice"
        assert profile["role"] == "CEO"

    def test_onboarding_cleans_up_json_on_git_failure(self, tmp_path: Path) -> None:
        """Subprocess errors should remove memora.json and exit with an auth hint."""
        inputs = iter(["Alice", "CEO", "https://github.com/acme/corp", "1", "1", ""])
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
        inputs = iter(["Alice", "CEO", "https://github.com/acme/corp", "1", "1", ""])

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

    def test_onboarding_writes_env_helper(self, tmp_path: Path) -> None:
        """When non-default choices and RAG credentials are provided, env helper should be written."""
        inputs = iter(["Alice", "CEO", "https://github.com/acme/corp", "2", "2", "https://worker.example.com", "secret_token"])

        with patch("builtins.input", side_effect=inputs):
            with patch("memora.onboarding.subprocess.run"):
                with patch("memora.onboarding.generate_company_pr"):
                    run_onboarding(hermes_home=str(tmp_path))

        env_file = tmp_path / "memora_env.sh"
        assert env_file.exists()
        text = env_file.read_text()
        assert 'MEMORA_KANBAN_BACKEND="linear"' in text
        assert 'MEMORA_TUNNEL="ngrok"' in text
        assert 'RAG_WORKER_URL="https://worker.example.com"' in text
        assert 'RAG_AUTH_TOKEN="secret_token"' in text


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
