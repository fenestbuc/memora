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

## "GitHub sync failed" during install

**Cause:** `gh` CLI is not authenticated or you don't have write access.

**Fix:**
```bash
gh auth login
# Then re-run ./install.sh
```

## "cloudflared tunnel not starting"

**Cause:** Port 8742 is already in use.

**Fix:**
```bash
# Find what's using it
lsof -i :8742
# Or use a different port
export MEMORA_DAEMON_PORT=8743
memora-daemon --port 8743
```

## "systemd service failed to start"

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

## "No RAG backend configured" in daemon logs

**Cause:** Environment variables not passed to the systemd service.

**Fix:**
```bash
# Add env vars to the service
mkdir -p ~/.config/systemd/user/memora-daemon.service.d
cat > ~/.config/systemd/user/memora-daemon.service.d/env.conf <<EOF
[Service]
Environment="RAG_WORKER_URL=https://your-worker.workers.dev"
Environment="RAG_AUTH_TOKEN=your-token"
EOF
systemctl --user daemon-reload
systemctl --user restart memora-daemon.service
```

## "Database is locked" errors

**Cause:** Concurrent access to SQLite queue without WAL mode.

**Fix:** Already fixed in v0.4.0+ (WAL mode enabled). If you see this on an old install:
```bash
sqlite3 ~/.hermes/memora_queue_*.db "PRAGMA journal_mode=WAL;"
```

## "UnicodeDecodeError" from Discord webhook

**Cause:** Discord sent non-UTF8 bytes.

**Fix:** Fixed in v0.4.0+ with encoding fallback.

## "ImportError: cannot import name 'MemoraProvider'"

**Cause:** Python < 3.10 or package not installed.

**Fix:**
```bash
python3 --version  # Must be 3.10+
pip install -e /path/to/memora
```

## Worker Deployment Issues

### "database_id not found"

Create the D1 database first:
```bash
wrangler d1 create hermes-memory
# Copy the database_id into wrangler.toml
```

### "KV namespace not found"

Create the KV namespace:
```bash
wrangler kv:namespace create CACHE
# Copy the id into wrangler.toml
```

### "Vectorize index not found"

Create the Vectorize index:
```bash
wrangler vectorize create hermes-kb --dimensions=1024 --metric=cosine
```
