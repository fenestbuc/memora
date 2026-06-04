#!/usr/bin/env bash
# Memora Enterprise Self-Install Script
#
# Sets up a Digital Twin for a team member on a fresh workstation.
# Performs strict interactive onboarding (name, role, company repo URL),
# installs the Memora plugin, provisions a Cloudflare Tunnel for secure
# webhook ingress, registers a systemd daemon, and (for CEOs) installs
# nightly LLMOps optimizer + auto-merge hooks.
#
# Facts and golden eval datasets are stored as line-delimited JSON (.jsonl)
# to eliminate git merge conflicts when multiple agents push concurrently.
#
# Usage: ./install.sh [--name <name>] [--role <role>] [--repo <url>]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
WORKSPACE="${MEMORA_WORKSPACE:-$HOME/hermes-workspace}"
COMPANY_REPO="${COMPANY_MEMORY_REPO:-}"

NAME=""
ROLE=""

# ---------------------------------------------------------------------------
# Parse flags
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --name)
      NAME="$2"
      shift 2
      ;;
    --role)
      ROLE="$2"
      shift 2
      ;;
    --repo)
      COMPANY_REPO="$2"
      shift 2
      ;;
    --help | -h)
      cat <<'EOF'
Memora Enterprise Installer

Sets up a Digital Twin for AI-native startup teams. The script performs
strict interactive onboarding, installs the Memora Hermes plugin, clones
the company memory repository, and (on Linux) registers a systemd daemon.

Key capabilities:
  • JSONL storage — Facts/eval datasets use .jsonl for zero-conflict git sync.
  • Cloudflare Tunnel — Secure public webhook ingress via cloudflared.
  • systemd daemonization — Background FastAPI listener (memora-daemon.service).
  • Strict onboarding — Requires first name, role, and company GitHub repo URL.
                     Halts with instructions if the repo URL is missing.
  • CEO orchestration — Auto-merges safe PRs (memora-feedback-*, memora-optimizer-*)
                        and registers the nightly LLMOps optimizer cron.

Usage:
  ./install.sh [--name <first_name>] [--role <role>] [--repo <git_url>]

Environment overrides:
  HERMES_HOME         Path to Hermes config dir (default: ~/.hermes)
  MEMORA_WORKSPACE    Workspace root (default: ~/hermes-workspace)
  COMPANY_MEMORY_REPO Company memory repository URL

Examples:
  ./install.sh --name Sreyan --role GTM
  ./install.sh --name Vaibhav --role CEO --repo https://github.com/Kubar-Labs/company-memory.git
EOF
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      echo "Run '$0 --help' for usage." >&2
      exit 1
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Prompt for missing values (strict onboarding)
# ---------------------------------------------------------------------------
if [[ -z "$NAME" ]]; then
  read -rp "Enter your first name: " NAME
fi
if [[ -z "$ROLE" ]]; then
  read -rp "Enter your role (e.g., CEO, GTM, Engineering): " ROLE
fi
if [[ -z "$COMPANY_REPO" ]]; then
  read -rp "Company GitHub repo URL: " COMPANY_REPO
fi

if [[ -z "$NAME" || -z "$ROLE" || -z "$COMPANY_REPO" ]]; then
  echo "Error: first name, role, and company GitHub repo URL are all required." >&2
  echo "" >&2
  echo "A company GitHub repository is required for Memora multiplayer mode." >&2
  echo "The repository must be created and configured by the CEO or key " >&2
  echo "decision-maker before team members can onboard." >&2
  echo "" >&2
  echo "Please ask them to set up the repo and provide the link," >&2
  echo "then re-run this installer." >&2
  exit 1
fi

echo "==> Installing Memora Enterprise for $NAME ($ROLE)..."

# ---------------------------------------------------------------------------
# 1. Install plugin to ~/.hermes/plugins/ if not already present
# ---------------------------------------------------------------------------
PLUGIN_DIR="$HERMES_HOME/plugins/memora"
if [[ ! -d "$PLUGIN_DIR" ]]; then
  echo "==> Installing Memora plugin to $PLUGIN_DIR..."
  mkdir -p "$HERMES_HOME/plugins"
  if [[ -d "$SCRIPT_DIR/.git" ]]; then
    # Running from the repo directly — symlink or clone
    if command -v ln &>/dev/null; then
      ln -s "$SCRIPT_DIR" "$PLUGIN_DIR"
    else
      git clone "$SCRIPT_DIR" "$PLUGIN_DIR"
    fi
  else
    git clone https://github.com/fenestbuc/memora.git "$PLUGIN_DIR"
  fi
else
  echo "==> Memora plugin already installed at $PLUGIN_DIR"
fi

# Install Python package in editable mode
if ! python3 -c "import memora" 2>/dev/null; then
  echo "==> Installing Python package..."
  pip install -e "$PLUGIN_DIR" >/dev/null 2>&1 || pip install -e "$PLUGIN_DIR"
else
  echo "==> Python package already available"
fi

# ---------------------------------------------------------------------------
# 2. Write memora profile (strict onboarding format)
# ---------------------------------------------------------------------------
MEMORA_CONFIG="$HERMES_HOME/memora.json"
mkdir -p "$HERMES_HOME"
cat > "$MEMORA_CONFIG" <<EOF
{
  "first_name": "$NAME",
  "role": "$ROLE",
  "company_github_repo": "$COMPANY_REPO"
}
EOF
echo "==> Wrote memora profile to $MEMORA_CONFIG"

# ---------------------------------------------------------------------------
# 3. Push member declaration to company repo
# ---------------------------------------------------------------------------
echo "==> Pushing member declaration to company repository..."
export MEMORA_FIRST_NAME="$NAME"
export MEMORA_ROLE="$ROLE"
export MEMORA_REPO="$COMPANY_REPO"

python3 <<PYEOF
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Ensure the installed src path is discoverable
sys.path.insert(0, os.path.expanduser("~/.hermes/plugins/memora/src"))

try:
    from memora.github_sync import generate_company_pr
except Exception as exc:
    print(f"Warning: could not import memora.github_sync: {exc}")
    print("Skipping member declaration push.")
    sys.exit(0)

profile = {
    "first_name": os.environ["MEMORA_FIRST_NAME"],
    "role": os.environ["MEMORA_ROLE"],
    "company_github_repo": os.environ["MEMORA_REPO"],
}

filename = f"members/{profile['role'].lower()}-{profile['first_name'].lower()}.json"

try:
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir) / "repo"
        subprocess.run(
            ["git", "clone", profile["company_github_repo"], str(repo_path)],
            check=True,
            capture_output=True,
        )
        generate_company_pr(
            title=f"Add member: {profile['role']} - {profile['first_name']}",
            filename=filename,
            content=json.dumps(profile, indent=2),
            repo_path=repo_path,
        )
    print(f"Member declaration pushed: {filename}")
except subprocess.CalledProcessError as exc:
    print("\nGitHub sync failed.")
    print(
        "Please ensure you have authenticated with the GitHub CLI "
        "(\`gh auth login\`) and have write access to the company repository."
    )
    print(f"Original error: {exc}")
    sys.exit(1)
PYEOF

# ---------------------------------------------------------------------------
# 4. Clone company memory repository for local workspace use
# ---------------------------------------------------------------------------
COMPANY_DIR="$WORKSPACE/company-memory"
if [[ -d "$COMPANY_DIR/.git" ]]; then
  echo "==> Company memory repo already exists at $COMPANY_DIR"
else
  echo "==> Cloning company memory repository..."
  mkdir -p "$WORKSPACE"
  git clone "$COMPANY_REPO" "$COMPANY_DIR"
fi

# ---------------------------------------------------------------------------
# 5. Update Hermes config.yaml for memora provider
# ---------------------------------------------------------------------------
CONFIG_YAML="$HERMES_HOME/config.yaml"

python3 <<PYEOF
import yaml, sys, os

config_path = os.path.expanduser("$CONFIG_YAML")

try:
    with open(config_path, "r") as f:
        config = yaml.safe_load(f) or {}
except FileNotFoundError:
    config = {}

# Ensure memory.provider is memora
config.setdefault("memory", {})
config["memory"]["provider"] = "memora"

# Ensure plugins.enabled contains memora
config.setdefault("plugins", {})
config["plugins"].setdefault("enabled", [])
if "memora" not in config["plugins"]["enabled"]:
    config["plugins"]["enabled"].append("memora")

# Keep company_memory directory discoverable for agents
config.setdefault("custom", {})
config["custom"]["company_memory_dir"] = "$COMPANY_DIR"

with open(config_path, "w") as f:
    yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

print(f"Updated {config_path}")
PYEOF

# ---------------------------------------------------------------------------
# 6. Install skill
# ---------------------------------------------------------------------------
SKILL_DIR="$HERMES_HOME/skills/memora"
mkdir -p "$SKILL_DIR"
if [[ -f "$SCRIPT_DIR/docs/SKILL.md" ]]; then
  cp "$SCRIPT_DIR/docs/SKILL.md" "$SKILL_DIR/SKILL.md"
  echo "==> Installed memora skill to $SKILL_DIR"
fi

# ---------------------------------------------------------------------------
# 7. CEO digest + nightly optimizer cron jobs
# ---------------------------------------------------------------------------
ROLE_LOWER=$(echo "$ROLE" | tr '[:upper:]' '[:lower:]')
if [[ "$ROLE_LOWER" == "ceo" ]]; then
  echo "==> Role is CEO — installing nightly digest and LLMOps optimizer cron jobs..."
  mkdir -p "$WORKSPACE/logs"

  # Daily digest (09:00) — includes auto-merge of safe memora-* PRs
  CRON_DIGEST="0 9 * * * cd $WORKSPACE && python3 -c \"from memora.ceo_digest import send_digest; send_digest()\" >> $WORKSPACE/logs/ceo_digest.log 2>&1"
  # Nightly evaluation (02:00) — seeds the LLMOps optimizer flywheel
  CRON_EVAL="0 2 * * * cd $WORKSPACE && memora-evals --golden data/eval_golden.jsonl -o reports/nightly_\$(date +\\%Y\\%m\\%d).json >> $WORKSPACE/logs/memora_evals.log 2>&1"

  # Remove any existing memora CEO lines to avoid duplicates
  (crontab -l 2>/dev/null | grep -v "ceo_digest" | grep -v "memora-evals" || true; echo "$CRON_DIGEST"; echo "$CRON_EVAL") | crontab -
  echo "==> Cron jobs installed: CEO digest at 09:00, memora-evals at 02:00"
else
  echo "==> Role is $ROLE — skipping CEO digest and optimizer cron jobs."
fi

# ---------------------------------------------------------------------------
# 8. Install cloudflared (for tunnel support)
# ---------------------------------------------------------------------------
install_cloudflared() {
  if command -v cloudflared &>/dev/null; then
    echo "==> cloudflared already installed: $(command -v cloudflared)"
    return
  fi

  echo "==> cloudflared not found — downloading..."

  local os arch binary_name download_url bin_dir
  os=$(uname -s)
  arch=$(uname -m)

  case "$os" in
    Linux)
      case "$arch" in
        x86_64|amd64) binary_name="cloudflared-linux-amd64" ;;
        aarch64|arm64) binary_name="cloudflared-linux-arm64" ;;
        *) echo "Unsupported architecture: $arch" >&2; exit 1 ;;
      esac
      ;;
    Darwin)
      case "$arch" in
        x86_64|amd64) binary_name="cloudflared-darwin-amd64" ;;
        aarch64|arm64) binary_name="cloudflared-darwin-arm64" ;;
        *) echo "Unsupported architecture: $arch" >&2; exit 1 ;;
      esac
      ;;
    *)
      echo "Unsupported OS: $os" >&2
      exit 1
      ;;
  esac

  bin_dir="$HERMES_HOME/bin"
  mkdir -p "$bin_dir"

  download_url="https://github.com/cloudflare/cloudflared/releases/latest/download/${binary_name}"
  echo "    Downloading ${binary_name}..."
  if command -v curl &>/dev/null; then
    curl -fsSL --retry 3 "$download_url" -o "$bin_dir/cloudflared"
  elif command -v wget &>/dev/null; then
    wget -q --tries=3 "$download_url" -O "$bin_dir/cloudflared"
  else
    echo "Error: curl or wget is required to download cloudflared." >&2
    exit 1
  fi

  chmod +x "$bin_dir/cloudflared"
  echo "==> Installed cloudflared to $bin_dir/cloudflared"
}

install_cloudflared

# ---------------------------------------------------------------------------
# 9. Daemon systemd service (Linux only)
# ---------------------------------------------------------------------------
if [[ "$(uname -s)" == "Linux" ]] && command -v systemctl &>/dev/null; then
  echo "==> Installing memora-daemon systemd service..."

  DAEMON_PORT="${MEMORA_DAEMON_PORT:-8742}"

  if [[ "$EUID" -eq 0 ]]; then
    SERVICE_DIR="/etc/systemd/system"
  else
    SERVICE_DIR="$HOME/.config/systemd/user"
  fi
  mkdir -p "$SERVICE_DIR"

  SERVICE_FILE="$SERVICE_DIR/memora-daemon.service"
  cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Memora Background Daemon (Discord/MCP listeners + Cloudflare Tunnel)
After=network.target

[Service]
Type=simple
ExecStart=$(command -v python3) -m memora.daemon
Restart=always
RestartSec=5
Environment=MEMORA_DAEMON_PORT=${DAEMON_PORT}
Environment=MEMORA_TUNNEL=1
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF

  if [[ "$EUID" -eq 0 ]]; then
    systemctl daemon-reload
    systemctl enable memora-daemon.service
    systemctl start memora-daemon.service
    echo "==> System service installed and started: memora-daemon.service"
  else
    systemctl --user daemon-reload
    systemctl --user enable memora-daemon.service
    systemctl --user start memora-daemon.service
    echo "==> User service installed and started: memora-daemon.service"
    echo "    (logs: journalctl --user -u memora-daemon.service -f)"
  fi
else
  echo "==> Skipping systemd service installation (not Linux or systemctl unavailable)."
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
echo "Memora Enterprise setup complete for $NAME ($ROLE)!"
echo "  Company memory : $COMPANY_DIR"
echo "  Plugin config  : $MEMORA_CONFIG"
echo "  Hermes config  : $CONFIG_YAML"
echo ""
echo "Key features active:"
echo "  • JSONL datasets — Conflict-free sync for facts and eval_golden.jsonl"
echo "  • Cloudflare Tunnel — Public URL logged to ~/.hermes/memora_tunnel.txt"
echo "  • systemd daemon — Background webhook listener (memora-daemon.service)"
if [[ "$ROLE_LOWER" == "ceo" ]]; then
  echo "  • CEO auto-merge — Safe PRs (memora-feedback-*, memora-optimizer-*) merged automatically"
  echo "  • Nightly optimizer — memora-evals runs at 02:00; scores < 95%% trigger prompt tuning"
fi
echo ""
echo "Next steps:"
echo "  1. Restart Hermes to load the updated configuration."
echo "  2. Verify with: hermes-cli --check-config"
echo "  3. Start collaborating — company facts sync via git PRs in .jsonl."
echo "  4. (CEO) Review nightly eval reports in $WORKSPACE/logs/"
