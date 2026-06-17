# Memora vs. GBrain Company Brain: Audit and Improvement Opportunities

This document summarizes what we learned from the updated `garrytan/gbrain` repository and which ideas are worth bringing into Memora.

**Important context:** GBrain is primarily a codebase intelligence engine. Its company-brain tutorial shows how to extend that engine into a shared organizational memory. Memora is purpose-built for company memory from the start. We should therefore borrow GBrain's multiplayer organizational abstractions, not its code-specific features such as symbol/file indexing or code-aware graph traversal.

Last reviewed: 2026-06-18.

---

## 1. What GBrain's company brain does

GBrain is a local-first semantic knowledge graph (PGLite or Postgres). Its company-brain layer adds:

1. **Sources inside one brain.** A source is a named content repo inside a single database. `slug` collisions are okay across sources.
2. **Two scoping models.**
   - Model A: separate sources plus OAuth scoping (`--source`, `--federated-read`). SQL-enforced read/write isolation.
   - Model B: one source with per-person subdirectories (`partners/<slug>/`). Isolation is convention-only, but ops stay simple.
3. **HTTP MCP server with OAuth 2.1.** `gbrain serve --http` exposes the brain to remote clients with scoped access.
4. **Per-person folders inside each source.** Each teammate gets `USER.md`, `concepts/`, `sources/`, and personal work subfolders.
5. **Per-person crons.** Markdown files in `crons/<client>/` with YAML frontmatter (`schedule`, `client`, prompt).
6. **Shared root rule files.** `_brain-filing-rules.md`, `_output-rules.md`, `_excluded-people.md`, `_operating-rules.md` live at the repo root and are read by every skill.
7. **Per-person / scoped skills.** Skills can declare `allowed_clients` in frontmatter.
8. **`gbrain think`.** Synthesized, cited answers with explicit gap analysis.
9. **Graph links.** Beyond vectors: explicit `add_link` relationships between pages/entities.
10. **Self-healing.** `gbrain autopilot`, `gbrain doctor --remediate --target-score 90 --max-usd 5`.
11. **Thin-client install.** Teammates without the full GBrain stack can run `gbrain init --mcp-only`.

---

## 2. Memora's company brain as of v0.5.1

| Capability | Status |
|---|---|
| Multiplayer onboarding via company GitHub repo | Done: `install.sh` pushes `members/{role}-{name}.json` |
| Personal vs company scope filtering | Done: `owner_id` + `scope` column in D1 |
| Per-person company repo workspace | Partial: flat member JSON only |
| Shared rule files | Not implemented |
| Per-person/role crons | Not implemented (only CEO digest + nightly evals) |
| Synthesized, cited answers (`think`) | Not implemented (`prefetch()` returns raw bullets) |
| Graph links (`linked_ids`) | Partial: only `parent_id` |
| Self-healing health checks | Not implemented |
| Remote OAuth/MCP server | Not implemented (single `AUTH_TOKEN`) |
| Thin-client install for non-Hermes agents | Not implemented |
| Deferred/offline embeddings | Partial: `pending_vector_sync` + `/memory/sync` exist |

---

## 3. Feature map: GBrain → Memora

| GBrain primitive | Memora recommendation | Effort |
|---|---|---|
| Model B per-person subfolders | Add `members/{role}-{name}/USER.md` + subfolders. Update onboarding, wiki builder, org graph. | Low |
| Shared root rule files | Load `_brain-filing-rules.md`, `_output-rules.md`, `_excluded-people.md`, `_operating-rules.md` into the provider system prompt. | Low |
| `gbrain think` | Add a `/think` endpoint (worker-side) and `memora_think` tool. Retrieve, then ask an LLM to cite sources and call out gaps. | Medium |
| `gbrain doctor` | Add `memora-doctor` CLI: health, stats, pending sync, queue depth, repo lag. | Low–Medium |
| Deferred/offline embeddings | Complete the path: daemon periodically calls `/memory/sync`; provider queues with `pending_vector_sync=1`. | Low |
| Per-person crons | Add `crons/{role}-{name}/` markdown convention and a scanner in the daemon. | Medium |
| Graph links | Add `linked_ids` JSON column and hydrate linked facts in search. Add `memora_link` tool. | Medium |
| Scoped skills | Parse `allowed_clients`/`allowed_roles` frontmatter in skill dispatcher. | Medium |
| Model A sources + OAuth | Add `source` column + per-source auth tokens. Requires worker auth layer. Keep as v0.6+. | High |
| Thin-client / MCP bridge | Package a minimal MCP/stdio client that talks to the RAG worker. | Medium–High |
| Local-first PGLite/Postgres backend | Add optional embedded Postgres backend to remove Cloudflare dependency. Large architectural change. | High |

---

## 4. Design decisions

1. **Start with Model B, not Model A.** GBrain's Model A gives stronger isolation but introduces OAuth, per-client tokens, and a multi-tenant worker. Memora's existing stack is single-tenant with `owner_id` + `scope`. Model B (folder conventions inside a shared GitHub repo) keeps the 10-minute install intact and ships now.
2. **Worker-side `/think`.** Putting synthesis in the RAG worker keeps the provider thin and lets Claude/Cursor users benefit later without a Python rewrite.
3. **Rule files are opt-in.** A company repo without `_brain-filing-rules.md` works exactly as before. If the files exist, the provider loads them into the system prompt.
4. **No secrets in the public repo.** Auth stays in `wrangler secret`, tokens stay in `~/.hermes`, and real names never appear in docs or tests.

---

## 5. Open questions

1. Do we implement source-level OAuth in v0.6 or stay with folder-based scoping?
2. Should `memora think` default to company+personal blend or respect the user's active `scope`?
3. Which shared rule files does Kubar need immediately? Start with `_brain-filing-rules.md` and `_output-rules.md` only?
4. Should `~/.hermes/plugins/memora/` become a symlink to the repo source or be re-installed after each merge?
5. Do we create a separate `memora-testbed` repo for integration testing, or use temporary clones?

---

## 6. References

- GBrain company-brain tutorial: `gbrain/docs/tutorials/company-brain.md`
- GBrain brain/source model: `gbrain/docs/architecture/brains-and-sources.md`
- GBrain MCP deployment: `gbrain/docs/mcp/DEPLOY.md`
- Memora repo: `https://github.com/fenestbuc/memora`
