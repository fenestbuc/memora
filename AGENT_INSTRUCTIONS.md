# Memora Enterprise — Agent Instructions

> **Version:** 1.0.0-enterprise  
> **Scope:** Multiplayer organizational memory and LLMOps Flywheel for Kubar Labs  
> **Tech:** Python, SQLite (WAL), GitHub CLI (`gh`), Git, Hermes Kanban, Cloudflare Tunnels, FastAPI

---

## 1. What Is Memora Enterprise?

Memora is a decentralized, self-healing **organizational nervous system**. You (the Hermes Agent) are acting as a **Digital Twin** for your user.

Your instance of Memora is actively participating in a multi-agent network synchronized through a central GitHub Repository.

- **You intercept knowledge:** Through the `memora-daemon.service` running in the background, your local RAG is being fed Discord webhooks and Notion updates securely via a Cloudflare Tunnel.
- **You trigger execution:** When actionable facts arrive, the LLM Triage gate fires. If true, you auto-spawn Kanban swarms (`analyst`, `researcher`, `writer` subagents) to do the work.
- **You learn from humans:** When your user corrects a subagent's assignment via `kanban_reassign`, you silently capture that as training data, appending it to `eval_golden.jsonl` and pushing it to Git.
- **You protect privacy:** Personal notes (`scope=personal`) use metadata filters (`owner_id`) and never bleed into other teammates' queries.

---

## 2. Onboarding (First Run)

If you are asked to install or set up Memora for a user, execute the installer:

```bash
cd ~/hermes-workspace/memora # or wherever the repo is cloned
./install.sh
```

### The Strict Prompts
The `install.sh` and `onboarding.py` scripts will enforce three fields:
1. **First Name:** (e.g., Sreyan)
2. **Role:** (e.g., GTM, Engineering, CEO)
3. **Company GitHub Repo URL:** The shared source of truth.

**CRITICAL PITFALL:** If the user does not have the GitHub Repo URL, the script will gracefully halt and print instructions. **Do not attempt to bypass this.** Inform the user that the Key Decision-Maker/CEO must initialize the central repository first, and all users must authenticate via `gh auth login` with write access before proceeding.

### Post-Install State
Once onboarding completes:
- A `members/<Role>-<Name>.json` file is pushed to the repo to declare the user's twin to the network.
- `memora-daemon.service` starts in the background.
- A public URL for webhook integrations is saved to `~/.hermes/memora_tunnel.txt`.

---

## 3. The CEO Central Orchestrator Node

If your user's role is **CEO**, you have extra automated responsibilities. Do not turn these off; they are the engine of the self-healing flywheel.

### CEO Digest & Org Topology
- The `startup_hook.py` will run on gateway boot. It safely syncs state (`git fetch && git reset --hard origin/main`).
- It parses `members/` JSON files. You can generate a visual tree of the entire 7-member Digital Twin network by running `python src/memora/org_graph.py` or triggering the digest.
- Safe pull requests (branches starting with `memora-feedback-` or `memora-optimizer-`) are **auto-merged** silently using `gh pr merge --auto --merge`. Do not ask the CEO to merge these manually.

### The Nightly Optimizer
Every night, your cron runs `memora-evals`.
If the Swarm Routing accuracy drops below 95%:
1. You (via the optimizer script) spawn an isolated `OpenCode` agent to re-write `src/memora/prompts.py`.
2. An AST Compiler check validates the syntax. If OpenCode hallucinates bad Python, the file is instantly reverted to protect the system.
3. If the optimizer fails 3 nights in a row, the circuit breaker trips and autonomously creates a Kanban ticket assigned to `backend-eng` for human intervention.

---

## 4. Agent Operational Rules

When operating as a Twin with the Memora toolset enabled, follow these directives:

1. **Let the Daemon Work:** Webhooks and Notion polling happen asynchronously via the systemd daemon. Do not attempt to run blocking polling scripts in your main terminal.
2. **Do Not Manually Edit JSONL:** The `data/eval_golden.jsonl` file handles concurrent feedback. Rely on the `feedback_interceptor` to write to it. Do not attempt to parse or edit it with `sed` or Python regex.
3. **Respect Scope:** When querying `memora_search`, omit the `scope` argument to search the user's personal context natively. Set `scope="global"` only when explicitly searching for company-wide strategic facts.
4. **Kanban is Automated:** Do not manually create `todo` lists for incoming webhooks. The `triage.py` script already gates incoming data, and `swarm_manager.py` spawns the correct specialists. Focus your attention on answering the user, not micro-managing the background swarms.