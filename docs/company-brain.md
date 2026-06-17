# Tutorial: Turn your personal Memora into a company brain

This tutorial assumes you already have a working personal Memora install: the Hermes agent, the Memora plugin, and a deployed Cloudflare RAG worker. Now you want your team to share the same brain, with each person seeing only what they are allowed to see.

**Time:** about 60 minutes on top of the personal install.
**Cost:** Cloudflare Workers free tier is usually enough for a small team. Embeddings run through Workers AI or your configured provider.

If you have not set up the personal install yet, do that first. Come back when `memora_stats` works from your agent.

---

## Part 1: The mental model

The personal brain is a single-user system: one agent, one `~/.hermes/memora.json`, your facts. The company brain adds three things on top of the same stack:

1. **A company GitHub repo as the shared source of truth.** Facts with `scope=company` sync here. Personal facts stay in the RAG backend but are filtered by `owner_id`.
2. **Per-person workspace folders inside the repo.** Each teammate gets their own `members/<role>-<name>/` directory with a profile, concepts, and personal notes that are still visible to the team by default.
3. **Shared rule files at the repo root.** The agent reads `_brain-filing-rules.md`, `_output-rules.md`, and similar files before it decides how to store or answer things.

### What this is NOT

It is not a different product. The plugin, the RAG worker, and the SQLite queue stay the same. We are adding a multiplayer layer, not replacing the engine.

It is also not a thin-client-everywhere system today. Each teammate runs their own Memora plugin (Hermes, Claude, Cursor, etc.) pointed at the same RAG worker and the same company repo. A future version may ship a smaller MCP-only client.

### What you get

- **Shared memory.** The whole team queries the same brain. Alice's customer notes show up when Bob asks about that customer, with a citation back to the source.
- **Scoped privacy.** Personal notes stay personal. Company facts are shared. Sensitive folders can be isolated by repo conventions.
- **One sync pipeline.** The daemon pulls facts from RAG, rebuilds the wiki, and pushes to GitHub on a schedule.
- **One operating burden.** One worker, one repo, one set of crons.

---

## Part 2: The CEO or admin sets up the company repo

Create an empty GitHub repository for your company memory. For example:

```bash
gh repo create your-org/company-memory --public --add-readme
```

Clone it into your workspace:

```bash
cd ~/hermes-workspace
git clone https://github.com/your-org/company-memory.git
```

Add the shared rule files at the root. These are optional but strongly recommended:

```bash
cd company-memory
cat > _brain-filing-rules.md <<'EOF'
# Brain filing rules

When saving a new fact, choose the category in this order:
1. `people` for individuals.
2. `companies` for customers, partners, or investors.
3. `meetings` for notes tied to a specific call.
4. `strategy` for pivots, decisions, or board-level topics.
5. `projects` for execution work.
6. `integrations` for tools, APIs, or vendor notes.
EOF

cat > _output-rules.md <<'EOF'
# Output rules

- Cite sources using `[category/date]`.
- Never hallucinate a Slack URL. Build it from API data only.
- Use plain language. Avoid AI-sounding filler.
EOF

git add .
git commit -m "chore: add initial company brain rule files"
git push origin main
```

You do not have to use these exact categories. The important part is that the file is versioned and every agent reads it.

---

## Part 3: Onboard each teammate

Each teammate runs the installer with the company repo URL:

```bash
cd /tmp
curl -fsSL https://raw.githubusercontent.com/fenestbuc/memora/main/install.sh | bash -s -- \
  --name Alice \
  --role Sales \
  --repo https://github.com/your-org/company-memory.git
```

Or, if they already cloned the repo locally:

```bash
cd memora
./install.sh --name Alice --role Sales --repo https://github.com/your-org/company-memory.git
```

The installer:

1. Installs or updates the Memora plugin in `~/.hermes/plugins/memora/`.
2. Writes `~/.hermes/memora.json` with `first_name`, `role`, and `company_github_repo`.
3. Opens a PR that adds `members/sales-alice/USER.md` and creates empty subfolders:
   - `members/sales-alice/concepts/`
   - `members/sales-alice/meetings/`
   - `members/sales-alice/customers/`
   - `members/sales-alice/sources/`
4. Clones the company repo into `~/hermes-workspace/company-memory`.
5. Installs the skill file in `~/.hermes/skills/memora/`.

A typical member PR looks like this:

```
members/
└── sales-alice/
    ├── USER.md
    ├── concepts/
    │   └── icp-criteria.md
    ├── customers/
    │   └── acme-co.md
    ├── meetings/
    │   └── 2026-06-18-acme-renewal.md
    └── sources/
        └── playbook-links.md
```

### Pair the teammate with their context

Before they run their first query, seed their folder with 5–10 pages that are relevant to them: their ICP definition, active deals, recurring meeting formats, and links to dashboards they check. This is the difference between "this is a cool tool" and "this already knows me."

---

## Part 4: Scoping and sources

Memora currently uses two access axes:

1. **`owner_id`** — the teammate's first name from `~/.hermes/memora.json`.
2. **`scope`** — `personal` (default), `company`, or `global`.

### How scoping works

- `scope=personal` facts are returned only when the query matches the same `owner_id`.
- `scope=company` or `scope=global` facts are visible to everyone.
- Folder conventions inside the company repo add a second, softer boundary. For example, sensitive HR notes can live in `members/hr-carol/private/` and be excluded from the wiki index via `_output-rules.md`.

### Two models to choose from

**Model B (recommended for most teams): one source, directory-based per-person scoping.** This is what Memora ships today. Every teammate's writes go to their own `members/<role>-<name>/` folder. Shared content lives at the repo root or in explicit `shared/` folders. Ops stay simple because there is only one RAG worker and one auth token.

**Model A (future): separate sources with OAuth scoping.** Each teammate gets their own OAuth client and can only read/write their sources. This gives stronger isolation but requires a multi-tenant auth layer in the worker. If you need this, follow the roadmap in `docs/gbrain-audit.md`.

---

## Part 5: Prepopulate a teammate's slice

When a new teammate joins, ask the CEO or team lead to create these files before the teammate runs their first query:

- `members/<role>-<name>/USER.md` — role, focus areas, top 3 priorities, preferred answer style.
- `members/<role>-<name>/concepts/` — frameworks and recurring themes that belong to them.
- `members/<role>-<name>/sources/` — links to dashboards, docs, and inboxes they care about.
- 2–3 example brain entries that demonstrate the shape: a customer page, a meeting note, an idea.

This takes about 20 minutes. The teammate's first query then returns context that feels specific to them.

---

## Part 6: Running your first shared query

Ask the agent:

```text
memora think "What do we know about acme-co? When did we last talk to them?"
```

The agent:

1. Searches the brain for `acme-co` across scopes where you have access.
2. Calls Memora's synthesis endpoint.
3. Returns a sourced answer, for example:

```markdown
## Answer

The most recent contact with acme-co was a renewal discussion on 2026-05-18, attended by Alice and acme-co's CTO. Key points:

- They are upgrading from team to enterprise.
- Annual contract value is moving from $48K to $180K.
- Driver: a new compliance requirement they must meet by Q3.

Sources:
- [meetings/2026-05-18] acme renewal meeting
- [customers/acme-co] account overview

**Gap:** No follow-up has been filed since the 2026-05-18 meeting. If a follow-up happened, it is not in the brain yet.
```

Notice the gap note. The brain tells you what it does not know instead of inventing it.

---

## Part 7: Operating the company brain

### Background sync

The daemon runs an hourly loop that:

1. Pulls the latest company repo.
2. Fetches `scope=company` facts from RAG.
3. Writes them to `facts/*.jsonl` to avoid git merge conflicts.
4. Runs `wiki_builder` to regenerate the markdown wiki.
5. Commits and pushes.

You can also trigger it manually:

```bash
cd ~/hermes-workspace
memora-sync
```

### Maintenance commands

| Command | Purpose |
|---|---|
| `memora-nightly` | Decay old facts, run evaluations, detect contradictions. |
| `memora-weekly` | Generate a weekly digest. |
| `memora-evals` | Run the routing/RAG evaluation suite. |
| `memora doctor` | Health check: worker reachability, pending vector sync, queue depth, repo sync lag. |

### Keeping the brain healthy

Run this once a day or put it in cron:

```bash
memora doctor
```

It prints a short report. If it reports pending vector syncs or a stale repo, run `memora-sync`.

---

## Part 8: Shared rule files

Files at the company repo root are automatically loaded into the agent's system prompt. The ones we recommend:

- `_brain-filing-rules.md` — where new facts belong.
- `_output-rules.md` — citation style, vocabulary, deterministic links.
- `_excluded-people.md` — names that must never be stored or attributed.
- `_operating-rules.md` — when to write to the brain vs a scratchpad, when to ask for confirmation.

Because these are plain markdown in git, changing a rule is just a PR. Every agent that talks to Memora picks up the new rule on its next turn.

---

## Part 9: Slack / Discord integration

Memora ships with a Discord webhook listener. Point your Discord integration at the public tunnel URL written to `~/.hermes/memora_tunnel.txt`. The daemon parses messages, searches the brain, and persists the turn so thread continuity lasts up to 24 hours.

For Slack, the recommended pattern is two jobs:

1. A **signal scan** every 5–15 minutes that surfaces new mentions or decisions.
2. A **nightly archive** that stores full conversation history.

Keep a `topic-registry.json` that maps Slack channel IDs to friendly topic names so your agents never reference raw channel IDs. See GBrain's Slack notes for the full pattern.

---

## Part 10: Cost, limits, and gotchas

### Expected costs

- **Cloudflare Workers free tier:** covers most small-team usage.
- **Vectorize + D1:** generous free limits; monitor in the Cloudflare dashboard.
- **LLM synthesis (`memora think`):** billed per token through your configured provider.

For a 10–25 person company, expect the AI side to stay under low tens of dollars a month at moderate usage.

### Common gotchas

**"My teammate cannot see any company facts."**

Check that the fact was saved with `scope=company` (not `personal`). The agent must call `memora_add` with the explicit scope, or the daemon must sync it.

**"Sync is slow the first time."**

The first `memora-sync` may fetch many facts. Subsequent runs are incremental. If you have thousands of facts, the initial export can take a few minutes.

**"I see facts I should not see."**

Verify the `scope` and `owner_id` on the fact. Personal facts should only match queries from the same owner. If you suspect a leak, file a bug with the exact query and the returned fact IDs.

**"The synthesized answer is stale."**

`memora think` only knows what is in the brain. If the sources are old, the answer will be old. Run `memora-sync` and re-ingest recent notes.

**"The generated Slack link is wrong."**

Never let the LLM compose Slack URLs. Build them from workspace ID + channel ID + message timestamp. Put this rule in `_output-rules.md`.

**"Do I put secrets in wrangler.toml?"**

No. Secrets go through `wrangler secret put`. The public repo must never contain tokens.

---

## Part 11: What you built

You now have:

- A shared company memory repo with per-person workspace folders.
- Shared rule files that govern how every agent files and answers.
- A synthesis command (`memora think`) that returns cited answers with gap analysis.
- A health command (`memora doctor`) to catch drift early.
- A sync pipeline that keeps GitHub and the RAG backend aligned.

Next steps:

- Wire ingestion from external systems (Slack, Notion, meeting transcripts).
- Add per-person or per-role crons under `crons/<role>-<name>/`.
- Explore stronger isolation (GBrain Model A) if your team grows past 25 people or needs department-level access control.

For the deeper technical comparison, see `docs/gbrain-audit.md`.
