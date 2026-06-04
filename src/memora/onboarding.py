"""Interactive onboarding flow for Memora multiplayer config.

On first run, guides the user through setting up their Digital Twin:
  • Identity (name, role)
  • Company GitHub repo
  • Kanban backend (Hermes native vs Linear)
  • Webhook tunnel provider (cloudflared, ngrok, localtunnel)

Writes the profile to ``~/.hermes/memora.json`` and pushes a member
declaration to the company repository via ``github_sync``.
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


def _prompt_choice(
    prompt: str,
    options: list[tuple[str, str]],
    default: str | None = None,
) -> str:
    """Display a numbered choice menu and return the selected key.

    Args:
        prompt: Question to display before options.
        options: List of (key, description) tuples.
        default: Default key when user presses Enter.

    Returns:
        The selected key string.
    """
    print(f"\n{prompt}")
    for i, (key, desc) in enumerate(options, 1):
        marker = " (default)" if key == default else ""
        print(f"  {i}. {key:<14} — {desc}{marker}")

    while True:
        choice = input("Choice (number or name): ").strip()
        if not choice and default:
            return default

        # Try numeric choice
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(options):
                return options[idx][0]
        except ValueError:
            pass

        # Try key match
        for key, _ in options:
            if key.lower() == choice.lower():
                return key

        print("  Invalid choice. Please enter the number or name.")


def run_onboarding(hermes_home: str | None = None) -> dict[str, Any]:
    """Run interactive onboarding with guided default selection.

    Guides the user through:
    1. Identity setup (name, role)
    2. Company repository
    3. Kanban backend preference
    4. Tunnel provider preference
    5. Pushes member declaration

    Args:
        hermes_home: Path to the Hermes home directory. Defaults to ``~/.hermes``.

    Returns:
        The newly created profile dict with all preferences set.
    """
    print("=" * 60)
    print("  Welcome to Memora — Your Digital Twin")
    print("=" * 60)
    print(
        "\nMemora gives your AI assistant long-term memory across sessions.\n"
        "It works as a team: each member runs a Digital Twin that syncs\n"
        "facts through a shared GitHub repository.\n"
    )

    # ------------------------------------------------------------------
    # Step 1: Identity
    # ------------------------------------------------------------------
    print("━" * 60)
    print("Step 1/4 — Who are you?")
    print("━" * 60)

    first_name = ""
    while not first_name:
        first_name = input("  First name: ").strip()

    role = ""
    while not role:
        role = input("  Role (e.g. CEO, GTM, Engineering): ").strip()

    # ------------------------------------------------------------------
    # Step 2: Company repository
    # ------------------------------------------------------------------
    print("\n━" * 60)
    print("Step 2/4 — Company repository")
    print("━" * 60)
    print(
        "  Memora uses a shared GitHub repository as the 'source of truth'\n"
        "  for company-wide facts. If your company doesn't have one yet,\n"
        "  the CEO or decision-maker should create it first.\n"
    )

    repo_url = input("  Company GitHub repo URL: ").strip()
    if not repo_url:
        print(
            "\n  A company GitHub repository is required for Memora multiplayer mode.\n"
            "  The repository must be created and configured by the CEO or key\n"
            "  decision-maker before team members can onboard.\n\n"
            "  Please ask them to set up the repo and provide the link."
        )
        sys.exit(1)

    # ------------------------------------------------------------------
    # Step 3: Kanban backend
    # ------------------------------------------------------------------
    print("\n━" * 60)
    print("Step 3/4 — Kanban backend")
    print("━" * 60)
    print(
        "  When Memora detects actionable facts, it can create Kanban tasks\n"
        "  automatically. Choose your preferred task tracker:\n"
    )

    kanban_backend = _prompt_choice(
        "Which Kanban system do you use?",
        [
            ("hermes", "Native Hermes kanban (works inside Hermes CLI)"),
            ("linear", "Linear (requires API key from linear.app/settings)"),
            ("none", "No Kanban — tasks will only be logged"),
        ],
        default="hermes",
    )

    # ------------------------------------------------------------------
    # Step 4: Tunnel provider
    # ------------------------------------------------------------------
    print("\n━" * 60)
    print("Step 4/4 — Webhook tunnel")
    print("━" * 60)
    print(
        "  Memora can receive webhooks from Discord, Notion, and Linear\n"
        "  even if you're behind a firewall. Choose a tunnel provider:\n"
    )

    tunnel_provider = _prompt_choice(
        "Which tunnel provider do you prefer?",
        [
            ("cloudflared", "Cloudflare (free, no account needed)"),
            ("ngrok", "ngrok (requires free account at ngrok.com)"),
            ("localtunnel", "localtunnel (requires npm)"),
            ("none", "No tunnel — manual port forwarding only"),
        ],
        default="cloudflared",
    )

    # ------------------------------------------------------------------
    # Build & save profile
    # ------------------------------------------------------------------
    profile: dict[str, Any] = {
        "first_name": first_name,
        "role": role,
        "company_github_repo": repo_url,
        "kanban_backend": kanban_backend,
        "tunnel_provider": tunnel_provider,
        "version": "1.0.0",
    }

    home = Path(hermes_home) if hermes_home else Path.home() / ".hermes"
    home.mkdir(parents=True, exist_ok=True)
    memora_json = home / "memora.json"
    memora_json.write_text(json.dumps(profile, indent=2), encoding="utf-8")

    print(f"\n{'=' * 60}")
    print("  Profile saved!")
    print(f"  → {memora_json}")
    print(f"{'=' * 60}")
    print(f"\n  Name:  {first_name}")
    print(f"  Role:  {role}")
    print(f"  Repo:  {repo_url}")
    print(f"  Kanban: {kanban_backend}")
    print(f"  Tunnel: {tunnel_provider}")

    # Write environment helper
    env_file = home / "memora_env.sh"
    env_lines = [f"# Memora environment — auto-generated by onboarding"]
    if kanban_backend != "hermes":
        env_lines.append(f'export MEMORA_KANBAN_BACKEND="{kanban_backend}"')
    if tunnel_provider and tunnel_provider != "none":
        env_lines.append(f'export MEMORA_TUNNEL="{tunnel_provider}"')
    env_file.write_text("\n".join(env_lines) + "\n", encoding="utf-8")
    print(f"\n  Environment helper written to:")
    print(f"  → {env_file}")
    print(f"  Source it with: source {env_file}")

    # ------------------------------------------------------------------
    # Push member declaration
    # ------------------------------------------------------------------
    print(f"\n{'=' * 60}")
    print("  Pushing member declaration to company repo...")
    print(f"{'=' * 60}")

    try:
        _push_member_declaration(profile)
    except subprocess.CalledProcessError as exc:
        print("\n  GitHub sync failed.")
        print(
            "  Please ensure you have authenticated with the GitHub CLI\n"
            "  (`gh auth login`) and have write access to the company repository."
        )
        print(f"  Original error: {exc}")
        memora_json.unlink(missing_ok=True)
        print(
            f"  Removed partial profile at {memora_json} so onboarding can be retried."
        )
        sys.exit(1)

    # Post-onboarding instructions
    print(f"\n{'=' * 60}")
    print("  Next steps")
    print(f"{'=' * 60}")

    if kanban_backend == "linear":
        print(
            "\n  1. Get your Linear API key: https://linear.app/settings/account"
            "\n  2. Run: export LINEAR_API_KEY='<your-key>'"
            "\n     (or add it to ~/.hermes/memora_env.sh)"
        )
    if tunnel_provider == "ngrok":
        print(
            "\n  1. Install ngrok: https://ngrok.com/download"
            "\n  2. Configure auth token: ngrok config add-authtoken <token>"
        )
    if tunnel_provider == "localtunnel":
        print(
            "\n  1. Install localtunnel: npm install -g localtunnel"
        )

    print(
        f"\n  Restart your agent or run `./install.sh` to apply changes.\n"
        f"  Check health: python -c \"from memora.plugin import MemoraProvider; "
        f"p=MemoraProvider(); print(p.is_available())\""
    )

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

        print(f"  Member declaration pushed: {filename}")
