#!/usr/bin/env bash
# Memora Self-Install Script
#
# Sets up a Digital Twin for a team member on a fresh workstation.
# Performs strict interactive onboarding (name, role, company repo URL),
# installs the Memora plugin, provisions a public tunnel for secure
# webhook ingress, and registers a systemd daemon.
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
Memora Installer

Sets up a Digital Twin for AI-native startup teams. The script performs
strict interactive onboarding, installs the Memora Hermes plugin, clones
the company memory repository, and (on Linux) registers a systemd daemon.

Key capabilities:
  • JSONL storage — Facts/eval datasets use .jsonl for zero-conflict git sync.
  • Public tunnel — Secure webhook ingress via cloudflared, ngrok, or localtunnel.
  • systemd daemonization — Background FastAPI listener (memora-daemon.service).
  • Strict onboarding — Requires first name, role, and company GitHub repo URL.
                     Halts with instructions if the repo URL is missing.
  • CEO digest — Daily summary of pending PRs requiring human approval.

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
# Preflight checks
# ---------------------------------------------------------------------------
preflight_passed=true

echo "==> Running preflight checks..."

python_version=$(python3 --version 2>/dev/null | awk '{print $2}')
if [[ -z "$python_version" ]]; then
  echo "❌ Python 3 not found. Install Python 3.10+ and try again." >&2
  preflight_passed=false
else
  major=$(echo "$python_version" | cut -d. -f1)
  minor=$(echo "$python_version" | cut -d. -f2)
  if [[ "$major" -lt 3 || ( "$major" -eq 3 && "$minor" -lt 10 ) ]]; then
    echo "❌ Python $python_version is too old. Python 3.10+ required." >&2
    preflight_passed=false
  else
    echo "  ✅ Python $python_version"
  fi
fi

if ! command -v git &>/dev/null; then
  echo "❌ git not found. Install git and try again." >&2
  preflight_passed=false
else
  echo "  ✅ git"
fi

if ! command -v pip3 &>/dev/null && ! command -v pip &>/dev/null; then
  echo "❌ pip not found. Install pip and try again." >&2
  preflight_passed=false
else
  echo "  ✅ pip"
fi

if ! command -v gh &>/dev/null; then
  echo "⚠️  gh (GitHub CLI) not found. Member declaration push will be skipped." >&2
  echo "    Install from: https://cli.github.com/" >&2
elif ! gh auth status &>/dev/null; then
  echo "⚠️  gh CLI not authenticated. Run 'gh auth login' before proceeding." >&2
  echo "    Member declaration push will fail without authentication." >&2
fi

if ! command -v curl &>/dev/null && ! command -v wget &>/dev/null; then
  echo "❌ Neither curl nor wget found. One is required for cloudflared download." >&2
  preflight_passed=false
else
  echo "  ✅ curl/wget"
fi

if command -v wrangler &>/dev/null; then
  echo "  ✅ wrangler (Cloudflare CLI)"
else
  echo "⚠️  wrangler not found. Required only if you plan to deploy the RAG Worker yourself." >&2
  echo "    Install: npm install -g wrangler" >&2
fi

if command -v docker &>/dev/null; then
  echo "  ✅ docker"
else
  echo "⚠️  docker not found. Optional — only needed for the Docker quickstart." >&2
fi

if [[ "$preflight_passed" == false ]]; then
  echo "" >&2
  echo "Preflight checks failed. Fix the issues above and re-run." >&2
  exit 1
fi
echo "  ✅ Preflight passed"
echo ""

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

echo "==> Installing Memora for $NAME ($ROLE)..."

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
        "(\\`gh auth login\\`) and have write access to the company repository."
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
# 7. Optional Linear integration
# ---------------------------------------------------------------------------
echo ""
echo "Memora supports two Kanban backends:"
echo "  1. Hermes native kanban (default) — works if Hermes CLI is installed."
echo "  2. Linear — for teams not using Hermes."
echo ""
read -rp "Enable Linear integration? [y/N]: " enable_linear
if [[ "$enable_linear" =~ ^[Yy]$ ]]; then
  read -rsp "Linear API key (from https://linear.app/settings/account): " linear_key
  echo ""
  read -rp "Linear Team ID (optional, press Enter to skip): " linear_team

  python3 <<PYEOF
import os, yaml
config_path = os.path.expanduser("$CONFIG_YAML")
with open(config_path, "r") as f:
    config = yaml.safe_load(f) or {}
config.setdefault("linear", {})
config["linear"]["api_key"] = "$linear_key"
if "$linear_team":
    config["linear"]["team_id"] = "$linear_team"
with open(config_path, "w") as f:
    yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
print("Linear credentials saved to config.yaml")
PYEOF

  # Set env var for current session
  export MEMORA_KANBAN_BACKEND="linear"
  echo "export MEMORA_KANBAN_BACKEND=linear" >> "$HERMES_HOME/memora_env.sh"
  echo "export LINEAR_API_KEY=$linear_key" >> "$HERMES_HOME/memora_env.sh"
  if [[ -n "$linear_team" ]]; then
    echo "export LINEAR_TEAM_ID=$linear_team" >> "$HERMES_HOME/memora_env.sh"
  fi
  echo "==> Linear integration enabled."
else
  echo "==> Using Hermes native kanban (default)."
fi

# ---------------------------------------------------------------------------
# 8. CEO digest + nightly evaluation cron jobs
# ---------------------------------------------------------------------------
ROLE_LOWER=$(echo "$ROLE" | tr '[:upper:]' '[:lower:]')
if [[ "$ROLE_LOWER" == "ceo" ]]; then
  echo "==> Role is CEO — installing nightly digest and evaluation cron jobs..."
  mkdir -p "$WORKSPACE/logs"

  # Daily digest (09:00) — CEO reviews pending PRs, NO auto-merge
  CRON_DIGEST="0 9 * * * cd $WORKSPACE && python3 -c \"from memora.ceo_digest import send_digest; send_digest()\" >> $WORKSPACE/logs/ceo_digest.log 2>&1"
  # Nightly evaluation (02:00) — generates suggestion PRs, NEVER auto-applies
  CRON_EVAL="0 2 * * * cd $WORKSPACE && memora-evals --golden data/eval_golden.jsonl -o reports/nightly_\$(date +\\%Y\\%m\\%d).json >> $WORKSPACE/logs/memora_evals.log 2>&1"

  # Remove any existing memora CEO lines to avoid duplicates, then add
  (crontab -l 2>/dev/null | grep -v "ceo_digest" | grep -v "memora-evals" || true; echo "$CRON_DIGEST"; echo "$CRON_EVAL") | crontab -
  echo "==> Cron jobs installed: CEO digest at 09:00, memora-evals at 02:00"
else
  echo "==> Role is $ROLE — skipping CEO digest and optimizer cron jobs."
fi

# ---------------------------------------------------------------------------
# 9. Install tunnel tooling (cloudflared preferred, ngrok/localtunnel optional)
# ---------------------------------------------------------------------------
install_cloudflared() {
  if command -v cloudflared &>/dev/null; then
    echo "==> cloudflared already installed: $(command -v cloudflared)"
    return 0
  fi

  echo "==> cloudflared not found — downloading with checksum verification..."

  local os arch binary_name checksum_url bin_dir expected_checksum
  os=$(uname -s)
  arch=$(uname -m)

  case "$os" in
    Linux)
      case "$arch" in
        x86_64|amd64) binary_name="cloudflared-linux-amd64" ;;
        aarch64|arm64) binary_name="cloudflared-linux-arm64" ;;
        *) echo "Unsupported architecture: $arch" >&2; return 1 ;;
      esac
      ;;
    Darwin)
      case "$arch" in
        x86_64|amd64) binary_name="cloudflared-darwin-amd64" ;;
        aarch64|arm64) binary_name="cloudflared-darwin-arm64" ;;
        *) echo "Unsupported architecture: $arch" >&2; return 1 ;;
      esac
      ;;
    *)
      echo "Unsupported OS: $os" >&2
      return 1
      ;;
  esac

  bin_dir="$HERMES_HOME/bin"
  mkdir -p "$bin_dir"

  # Download latest release page to find the actual version
  latest_url="https://github.com/cloudflare/cloudflared/releases/latest"
  # Extract version from redirect URL
  version=$(curl -sI "$latest_url" | grep -i "location:" | sed 's|.*/tag/||' | tr -d '\r')
  if [[ -z "$version" ]]; then
    echo "Warning: Could not determine latest cloudflared version. Skipping checksum verification." >&2
    version="latest"
  fi

  download_url="https://github.com/cloudflare/cloudflared/releases/download/${version}/${binary_name}"
  checksum_url="https://github.com/cloudflare/cloudflared/releases/download/${version}/${binary_name}.sha256"

  echo "    Downloading ${binary_name} (${version})..."
  if command -v curl &>/dev/null; then
    curl -fsSL --retry 3 "$download_url" -o "$bin_dir/cloudflared"
    if [[ "$version" != "latest" ]]; then
      expected_checksum=$(curl -fsSL --retry 3 "$checksum_url" 2>/dev/null | awk '{print $1}')
    fi
  elif command -v wget &>/dev/null; then
    wget -q --tries=3 "$download_url" -O "$bin_dir/cloudflared"
    if [[ "$version" != "latest" ]]; then
      expected_checksum=$(wget -q --tries=3 -O - "$checksum_url" 2>/dev/null | awk '{print $1}')
    fi
  else
    echo "Error: curl or wget is required to download cloudflared." >&2
    return 1
  fi

  # Verify checksum if available
  if [[ -n "$expected_checksum" ]]; then
    actual_checksum=$(sha256sum "$bin_dir/cloudflared" | awk '{print $1}')
    if [[ "$actual_checksum" != "$expected_checksum" ]]; then
      echo "Error: cloudflared checksum mismatch! Expected $expected_checksum, got $actual_checksum" >&2
      rm -f "$bin_dir/cloudflared"
      return 1
    fi
    echo "    Checksum verified."
  fi

  chmod +x "$bin_dir/cloudflared"
  echo "==> Installed cloudflared to $bin_dir/cloudflared"
}

install_cloudflared

# ---------------------------------------------------------------------------
# 10. Daemon systemd service (Linux only)
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
Description=Memora Background Daemon (Discord/MCP listeners + Public Tunnel)
After=network.target

[Service]
Type=simple
ExecStart=$(command -v python3) -m memora.daemon --tunnel cloudflared
Restart=always
RestartSec=5
Environment=MEMORA_DAEMON_PORT=${DAEMON_PORT}
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
# 11. RAG Worker credentials
# ---------------------------------------------------------------------------
echo ""
echo "==> Memora needs a Cloudflare Workers RAG backend to store memories."
echo "    If you haven't deployed it yet, see: rag-worker/README.md"
echo ""

read -rp "RAG Worker URL (e.g. https://your-worker.workers.dev): " rag_url
read -rsp "RAG Auth Token: " rag_token
echo ""

if [[ -n "$rag_url" && -n "$rag_token" ]]; then
  ENV_FILE="$HERMES_HOME/memora_env.sh"
  echo "# Memora environment — auto-generated by install.sh" > "$ENV_FILE"
  echo "export RAG_WORKER_URL=\"$rag_url\"" >> "$ENV_FILE"
  echo "export RAG_AUTH_TOKEN=\"$rag_token\"" >> "$ENV_FILE"
  echo "==> RAG credentials saved to $ENV_FILE"
  echo "    Source it with: source $ENV_FILE"
else
  echo "⚠️  RAG credentials skipped. Your Digital Twin will store facts locally"
  echo "    but cannot sync to the cloud backend until you set:"
  echo "      export RAG_WORKER_URL=<url>"
  echo "      export RAG_AUTH_TOKEN=<token>"
fi

# ---------------------------------------------------------------------------
# 12. Wrangler secret reminder (for RAG Worker operators)
# ---------------------------------------------------------------------------
if [[ -f "$SCRIPT_DIR/rag-worker/wrangler.toml.template" ]]; then
  echo ""
  echo "==> RAG Worker deployment reminder:"
  echo "    If YOU are deploying the worker (not just using one someone else deployed):"
  echo ""
  echo "    1. cd rag-worker"
  echo "    2. cp wrangler.toml.template wrangler.toml"
  echo "    3. Fill in database_id and kv_namespaces id"
  echo "    4. wrangler secret put AUTH_TOKEN   <-- NEVER put secrets in wrangler.toml"
  echo "    5. wrangler d1 execute hermes-memory --file=schema.sql"
  echo "    6. wrangler deploy"
fi

# ---------------------------------------------------------------------------
# 13. Optional Docker quickstart
# ---------------------------------------------------------------------------
if command -v docker &>/dev/null && [[ -f "$SCRIPT_DIR/docker-compose.yml" ]]; then
  echo ""
  read -rp "Run Memora daemon via Docker Compose? [y/N]: " enable_docker
  if [[ "$enable_docker" =~ ^[Yy]$ ]]; then
    cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env" 2>/dev/null || true
    echo "==> Docker Compose quickstart enabled."
    echo "    1. Edit .env with your credentials"
    echo "    2. docker compose up -d"
  fi
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
echo "Memora setup complete for $NAME ($ROLE)!"
echo "  Company memory : $COMPANY_DIR"
echo "  Plugin config  : $MEMORA_CONFIG"
echo "  Hermes config  : $CONFIG_YAML"
echo ""
echo "Key features active:"
echo "  • JSONL datasets — Conflict-free sync for facts and eval_golden.jsonl"
echo "  • Public tunnel  — Secure webhook ingress (cloudflared/trycloudflare.com)"
echo "  • systemd daemon — Background webhook listener (memora-daemon.service)"
if [[ "$ROLE_LOWER" == "ceo" ]]; then
  echo "  • CEO digest     — Daily summary of pending PRs requiring your approval"
  echo "  • Nightly evals  — memora-evals runs at 02:00; low scores generate suggestion PRs"
fi
echo ""
echo "Tunnel alternatives (no domain needed):"
echo "  • cloudflared --tunnel cloudflared   (default, free)"
echo "  • ngrok       --tunnel ngrok         (requires ngrok account)"
echo "  • localtunnel --tunnel localtunnel   (requires npm)"
echo ""
echo "Next steps:"
echo "  1. Source environment: source $HERMES_HOME/memora_env.sh"
echo "  2. Restart Hermes to load the updated configuration."
echo "  3. Verify with: python -c \"from memora.plugin import MemoraProvider; p=MemoraProvider(); print(p.is_available())\""
echo "  4. Start collaborating — company facts sync via git PRs in .jsonl."
echo "  5. (CEO) Review nightly eval reports in $WORKSPACE/logs/"
