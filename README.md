# Hermes RAG Memory

> Give your AI assistant a memory that persists across sessions.

Hermes RAG Memory is a **second-brain plugin** for [Hermes](https://github.com/hermes-ai) agents.
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

**Hermes RAG Memory solves this.** Every conversation enriches a persistent knowledge graph
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

## Quick Start

### 1. Deploy the RAG Worker

```bash
cd hermes-rag/  # Cloudflare Workers project
npx wrangler deploy
# Note the deployed URL and set an AUTH_TOKEN secret
```

### 2. Install Hermes RAG Memory

```bash
git clone https://github.com/fenestbuc/hermes-rag-memory.git
pip install -e hermes-rag-memory/
```

### 3. Configure

```bash
cp hermes-rag-memory/config/example.env .env
# Edit .env with your worker URL and token
export $(cat .env | xargs)
```

### 4. Add to Hermes

Edit your Hermes `config.yaml`:

```yaml
memory:
  provider: hermes-rag-memory
```

### 5. Schedule Nightly Maintenance

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
HermesRagMemoryProvider (SQLite write-behind queue + local .md mirror)
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
hermes-rag-memory/
|-- src/memora/
|   |-- plugin.py          # Hermes MemoryProvider implementation
|   |-- brain_indexer.py   # Workspace manifest + session indexer
|   |-- wiki_ingester.py   # Session -> markdown wiki generator
|   |-- conflict_detector.py  # Contradiction detection
|   |-- nightly_brain.py   # Scheduled maintenance runner
|-- tests/                 # 23+ tests, TDD approach
|-- config/
|   |-- example.env        # Template for env vars
|-- docs/
|   |-- SETUP.md           # Full deployment guide
```

---

## Development

```bash
cd hermes-rag-memory
python -m pytest tests/ -v      # run all tests
python -m memora.nightly_brain  # run maintenance manually
```

---

## License

MIT — see [LICENSE](LICENSE).
