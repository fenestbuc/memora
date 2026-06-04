# Memora RAG Worker

Cloudflare Workers backend for Memora's semantic memory. Uses D1 (SQLite), Vectorize (embeddings), and Workers AI (LLM / embeddings / reranking).

## Prerequisites

- [Wrangler CLI](https://developers.cloudflare.com/workers/wrangler/install-and-update/)
- Cloudflare account with D1, Vectorize, and Workers AI enabled

## Setup

```bash
cd rag-worker

# 1. Copy the template and fill in your IDs
cp wrangler.toml.template wrangler.toml
# Edit wrangler.toml: replace YOUR_DATABASE_ID_HERE and YOUR_KV_NAMESPACE_ID_HERE

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

## Security

- Auth via `Authorization: Bearer <AUTH_TOKEN>` header
- Timing-safe comparison to prevent timing attacks
- Input validation on all endpoints (max payload 5MB, max batch 200)
- Scope-based access control (`personal` filters by `owner_id`, `company` filters by `scope`)

## Two-Phase Commit

Writes to D1 first with `pending_vector_sync=1`, then to Vectorize, then the flag is cleared. If Vectorize fails, facts remain queryable via D1 and can be re-synced with:

```bash
curl -X POST $RAG_WORKER_URL/memory/sync \
  -H "Authorization: Bearer $RAG_AUTH_TOKEN" \
  -d '{"batch_size": 50}'
```
