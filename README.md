# Memora — Digital Twins for Startup Teams

**Memora** gives your AI assistant (Hermes, Claude, etc.) a persistent long-term memory. It bridges conversation sessions so your agent remembers what it learned yesterday, last week, or last quarter.

For teams, Memora creates a shared semantic memory layer: each member runs a local "Digital Twin" that syncs business-critical facts through a shared GitHub repository. No more repeating yourself to the AI.

> ⚠️ **Pre-alpha.** This project is actively developed. Expect breaking changes.

---

## What Memora Actually Does

| Feature | Status | Notes |
|---|---|---|
| **Persistent memory across sessions** | ✅ Working | SQLite queue + Cloudflare Workers RAG backend |
| **Semantic search** | ✅ Working | BGE-M3 embeddings + Vectorize + reranking |
| **Team sync via GitHub** | ✅ Working | JSONL facts merge cleanly; member declarations via PR |
| **Discord webhook integration** | ✅ Working | 24h thread continuity; multi-tunnel support |
| **CEO digest** | ✅ Working | Daily summary of pending PRs; no auto-merge |
| **Kanban task creation** | ✅ Working | Hermes native or Linear API; single ticket per trigger |
| **Importance scoring** | ✅ Working | LLM-based with heuristic fallback |
| **Memory decay** | ✅ Working | Exponential half-life + auto-archive |
| **Semantic chunking** | ✅ Working | Sentence-boundary + Markdown-aware |
| **RAG backend** | ✅ Working | Cloudflare Workers with D1 + Vectorize | |
| **Autonomous optimizer** | ⚠️ Human-in-the-loop | Generates suggestion PRs; CEO approves |
| **Multi-tenant privacy** | ⚠️ Partial | `owner_id` + `scope` filtering; pluggable backends |

---

## What Memora Is NOT

- **Not fully autonomous.** The optimizer suggests changes; it does not auto-merge or auto-deploy.
- **Not a multi-agent swarm.** Kanban integration creates single tickets, not orchestrated agent teams.
- **Not magic zero-conflict sync.** JSONL reduces merge conflicts but Git PRs still need review.
- **Not Windows-native.** Linux/macOS only; WSL may work but is not tested.
- **Not a hosted SaaS.** You deploy and manage your own Cloudflare Workers backend.

---

## Quick Start (< 10 minutes)

### Prerequisites

- Python 3.10+
- git, pip
- [GitHub CLI](https://cli.github.com/) (`gh auth login`)
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
# Edit wrangler.toml with your database_id and KV namespace id

# NEVER put secrets in wrangler.toml — use wrangler secrets
wrangler secret put AUTH_TOKEN
wrangler d1 execute hermes-memory --file=schema.sql
wrangler deploy
```

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
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Your Agent    │────▶│  Memora Plugin  │────▶│  Cloudflare     │
│  (Hermes/Claude)│     │  (Python)       │     │  Workers RAG    │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                              │   SQLite queue          │   D1 (facts)
                              │   Local mirror          │   Vectorize (embeddings)
                              ▼                         │   Workers AI (LLM)
                        ┌─────────────────┐            │   KV (cache)
                        │  GitHub Repo    │            └─────────────────┘
                        │  (team sync)    │
                        └─────────────────┘
```

---

## CLI Commands

| Command | Purpose |
|---|---|
| `memora-nightly` | Run brain maintenance (index, wiki ingest, decay, evals) |
| `memora-weekly` | Generate weekly digest |
| `memora-evals` | Run routing accuracy evaluation |
| `memora-migrate` | Manage Cloudflare D1 schema migrations |
| `memora-sync` | Sync facts from RAG, compile Markdown Wiki, and push to company repo |

---

## Troubleshooting

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for common errors and fixes.

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
