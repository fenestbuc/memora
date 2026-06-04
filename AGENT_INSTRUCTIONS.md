# Memora — Agent Instructions

> **Version:** 1.0.0  
> **Scope:** Multiplayer organizational memory for startup teams  
> **Tech:** Python, SQLite (WAL), GitHub CLI (`gh`), Git, Hermes Kanban, Cloudflare Tunnels, FastAPI

---

## 1. What Is Memora?

Memora is a **shared semantic memory layer** for AI-assisted teams. You (the Hermes Agent) act as a **Digital Twin** for your user.

Your instance syncs business-critical facts through a central GitHub repository so the team shares context without repeating themselves.

- **You intercept knowledge:** Through `memora-daemon.service`, Discord webhooks and incoming messages are parsed and stored securely via a Cloudflare Tunnel.
- **You trigger tasks:** When actionable facts arrive, `triage.py` determines whether a Kanban ticket should be created (single ticket, not a swarm).
- **You learn from humans:** When your user corrects a routing decision via `kanban_reassign`, that feedback is captured as training data in `routing_corrections.jsonl`.
- **You protect privacy:** Personal notes (`scope=personal`) use `owner_id` metadata filtering and never bleed into other teammates' queries.

---

## 2. Onboarding (First Run)

If you are asked to install or set up Memora for a user, execute the installer:

```bash
cd ~/hermes-workspace/memora # or wherever the repo is cloned
./install.sh
```

### The Strict Prompts
The `install.sh` and `onboarding.py` scripts enforce three fields:
1. **First Name:** (e.g., Sreyan)
2. **Role:** (e.g., GTM, Engineering, CEO)
3. **Company GitHub Repo URL:** The shared source of truth.

**CRITICAL PITFALL:** If the user does not have the GitHub Repo URL, the script will gracefully halt and print instructions. **Do not attempt to bypass this.** Inform the user that the Key Decision-Maker/CEO must initialize the central repository first, and all users must authenticate via `gh auth login` with write access before proceeding.

### Post-Install State
Once onboarding completes:
- A `members/<Role>-<Name>.json` file is pushed to the repo to declare the user's twin.
- `memora-daemon.service` starts in the background (Linux with systemd).
- A public URL for webhook integrations is saved to `~/.hermes/memora_tunnel.txt`.

---

## 3. The CEO Digest

If your user's role is **CEO**, the nightly cron generates a daily summary of pending PRs requiring human approval.

- `startup_hook.py` safely syncs state (`git fetch && git reset --hard origin/main`).
- CEO digests are **never** auto-merged. Human approval is required for all changes.
- The optimizer generates *suggestion PRs* only. It does **not** auto-apply or auto-deploy.

---

## 4. Agent Operational Rules

When operating as a Twin with the Memora toolset enabled, follow these directives:

1. **Let the Daemon Work:** Webhooks happen asynchronously via the daemon. Do not run blocking polling scripts in your main terminal.
2. **Do Not Manually Edit JSONL:** Rely on the `feedback_interceptor` to write to `routing_corrections.jsonl`.
3. **Respect Scope:** When querying `memora_search`, omit the `scope` argument to search the user's personal context. Set `scope="global"` only when explicitly searching for company-wide strategic facts.
4. **Kanban is Gated:** `triage.py` creates single tickets, not orchestrated agent swarms. Focus on answering the user rather than micro-managing background tasks.