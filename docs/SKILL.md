---
name: memora
description: "Query and manage the Hermes Memora long-term memory worker (Cloudflare Workers). Semantic search, fact CRUD, stats, and nightly indexing."
version: 2.0.0
author: Memora Contributors
license: MIT
metadata:
  hermes:
    tags: [memory, rag, search, knowledge-base, cloudflare, hermes-plugin, memora]
    category: research
    related_skills: []
---

# Memora: Persistent Semantic Memory

Connects Hermes to the persistent RAG worker for cross-session long-term memory.
The RAG worker stores facts in D1 and embeddings in Vectorize, enabling semantic
search across indexed memories.

## When This Skill Activates

Use when the user:
- Asks to recall something from past sessions or long-term memory
- Wants to search the knowledge base
- Needs to add, update, or delete a persistent fact
- Asks for memory stats or to export the memory database
- Wants to run nightly maintenance (`memora-nightly`) or generate weekly digests (`memora-weekly`)

## Tools

Memora provides the following tools:

- **memora_search**: Semantic search across all indexed memories. Returns ranked results by relevance. Use for conceptual queries.
  - Parameters: `query` (string), `top_k` (integer, default 10)
- **memora_list**: List facts with SQL filters. Good for browsing or finding facts by exact category.
  - Parameters: `category` (string), `search` (string), `limit` (integer), `offset` (integer)
- **memora_stats**: Get a breakdown of facts by category and total count.
- **memora_add**: Persist a new fact to long-term memory.
  - Parameters: `content` (string), `category` (string, default "memory"), `id` (string, optional)
- **memora_update**: Update an existing fact by ID.
- **memora_delete**: Delete facts by ID.

## Core Workflows

### 1. Recall & Search
1. When the user asks a question about past context, call `memora_search` with the user's query.
2. If exact categories are known (e.g., `business`, `projects`), use `memora_list`.
3. Do not assume facts are missing if they were just added. Vector indexing has a slight latency.

### 2. Capture & Update
1. After learning something important (e.g., investor preferences, strategic pivots), call `memora_add`.
2. Categorize appropriately (e.g., `user` for preferences, `business` for strategy, `project` for execution).
3. If a previous fact is contradicted, use `memora_update` to revise it rather than adding a duplicate.

### 3. Maintenance & CLI Scripts
The Memora Python package provides CLI scripts for workspace maintenance.
Run these via the terminal tool when requested by the user or scheduled via cron:

- **`memora-nightly`**: Nightly brain indexer. Scans workspace files (mtime hashing), extracts entities for the wiki, and detects contradictory facts. Run via `cd ~/hermes-workspace && memora-nightly`.
- **`memora-weekly`**: Generates a weekly digest of memory activity. Run via `cd ~/hermes-workspace && memora-weekly`.

## Installing Memora for Other Agents

If the user asks how to set up Memora on a new agent or workspace, provide these steps:

1. **Install Plugin:**
   ```bash
   cd ~/.hermes/plugins/
   git clone https://github.com/fenestbuc/memora.git
   pip install -e memora/
   ```
2. **Enable in config (`~/.hermes/config.yaml`):**
   ```yaml
   memory:
     provider: memora
   plugins:
     enabled:
       - memora
   ```
3. **Configure Environment:**
   Set `RAG_WORKER_URL` and `RAG_AUTH_TOKEN`.
4. **Install Skill:**
   Copy `docs/SKILL.md` from the repo into `~/.hermes/skills/memora/SKILL.md`.

## Troubleshooting & Constraints

- **Trivial Messages:** The plugin auto-filters short (< 15 chars) or trivial messages (e.g., "ok", "go ahead") from being ingested to prevent corpus pollution.
- **Vector Index Latency:** Facts added via `memora_add` may take a few seconds to appear in `memora_search` due to embedding generation. This is normal.
- **Worker Configuration:** The Cloudflare worker code is managed separately from the Hermes plugin. The plugin assumes a running, healthy worker. Check health using `memora_stats`.
