# Memora — Digital Twins for Startup Teams

Memora gives your AI assistant (Hermes, Claude, Cursor, etc.) a persistent long-term memory. It bridges conversation sessions so your agent remembers what it learned yesterday, last week, or last quarter.

For teams, Memora adds a shared company brain on top of the same engine: per-person workspace folders, shared rule files, and scoped facts that stay private or sync across the team through a central GitHub repository.

> Pre-alpha. This project is actively developed. Expect breaking changes.

---

## What Memora Actually Does

| Feature | Status | Notes |
|---|---|---|
| Persistent memory across sessions | Working | SQLite queue + Cloudflare Workers RAG backend |
| Semantic search | Working | BGE-M3 embeddings + Vectorize + reranking |
| Cited synthesis with gap analysis | Working | `memora_think` returns a sourced answer and a gaps section |
| Team sync via GitHub | Working | JSONL facts merge cleanly; member workspaces via PR |
| Per-person workspace folders | Working | `members/<Role>-<Name>/` with `USER.md` and subfolders |
| Shared company rule files | Working | `_brain-filing-rules.md` and `_output-rules.md` read into the system prompt |
| Discord webhook integration | Working | 24-hour thread continuity; multi-tunnel support |
| Per-person scheduled crons | Working | Markdown files under `crons/<Role>-<Name>/` run through the daemon |
| Deferred vector sync | Working | `/memory/sync` loop inside `memora-daemon` |
| Health checks | Working | `memora-doctor` reports worker, queue, and sync status |
| CEO digest | Working | Daily summary of pending PRs; no auto-merge |
| Kanban task creation | Working | Hermes native or Linear API; single ticket per trigger |
| Importance scoring | Working | LLM-based with heuristic fallback |
| Memory decay | Working | Exponential half-life + auto-archive |
| Semantic chunking | Working | Sentence-boundary + Markdown-aware |
| RAG backend | Working | Cloudflare Workers with D1 + Vectorize + Workers AI |
| Multi-tenant privacy | Partial | `owner_id` + `scope` filtering; per-person folders add soft boundaries |

---

## What Memora Is NOT

- Not fully autonomous. The optimizer suggests changes; it does not auto-merge or auto-deploy.
- Not a multi-agent swarm. Kanban integration creates single tickets, not orchestrated agent teams.
- Not magic zero-conflict sync. JSONL reduces merge conflicts, but Git PRs still need review.
- Not Windows-native. Linux/macOS only; WSL may work but is not tested.
- Not a hosted SaaS. You deploy and manage your own Cloudflare Workers backend.

---

## Quick Start (< 10 minutes)

### Prerequisites

- Python 3.10+
- git, pip
- GitHub CLI (`gh auth login`)
- A Cloudflare account (for the RAG worker)

### 1. Install Memora

```bash
git clone https://github.com/fenestbuc/memora.git
cd memora
./install.sh
```

The installer will prompt for:

- Your name and role
- Company GitHub repo URL
- Kanban backend (Hermes / Linear / none)
- Tunnel provider (cloudflared / ngrok / localtunnel)
- RAG Worker URL and auth token

### 2. Deploy the RAG Worker

The RAG worker code is included in this repo under `rag-worker/`:

```bash
cd rag-worker
cp wrangler.toml.template wrangler.toml
# Edit wrangler.toml with your database_id and vectorize index name

# NEVER put secrets in wrangler.toml — use wrangler secrets
wrangler secret put AUTH_TOKEN
wrangler d1 execute hermes-memory --file=schema.sql
wrangler deploy
```

See `rag-worker/README.md` for endpoint details and troubleshooting.

### 3. Verify

```bash
source ~/.hermes/memora_env.sh
python -c "from memora.plugin import MemoraProvider; p=MemoraProvider(); print(p.is_available())"
# → True
```

---

## Docker Quickstart (Optional)

```bash
# Set credentials in .env
cp .env.example .env
# Edit .env with your RAG_WORKER_URL and RAG_AUTH_TOKEN

docker compose up
```

---

## Architecture

```
                          Company GitHub repo
                 ┌─────────────────────────────────┐
                 │  _brain-filing-rules.md         │
                 │  _output-rules.md               │
                 │  members/<Role>-<Name>/         │
                 │    USER.md, meetings/, ...      │
                 └─────────────────────────────────┘
                             │
                             ▼ sync
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Your Agent    │────▶│  Memora Plugin  │────▶│  Cloudflare     │
│  (Hermes/etc.)  │     │  (Python)       │     │  Workers RAG    │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                                │                       │   D1 (facts)
                                │   SQLite queue        │   Vectorize (embeddings)
                                │   Local mirror        │   Workers AI (LLM)
                                ▼                       └─────────────────┘
                          ┌─────────────────┐
                          │  memora-daemon  │
                          │  (repo sync,    │
                          │   cron runner,  │
                          │   vector sync)  │
                          └─────────────────┘
```

Key pieces:

- **Memora plugin** runs inside your AI agent, exposes `memora_search`, `memora_think`, `memora_add`, etc.
- **RAG worker** stores facts in D1, embeddings in Vectorize, and runs LLM synthesis through Workers AI.
- **Company repo** is the shared source of truth for team facts, per-person workspaces, and shared rule files.
- **memora-daemon** keeps the local repo in sync, flushes deferred embeddings, and runs per-person crons.

---

## Core Concepts

### Members

Each teammate is declared through a PR that creates `members/<role>-<name>/`. That folder contains:

- `USER.md` — role, priorities, and answer-style preferences
- `concepts/` — frameworks and recurring themes
- `customers/` — accounts the teammate owns
- `meetings/` — named meeting notes
- `sources/` — dashboards, docs, and links

### Shared rule files

Place markdown files at the company repo root. They are automatically concatenated into the agent's system prompt:

- `_brain-filing-rules.md` — where new facts belong
- `_output-rules.md` — citation style, vocabulary, deterministic links
- `_excluded-people.md` — names that must never be stored or attributed
- `_operating-rules.md` — when to write to the brain vs a scratchpad

### Scope

Facts have two axes:

- `owner_id` — the teammate's first name from `~/.hermes/memora.json`
- `scope` — `personal` (default) or `company`

Use `scope=company` when a fact should be visible to the whole team. Personal facts only match queries from the same owner.

### Synthesis

`memora think "What do we know about acme-co?"` runs a search, asks an LLM to synthesize a cited answer, and includes a "Gaps" section that flags what is missing or uncertain.

---

## CLI Commands

| Command | Purpose |
|---|---|
| `memora-nightly` | Brain maintenance: index, wiki ingest, decay, evals |
| `memora-weekly` | Weekly digest |
| `memora-evals` | Routing accuracy evaluation |
| `memora-migrate` | Cloudflare D1 schema migrations |
| `memora-sync` | Sync facts from RAG, compile Markdown wiki, push to company repo |
| `memora-doctor` | Health check: worker, pending vector sync, local queue, repo sync lag |

---

## Documentation

- `docs/company-brain.md` — full company brain setup tutorial
- `docs/gbrain-audit.md` — comparison with GBrain and improvement map
- `docs/SKILL.md` — Hermes skill for working with Memora
- `AGENT_INSTRUCTIONS.md` — operational rules for agents running as digital twins
- `TROUBLESHOOTING.md` — common errors and fixes
- `rag-worker/README.md` — deploying the RAG backend

---

## Contributing

This is an open-source project under the MIT license. We follow conventional commits:

- `feat:` — new feature
- `fix:` — bug fix
- `chore:` — maintenance
- `docs:` — documentation

---

## License

MIT License — see LICENSE file.
