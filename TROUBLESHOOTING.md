# Memora Troubleshooting Guide

## "My agent says Memora is unavailable"

**Cause:** `RAG_WORKER_URL` or `RAG_AUTH_TOKEN` is not set.

**Fix:**

```bash
source ~/.hermes/memora_env.sh
# Or set directly:
export RAG_WORKER_URL="https://your-worker.workers.dev"
export RAG_AUTH_TOKEN="your-secret-token"
```

Verify:

```bash
python -c "from memora.plugin import MemoraProvider; p=MemoraProvider(); print(p.is_available())"
# Should print: True
```

Run a full health check:

```bash
memora-doctor
# or JSON:
memora-doctor --json
```

---

## "GitHub sync failed" during install

**Cause:** `gh` CLI is not authenticated or you do not have write access.

**Fix:**

```bash
gh auth login
# Then re-run ./install.sh
```

---

## "memora-daemon service failed to start"

**Cause:** Python package not found in systemd's PATH.

**Fix:**

```bash
# Check logs
journalctl --user -u memora-daemon.service -n 50

# Verify Python path
which python3
# Edit the service file to use the full python3 path
systemctl --user edit memora-daemon.service
```

---

## "No RAG backend configured" in daemon logs

**Cause:** Environment variables not passed to the systemd service.

**Fix:**

```bash
# Add env vars to the service override
mkdir -p ~/.config/systemd/user/memora-daemon.service.d
cat > ~/.config/systemd/user/memora-daemon.service.d/env.conf <<'EOF'
[Service]
Environment="RAG_WORKER_URL=https://your-worker.workers.dev"
Environment="RAG_AUTH_TOKEN=your-token"
EOF
systemctl --user daemon-reload
systemctl --user restart memora-daemon.service
```

---

## "Company rule files are not being followed"

**Cause:** The files are not in the company repo root or the repo path is wrong.

**Fix:**

1. Confirm the files live at the root of the company repo (`_brain-filing-rules.md`, `_output-rules.md`, etc.).
2. Verify `custom.company_memory_dir` in `~/.hermes/config.yaml` points at the local clone.
3. Rule files are read on every provider turn. Restart your agent if you just edited the config.

---

## "memora-think returns old or missing information"

`memora_think` only knows what is in the brain.

1. Run `memora-sync` to pull the latest facts from the RAG worker and rebuild the company wiki.
2. Check `memora-doctor` for pending vector syncs. If pending > 0, the daemon or `/memory/sync` will clear them.
3. Confirm the relevant facts were added with `scope=company` and not `scope=personal` from another owner.

---

## "Per-person crons are not running"

**Cause:** The cron file is in the wrong location or the schedule format is not recognized.

**Fix:**

1. File path must be `crons/<role>-<name>/<job>.md` (e.g., `crons/ceo-vaibhav/digest.md`).
2. Frontmatter must include `schedule`, `owner`, and `prompt`:

```markdown
---
schedule: "0 9 * * 1"
owner: vaibhav
prompt: "Summarize last week's customer activity."
---
```

Cron syntax is five fields: `minute hour day month weekday` (0 and 7 are Sunday).

3. The daemon must be running: `systemctl --user status memora-daemon.service`.

---

## "Database is locked" errors

**Cause:** Concurrent access to the SQLite queue without WAL mode.

**Fix:** WAL mode is enabled by default. If you see this on an old install:

```bash
sqlite3 ~/.hermes/memora_queue_*.db "PRAGMA journal_mode=WAL;"
```

---

## "UnicodeDecodeError" from Discord webhook

**Cause:** Discord sent non-UTF8 bytes.

**Fix:** This is handled automatically in recent versions with an encoding fallback. Upgrade the plugin if it persists.

---

## "ImportError: cannot import name 'MemoraProvider'"

**Cause:** Python < 3.10 or package not installed.

**Fix:**

```bash
python3 --version  # Must be 3.10+
pip install -e /path/to/memora
```

---

## Worker Deployment Issues

### "database_id not found"

Create the D1 database first:

```bash
wrangler d1 create hermes-memory
# Copy the database_id into wrangler.toml
```

### "Vectorize index not found"

Create the Vectorize index:

```bash
wrangler vectorize create hermes-kb --dimensions=1024 --metric=cosine
```

### "Authorization failed"

Set or rotate the worker token:

```bash
wrangler secret put AUTH_TOKEN
```

Then update `RAG_AUTH_TOKEN` on every agent machine and restart the daemon.
