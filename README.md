# Memora Enterprise: Autonomous Organizational Brain

> **Memora** — Decentralized, self-healing organizational memory for AI-native startup teams.

Memora Enterprise transforms individual Hermes agents into a **synchronized, multi-agent organizational brain**. Designed for lean startup teams (like Kubar Labs), it maps 1:1 to your Individual Contributors (ICs) to create Digital Twins. It intercepts their daily work, routes actionable tasks to Kanban swarms, syncs company knowledge via Git, and autonomously optimizes its own prompts overnight.

---

## Core Value Proposition

Traditional AI agents suffer from amnesia. Single-player RAG systems isolate knowledge. Memora solves this by building a decentralized nervous system:

1. **Digital Twins for Every IC:** Every team member runs a local Hermes agent wrapped in a Memora `FastAPI` daemon. These daemons are exposed to the public internet securely via automated **Cloudflare Tunnels**, allowing Discord webhooks and Notion integrations to proxy through the user's specific context.
2. **Autonomous Kanban Swarms:** Memora intercepts new facts (e.g. a Notion doc update) and runs them through a blazing-fast **LLM Triage Gate** (`gemini-2.5-flash-lite`). If the fact is actionable, it auto-spawns a multi-agent Kanban swarm (e.g., an `analyst` and `writer` subagent) to execute the work immediately.
3. **Multi-Tenant Privacy:** Syncs to a centralized Cloudflare Workers Vectorize database using explicit `owner_id` and `tenant_id` tagging. Personal drafts stay private; company facts become global.
4. **Git-Backed Shared State & Zero-Conflict Sync:** The system's source-of-truth is a shared GitHub repository. Facts and golden eval datasets are stored as `.jsonl` (JSON Lines) files to eliminate git merge conflicts when multiple agents push concurrent updates.
5. **The Autonomous LLMOps Flywheel:** 
   - **Capture:** When an IC overrides their agent (e.g., reassigning a Kanban ticket), the **Feedback Interceptor** captures the correction and appends it to the golden evaluation dataset (`eval_golden.jsonl`).
   - **Optimize:** Every night, the CEO's orchestrator node runs `memora-evals`. If Swarm Routing accuracy drops, it spawns an `OpenCode` agent to re-write its own prompt logic (`prompts.py`).
   - **Safety:** The optimizer is guarded by an AST Compiler Check to prevent fatal syntax errors, and a Circuit Breaker that escalates to human engineers via a Kanban ticket if it fails 3 times.

---

## Architecture

Memora consists of four primary loops interacting seamlessly:

### 1. The Multi-Platform Ingestion Loop
```text
[Discord Webhooks] \
[Notion Polling]   --> [memora-daemon.service] -> [Cloudflare Tunnel] -> [Local RAG Search/Add]
```
*Locally running daemon processes expose agents to the outside world securely.*

### 2. The Execution Loop
```text
[New Fact Ingested] -> [Triage Gate] --(Actionable)--> [swarm_manager] -> [Hermes Kanban Dispatch]
```
*Turns passive knowledge drops into immediate execution.*

### 3. The Synchronization Loop
```text
[Local SQLite Queue (WAL)] -> [Batch Flush to RAG]
                           -> [Append to Local JSONL] -> [Auto-Merge GitHub PR]
```
*Ensures every agent stays perfectly in sync without manual CEO intervention.*

### 4. The Self-Healing Optimization Loop (CEO Node Only)
```text
[Nightly Cron] -> [Run memora-evals] --(Score < 95%)--> [OpenCode rewrites prompts.py]
                                                    |-> [AST Compilation Check]
                                                    |-> [Evaluate New Prompts] -> [Commit to Git]
```

---

## Installation & Onboarding

Memora enforces strict, interactive onboarding to ensure the organizational topology is correct.

**Run the installer on every IC's machine:**

```bash
cd ~/.hermes/plugins/memora
./install.sh
```

**The installer will autonomously:**
1. Prompt for `First Name`, `Role` (e.g., CEO, GTM, Engineering), and the `Company GitHub Repo URL`.
2. Push a member declaration (e.g., `members/GTM-Sreyan.json`) to the Git repo.
3. Configure and start the `memora-daemon.service` via `systemd`.
4. Spawn a `cloudflared` tunnel, logging the secure public URL to `~/.hermes/memora_tunnel.txt`.
5. (If role is `CEO`) Register the nightly LLMOps optimizer cron job and CEO Digest hooks.

> **Note:** If the repository URL is not known, the script halts and provides instructions to the CEO on how to instantiate the foundational repo.

---

## Evaluation CLI (`memora-evals`)

Memora includes a robust, LLM-as-a-judge evaluation suite built-in.

```bash
# Run the full suite against the current golden dataset
memora-evals --golden data/eval_golden.jsonl --output reports/latest.json
```

**What it evaluates:**
- **RAG Retrieval Quality:** MRR, Hit Rate@K, NDCG@K.
- **Kanban Swarm Accuracy:** Measures if the Triage Gate correctly identified actionable tickets and assigned the correct subagent persona.
- **CEO Digest Quality:** An LLM judges the generated digests on Completeness, Actionability, Conciseness, and Accuracy.

---

## Development & Maintenance

### File Structure
```
memora/
|-- install.sh               # Cross-platform installer + Cloudflared + Systemd setup
|-- AGENT_INSTRUCTIONS.md    # Operating manual for Hermes Twin Agents
|-- data/
|   |-- eval_golden.jsonl    # Line-delimited ground truth dataset
|-- src/memora/
|   |-- plugin.py            # Core MemoryProvider, tool interception, RAG API
|   |-- daemon.py            # FastAPI background webhook listener
|   |-- discord_proxy.py     # Discord webhook payload parsing
|   |-- mcp_sync.py          # Notion polling 
|   |-- swarm_manager.py     # Kanban task instantiation wrapper
|   |-- triage.py            # LLM-gated actionability checks
|   |-- feedback_interceptor.py # Human-in-the-loop data capture
|   |-- optimizer.py         # Autonomous OpenCode prompt tuning
|   |-- ceo_digest.py        # Org graph, alerts, and safe auto-merging
|   |-- org_graph.py         # Topology parsing
|   |-- evaluations.py       # Metrics engine (MRR, LLM-as-a-judge)
|   |-- run_evals.py         # CLI wrapper
|-- tests/                   # 200+ TDD Pytest unit/integration tests
```

Run tests from the repo root:
```bash
python -m pytest tests/ -v
```

---

## License

MIT — see [LICENSE](LICENSE).