# Memora RAG Worker

Cloudflare Workers backend for Memora's semantic memory. Uses D1 (SQLite), Vectorize (embeddings), and Workers AI (LLM / embeddings / reranking).

## Prerequisites

- Wrangler CLI
- Cloudflare account with D1, Vectorize, and Workers AI enabled

## Setup

```bash
cd rag-worker

# 1. Copy the template and fill in your IDs
cp wrangler.toml.template wrangler.toml
# Edit wrangler.toml: replace YOUR_DATABASE_ID_HERE and YOUR_VECTORIZE_INDEX_NAME_HERE

# 2. Set the auth token securely (never commit this)
wrangler secret put AUTH_TOKEN

# 3. Initialize the D1 schema
wrangler d1 execute hermes-memory --file=schema.sql

# 4. Deploy
wrangler deploy
```

## Migrations

Schema changes are managed via the Memora CLI:

```bash
# From the memora repo root
python -m memora.rag_migrate status
python -m memora.rag_migrate apply
```

Migration files live in `migrations/` and are applied in sequence.

## Architecture

- **D1** (`hermes-memory`): Source of truth for facts, sync log, migration state
- **Vectorize** (`hermes-kb`): Semantic search index (embeddings)
- **KV** (`CACHE`): Embedding cache and search result cache
- **Workers AI**: BGE-M3 embeddings, BGE-Reranker, Llama-4-Scout chat

## Endpoints

| Endpoint | Purpose |
|---|---|
| `POST /embed` | Embed a text string |
| `POST /ingest` | Ingest a fact with optional vector |
| `POST /search` | Semantic search with reranking and scope filtering |
| `POST /rerank` | Rerank a list of candidate facts |
| `POST /chat` | Chat completion with search grounding |
| `POST /think` | Synthesize a cited answer with gaps |
| `POST /delete` | Delete facts by ID |
| `POST /memory/add` | Add a fact |
| `POST /memory/update` | Update a fact |
| `POST /memory/list` | List facts with filters |
| `POST /memory/sync` | Flush facts queued with `pending_vector_sync` |
| `POST /memory/stats` | Aggregate counts and pending sync count |
| `GET /health` | Liveness and model info |

## Two-Phase Commit

Writes to D1 first with `pending_vector_sync=1`, then to Vectorize, then the flag is cleared. If Vectorize fails, facts remain queryable via D1 and can be re-synced with:

```bash
curl -X POST $RAG_WORKER_URL/memory/sync \
  -H "Authorization: Bearer $RAG_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"batch_size": 50}'
```

## Security

- Auth via `Authorization: Bearer <token>` header
- Timing-safe comparison to prevent timing attacks
- Input validation on all endpoints (max payload 5MB, max batch 200)
- Scope-based access control (`personal` filters by `owner_id`, `company` filters by `scope`)
