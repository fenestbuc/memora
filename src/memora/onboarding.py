"""Interactive onboarding flow for Memora multiplayer config.

On first run, prompts the user for first name, role, and company GitHub repo URL,
writes the profile to ``~/.hermes/memora.json``, and pushes a member declaration
to the company repository via ``github_sync``.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .github_sync import generate_company_pr


def load_profile(hermes_home: str | None = None) -> dict[str, Any] | None:
    """Return the parsed memora profile if it exists, else ``None``.

    Args:
        hermes_home: Path to the Hermes home directory. Defaults to ``~/.hermes``.

    Returns:
        Parsed JSON dict or ``None`` when the file is missing.
    """
    home = Path(hermes_home) if hermes_home else Path.home() / ".hermes"
    memora_json = home / "memora.json"
    if memora_json.exists():
        return json.loads(memora_json.read_text(encoding="utf-8"))
    return None


def run_onboarding(hermes_home: str | None = None) -> dict[str, Any]:
    """Run interactive onboarding and push a member declaration.

    Args:
        hermes_home: Path to the Hermes home directory. Defaults to ``~/.hermes``.

    Returns:
        The newly created profile dict.
    """
    print("Welcome to Memora! Let's get you onboarded.")
    first_name = ""
    while not first_name:
        first_name = input("First name: ").strip()

    role = ""
    while not role:
        role = input("Role (e.g., CEO, GTM, Engineering): ").strip()

    repo_url = input("Company GitHub repo URL: ").strip()
    if not repo_url:
        print(
            "\nA company GitHub repository is required for Memora multiplayer mode.\n"
            "The repository must be created and configured by the CEO or key "
            "decision-maker before team members can onboard.\n"
            "Please ask them to set up the repo and provide the link."
        )
        sys.exit(1)

    profile = {
        "first_name": first_name,
        "role": role,
        "company_github_repo": repo_url,
    }

    home = Path(hermes_home) if hermes_home else Path.home() / ".hermes"
    home.mkdir(parents=True, exist_ok=True)
    memora_json = home / "memora.json"
    memora_json.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    print(f"Profile saved to {memora_json}")

    try:
        _push_member_declaration(profile)
    except subprocess.CalledProcessError as exc:
        print("\nGitHub sync failed.")
        print(
            "Please ensure you have authenticated with the GitHub CLI "
            "(`gh auth login`) and have write access to the company repository."
        )
        print(f"Original error: {exc}")
        memora_json.unlink(missing_ok=True)
        print(
            f"Removed partial profile at {memora_json} so onboarding can be retried."
        )
        sys.exit(1)

    return profile


def _push_member_declaration(profile: dict[str, Any]) -> None:
    """Clone the company repo and push a member declaration PR.

    Args:
        profile: The onboarding profile containing ``company_github_repo``,
            ``first_name``, and ``role``.
    """
    repo_url = profile["company_github_repo"]
    first_name = profile["first_name"]
    role = profile["role"]

    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir) / "repo"
        subprocess.run(
            ["git", "clone", repo_url, str(repo_path)],
            check=True,
            capture_output=True,
        )

        filename = f"members/{role.lower()}-{first_name.lower()}.json"
        content = json.dumps(profile, indent=2)

        generate_company_pr(
            title=f"Add member: {role} - {first_name}",
            filename=filename,
            content=content,
            repo_path=repo_path,
        )

        print(f"Member declaration pushed: {filename}")
