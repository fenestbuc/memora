# Memora

> **Memora** — persistent semantic memory for AI agents.

> Give your AI assistant a memory that persists across sessions.

Memora is a **second-brain plugin** for [Hermes](https://github.com/hermes-ai) agents.
It bridges your AI to a Cloudflare Workers RAG backend, storing every important fact,
preference, and decision so your agent remembers context days, weeks, or months later.

---

## Why This Matters

### For Founders, CEOs & Entrepreneurs

Your AI agent is more than a chatbot. It's a co-founder, a chief of staff, and a
knowledge repository rolled into one. But without persistent memory, every session
starts from zero. You waste time re-explaining:

- Investor preferences and communication styles
- Strategic pivots and why you made them
- Board commitments and follow-up deadlines
- Product decisions and the reasoning behind them
- Team context, roles, and priorities

**Memora solves this.** Every conversation enriches a persistent knowledge graph
that your agent can search, reference, and reason over. The result: your AI
co-founder actually *knows* your business.

Key capabilities:

- **Semantic Search** — Ask "What did we decide about pricing?" and get the
  full context from three weeks ago.
- **Conflict Detection** — Automatically flags when new decisions contradict
  old ones, so strategic pivots are explicit, not accidental.
- **Auto-Wiki Generation** — Conversations spawn wiki pages for people,
  projects, and concepts, creating a living knowledge base.
- **Background Sync** — Facts are batched and synced automatically; no manual
  tagging needed.
- **Local Memory Mirror** — All facts are also written to local markdown files
  (e.g. `memory/business.md`) for human readability and git versioning.

---

## Install

### On an agent platform (Hermes)

Memora is a `memory_provider` plugin for [Hermes](https://github.com/hermes-ai) agents. Install it into your agent's plugin directory:

```bash
cd ~/.hermes/plugins/
git clone https://github.com/fenestbuc/memora.git
pip install -e memora/
```

Then enable it in your Hermes `config.yaml`:

```yaml
memory:
  provider: memora

plugins:
  enabled:
    - memora
```

Set your environment variables:

```bash
export RAG_WORKER_URL="https://your-rag-worker.workers.dev"
export RAG_AUTH_TOKEN="your-secret-token"
```

Restart Hermes. The agent will now persist every important fact, preference, and decision to the RAG backend automatically.

### From source

```bash
git clone https://github.com/fenestbuc/memora.git
cd memora
pip install -e .
```

---

## Quick Start (Full Deployment)

If you are deploying the RAG worker backend yourself, follow these steps:

### 1. Deploy the RAG Worker

```bash
cd hermes-rag/  # Cloudflare Workers project
npx wrangler deploy
# Note the deployed URL and set an AUTH_TOKEN secret
```

### 2. Configure

```bash
cp memora/config/example.env .env
# Edit .env with your worker URL and token
export $(cat .env | xargs)
```

### 3. Schedule Nightly Maintenance

```bash
# Add to crontab for daily indexing
0 2 * * * cd ~/hermes-workspace && python -m memora.nightly_brain
```

---

## Architecture

```
Hermes Agent
    |
    v
MemoraProvider (SQLite write-behind queue + local .md mirror)
    |
    +--> Batch flush --> Cloudflare Workers RAG Backend
    |                      (Vectorize + D1 + BGE-M3)
    |
    +--> Local markdown memory/*.md files
    |
    +--> Nightly brain indexer
    |      +-- scan_workspace (mtime-aware hashing)
    |      +-- wiki_ingester (entity extraction -> markdown)
    |      +-- conflict_detector (contradiction detection)
    |
    +--> Semantic recall <-- prefetch() before every turn
```

---

## Configuration

| Environment Variable | Description | Default |
|---------------------|-------------|---------|
| `RAG_WORKER_URL` | Your deployed RAG worker URL | `https://your-rag-worker.workers.dev` |
| `RAG_AUTH_TOKEN` | Bearer token for worker auth | `YOUR_RAG_AUTH_TOKEN` |
| `HERMES_HOME` | Path for SQLite queue + config | `~/.hermes` |

---

## Project Structure

```
memora/
|-- src/memora/
|   |-- __init__.py        # Package metadata
|   |-- plugin.py          # Hermes MemoryProvider implementation
|-- tests/                 # Plugin tests
|-- config/
|   |-- example.env        # Template for env vars
|-- docs/
|   |-- SETUP.md           # Full deployment guide
```

---

## Development

```bash
cd memora
python -m pytest tests/ -v      # run all tests
python -m memora.nightly_brain  # run maintenance manually
```

---

## License

MIT — see [LICENSE](LICENSE).
