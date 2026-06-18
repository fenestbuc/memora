"""Memora background daemon — HTTP wrapper for Discord/MCP listeners and autonomous Wiki syncing.

Provides a lightweight FastAPI server that exposes the Discord webhook
parsing and RAG proxy logic so it can run continuously as a systemd
service.

It also runs a background loop to autonomously synchronize the company memory
and build the comprehensive wiki asynchronously.
"""

from __future__ import annotations

import argparse
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict

import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from memora.discord_proxy import parse_discord_payload, proxy_query
from memora._version import __version__

logger = logging.getLogger(__name__)

# --- Background Repo Sync Loop ---
async def repo_sync_loop():
    """Continuously synchronizes the company memory repo in the background."""
    # Run once an hour. 
    # Use asyncio.sleep so we don't block the async event loop.
    import yaml
    
    sync_interval_seconds = 3600  # 1 hour
    
    # Read the custom repo path from hermes config if available
    repo_path = os.path.expanduser("~/hermes-workspace/kubarlabs-memory")
    hermes_config = os.path.expanduser("~/.hermes/config.yaml")
    if os.path.exists(hermes_config):
        try:
            with open(hermes_config, "r") as f:
                cfg = yaml.safe_load(f) or {}
                if "custom" in cfg and "company_memory_dir" in cfg["custom"]:
                    repo_path = cfg["custom"]["company_memory_dir"]
        except Exception as e:
            logger.warning(f"Could not read config.yaml for repo path: {e}")

    logger.info(f"Background repo sync loop started. Target repo: {repo_path}")
    
    while True:
        try:
            # Sleep first to avoid a thundering herd on restart
            await asyncio.sleep(60) 
            
            # Execute the sync synchronously in a thread
            def _do_sync():
                if os.path.exists(repo_path):
                    try:
                        # Late import to prevent circular dependencies if any
                        from memora.repo_sync import sync_repo
                        logger.info("Executing autonomous background repo sync...")
                        sync_repo(repo_path)
                    except Exception as e:
                        logger.error(f"Background sync failed: {e}")
                else:
                    logger.warning(f"Company memory repo not found at {repo_path}. Skipping autonomous sync.")
            
            await asyncio.to_thread(_do_sync)
            
            # Sleep until next interval
            await asyncio.sleep(sync_interval_seconds - 60)
            
        except asyncio.CancelledError:
            logger.info("Repo sync loop cancelled")
            break
        except Exception as e:
            logger.error(f"Unexpected error in repo sync loop: {e}")
            await asyncio.sleep(300) # Sleep 5 minutes on error before retry

async def memory_sync_loop():
    """Periodically flush any facts that were queued with pending_vector_sync."""
    from .http_client import HttpClient, HttpConfig

    base_url = os.environ.get("RAG_WORKER_URL", "").rstrip("/")
    token = os.environ.get("RAG_AUTH_TOKEN", "")
    interval_seconds = float(os.environ.get("MEMORA_SYNC_INTERVAL_SECONDS", "900"))

    if not base_url or not token:
        logger.warning("RAG_WORKER_URL or RAG_AUTH_TOKEN not set; memory sync loop is disabled")
        while True:
            await asyncio.sleep(interval_seconds)

    client = HttpClient(HttpConfig(base_url=base_url, token=token))
    logger.info("Background memory sync loop started (interval=%ss)", interval_seconds)

    while True:
        try:
            await asyncio.sleep(interval_seconds)
            result = await asyncio.to_thread(client.post, "/memory/sync", {})
            synced = result.get("synced", 0)
            if synced:
                logger.info("Deferred memory sync flushed %d facts", synced)
        except asyncio.CancelledError:
            logger.info("Memory sync loop cancelled")
            break
        except Exception as e:
            logger.error("Memory sync loop error: %s", e)
            await asyncio.sleep(300)


async def per_person_cron_loop():
    """Run scheduled per-member crons from the company repo."""
    from .company_rules import resolve_company_memory_dir
    from .cron_scanner import due_jobs, run_cron_job
    from .http_client import HttpClient, HttpConfig

    base_url = os.environ.get("RAG_WORKER_URL", "").rstrip("/")
    token = os.environ.get("RAG_AUTH_TOKEN", "")
    interval_seconds = 60

    if not base_url or not token:
        logger.warning("RAG worker not configured; per-person cron loop is disabled")
        while True:
            await asyncio.sleep(interval_seconds)

    client = HttpClient(HttpConfig(base_url=base_url, token=token))
    company_dir = resolve_company_memory_dir(None)
    logger.info("Per-person cron loop started")

    last_ran: set[str] = set()

    while True:
        try:
            await asyncio.sleep(interval_seconds)
            jobs = due_jobs(company_dir)
            for job in jobs:
                key = f"{job.path}:{job.schedule}:{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}"
                if key in last_ran:
                    continue
                last_ran.add(key)
                # cap history to avoid memory growth
                if len(last_ran) > 1000:
                    last_ran = set(list(last_ran)[-500:])

                logger.info("Running cron job: %s", job.path)

                def _run():
                    result = run_cron_job(job, lambda p: client.post("/think", p))
                    answer = result.get("answer", "")
                    if answer:
                        client.post("/memory/add", {
                            "content": f"Cron '{job.path.name}' result\n\n{answer}",
                            "category": "automations",
                            "owner_id": job.owner,
                            "scope": "company",
                        })
                    return result

                try:
                    await asyncio.to_thread(_run)
                except Exception as e:
                    logger.error("Cron job %s failed: %s", job.path, e)
        except asyncio.CancelledError:
            logger.info("Per-person cron loop cancelled")
            break
        except Exception as e:
            logger.error("Per-person cron loop error: %s", e)
            await asyncio.sleep(300)


from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Start background tasks
    repo_task = asyncio.create_task(repo_sync_loop())
    sync_task = asyncio.create_task(memory_sync_loop())
    cron_task = asyncio.create_task(per_person_cron_loop())
    yield
    # Shutdown: Cancel tasks
    repo_task.cancel()
    sync_task.cancel()
    cron_task.cancel()
    try:
        await repo_task
    except asyncio.CancelledError:
        pass
    try:
        await sync_task
    except asyncio.CancelledError:
        pass
    try:
        await cron_task
    except asyncio.CancelledError:
        pass

app = FastAPI(title="Memora Daemon", version=__version__, lifespan=lifespan)

# Lazy-initialized search callable and provider instance
_search_fn: Callable[[str], str] | None = None
_provider_instance: Any = None
_provider_lock = asyncio.Lock()


def _get_search_fn() -> Callable[[str], str]:
    """Return a callable that runs RAG search.

    Tries to use an initialized MemoraProvider if environment variables
    are present, otherwise returns a stub.
    """
    global _search_fn, _provider_instance
    if _search_fn is not None:
        return _search_fn

    try:
        from memora.plugin import MemoraProvider

        provider = MemoraProvider()
        if provider.is_available():
            provider.initialize(
                session_id="daemon",
                hermes_home=os.path.expanduser("~/.hermes"),
            )
            _provider_instance = provider
            _search_fn = provider.prefetch
            logger.info("MemoraProvider initialized for daemon search")
            return _search_fn
    except Exception as exc:
        logger.debug("Could not initialize MemoraProvider: %s", exc)

    def _stub(query: str) -> str:
        return f"[daemon stub] No RAG backend configured. Query was: {query}"

    _search_fn = _stub
    return _search_fn


@app.get("/health")
async def health() -> Dict[str, str]:
    """Liveness probe."""
    return {"status": "ok", "service": "memora-daemon"}


@app.post("/discord/webhook")
async def discord_webhook(request: Request) -> JSONResponse:
    """Receive a Discord webhook payload, parse it, and proxy through RAG.

    Thread continuity: messages in the same Discord channel share a
    session_id (24h TTL) so conversational context persists.
    """
    body_bytes = await request.body()
    try:
        payload = parse_discord_payload(body_bytes)
    except Exception as exc:
        logger.warning("Failed to parse Discord payload: %s", exc)
        return JSONResponse({"error": "Invalid payload"}, status_code=400)

    channel_id = payload.get("channel_id", "")

    # Thread continuity: get or create session for this channel
    from memora.discord_sessions import get_or_create_session
    session_id = get_or_create_session(channel_id)

    # Temporarily set provider session context for RAG search
    global _provider_instance
    old_session = None
    response_text = ""

    async with _provider_lock:
        if _provider_instance is not None:
            old_session = getattr(_provider_instance, "_session_id", None)
            _provider_instance._session_id = session_id

        search_fn = _get_search_fn()

        try:
            response_text = proxy_query(payload, search_fn)
        finally:
            if _provider_instance is not None and old_session is not None:
                _provider_instance._session_id = old_session

    # Persist this turn for future continuity (outside lock to avoid holding it during I/O)
    if _provider_instance is not None:
        try:
            _provider_instance.sync_turn(
                user_content=payload.get("content", ""),
                assistant_content=response_text,
                session_id=session_id,
            )
        except Exception as exc:
            logger.debug("Could not persist Discord turn: %s", exc)

    return JSONResponse({
        "response": response_text,
        "author": payload.get("author", "unknown"),
        "channel_id": channel_id,
        "session_id": session_id,
    })


@app.post("/discord/parse")
async def discord_parse(request: Request) -> JSONResponse:
    """Parse a raw Discord webhook payload and return structured fields."""
    body_bytes = await request.body()
    try:
        payload = parse_discord_payload(body_bytes)
    except Exception as exc:
        logger.warning("Failed to parse Discord payload: %s", exc)
        return JSONResponse({"error": "Invalid payload"}, status_code=400)
    return JSONResponse(payload)


@app.post("/discord/query")
async def discord_query(request: Request) -> JSONResponse:
    """Run a RAG query for a pre-parsed Discord payload."""
    data = await request.json()
    if not isinstance(data, dict):
        return JSONResponse(
            {"error": "Expected JSON object"}, status_code=400
        )

    payload = data.get("payload")
    if payload is None:
        return JSONResponse(
            {"error": "Missing 'payload' field"}, status_code=400
        )

    search_fn = _get_search_fn()
    response_text = proxy_query(payload, search_fn)
    return JSONResponse({"response": response_text})


# ---------------------------------------------------------------------------
# Tunnel support — pluggable backends
# ---------------------------------------------------------------------------

TUNNEL_REGEX = re.compile(r"https://[a-zA-Z0-9-]+\.(trycloudflare\.com|loca\.lt|ngrok-free\.app)")


def _drain_stream(stream, output_queue: queue.Queue[str]) -> None:
    """Read lines from *stream* and push them into *output_queue*."""
    try:
        for line in iter(stream.readline, ""):
            output_queue.put(line.strip())
    except Exception:
        pass
    finally:
        stream.close()


def _spawn_subprocess_with_drain(cmd: list[str]) -> tuple[subprocess.Popen, queue.Queue[str]]:
    """Spawn *cmd* and drain both stdout and stderr into a thread-safe queue.

    Prevents pipe-buffer deadlocks that occur when a subprocess fills a
    pipe (64 KB default) before the parent reads from it.
    """
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    output_queue: queue.Queue[str] = queue.Queue()

    # Start daemon threads to drain both streams concurrently
    threading.Thread(
        target=_drain_stream, args=(proc.stdout, output_queue), daemon=True
    ).start()
    threading.Thread(
        target=_drain_stream, args=(proc.stderr, output_queue), daemon=True
    ).start()

    return proc, output_queue


def _resolve_binary(name: str, hermes_subpath: str = "") -> str | None:
    """Resolve a binary path.

    Prefers ``~/.hermes/bin/<name>`` when *hermes_subpath* is provided,
    then falls back to ``$PATH``.
    """
    if hermes_subpath:
        hermes_bin = os.path.expanduser(f"~/.hermes/bin/{hermes_subpath}")
        if os.path.isfile(hermes_bin):
            return hermes_bin
    return name if subprocess.run(["which", name], capture_output=True).returncode == 0 else None


def spawn_cloudflare_tunnel(port: int) -> None:
    """Run cloudflared and capture the public URL.

    Uses the ephemeral ``trycloudflare.com`` service — **no Cloudflare
    domain or account required**.
    """
    binary = _resolve_binary("cloudflared", "cloudflared")
    if binary is None:
        logger.error(
            "cloudflared not found. Install it or choose a different tunnel provider."
        )
        return

    cmd = [binary, "tunnel", "--url", f"http://localhost:{port}"]
    logger.info("Spawning cloudflared tunnel: %s", " ".join(cmd))

    proc, output_queue = _spawn_subprocess_with_drain(cmd)
    tunnel_url: str | None = None
    deadline = time.time() + 30.0

    while time.time() < deadline:
        try:
            line = output_queue.get(timeout=1.0)
        except queue.Empty:
            if proc.poll() is not None:
                break
            continue

        logger.debug("cloudflared output: %s", line)
        match = TUNNEL_REGEX.search(line)
        if match:
            tunnel_url = match.group(0)
            break

    if tunnel_url:
        _save_tunnel_url(tunnel_url, provider="cloudflared")
    else:
        logger.warning("Could not discover tunnel URL from cloudflared output.")
        proc.terminate()


def spawn_ngrok_tunnel(port: int) -> None:
    """Run ngrok and capture the public URL.

    Requires ``ngrok`` to be installed and (for free tier) authtoken
    configured via ``ngrok config add-authtoken <token>``.
    """
    binary = _resolve_binary("ngrok")
    if binary is None:
        logger.error(
            "ngrok not found. Install it from https://ngrok.com/download "
            "or choose a different tunnel provider."
        )
        return

    cmd = [binary, "http", str(port), "--log=stdout"]
    logger.info("Spawning ngrok tunnel: %s", " ".join(cmd))

    proc, output_queue = _spawn_subprocess_with_drain(cmd)
    tunnel_url: str | None = None
    deadline = time.time() + 30.0

    # ngrok free tier URLs: https://xxxx.ngrok-free.app
    ngrok_re = re.compile(r"https://[a-zA-Z0-9-]+\.ngrok-free\.app")

    while time.time() < deadline:
        try:
            line = output_queue.get(timeout=1.0)
        except queue.Empty:
            if proc.poll() is not None:
                break
            continue

        logger.debug("ngrok output: %s", line)
        match = ngrok_re.search(line)
        if match:
            tunnel_url = match.group(0)
            break

    if tunnel_url:
        _save_tunnel_url(tunnel_url, provider="ngrok")
    else:
        logger.warning("Could not discover tunnel URL from ngrok output.")
        proc.terminate()


def spawn_localtunnel(port: int) -> None:
    """Run localtunnel (lt) and capture the public URL.

    Requires ``lt`` to be installed (``npm install -g localtunnel``).
    """
    binary = _resolve_binary("lt")
    if binary is None:
        logger.error(
            "localtunnel (lt) not found. Install it with: npm install -g localtunnel "
            "or choose a different tunnel provider."
        )
        return

    cmd = [binary, "--port", str(port)]
    logger.info("Spawning localtunnel: %s", " ".join(cmd))

    proc, output_queue = _spawn_subprocess_with_drain(cmd)
    tunnel_url: str | None = None
    deadline = time.time() + 30.0

    # localtunnel URLs: https://xxxx.loca.lt
    lt_re = re.compile(r"https://[a-zA-Z0-9-]+\.loca\.lt")

    while time.time() < deadline:
        try:
            line = output_queue.get(timeout=1.0)
        except queue.Empty:
            if proc.poll() is not None:
                break
            continue

        logger.debug("localtunnel output: %s", line)
        match = lt_re.search(line)
        if match:
            tunnel_url = match.group(0)
            break

    if tunnel_url:
        _save_tunnel_url(tunnel_url, provider="localtunnel")
    else:
        logger.warning("Could not discover tunnel URL from localtunnel output.")
        proc.terminate()


def _save_tunnel_url(url: str, provider: str) -> None:
    """Persist the discovered tunnel URL to disk."""
    hermes_home = os.path.expanduser("~/.hermes")
    os.makedirs(hermes_home, exist_ok=True)
    tunnel_file = os.path.join(hermes_home, "memora_tunnel.txt")
    with open(tunnel_file, "w") as f:
        f.write(f"{url}\n")
    logger.info(
        "%s tunnel active: %s (written to %s)",
        provider,
        url,
        tunnel_file,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Memora Daemon")
    parser.add_argument(
        "--tunnel",
        choices=["cloudflared", "ngrok", "localtunnel"],
        default=os.environ.get("MEMORA_TUNNEL", ""),
        help=(
            "Expose daemon via a public tunnel.  "
            "cloudflared = free, no account needed (default); "
            "ngrok = requires ngrok account; "
            "localtunnel = requires npm."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("MEMORA_DAEMON_PORT", "8742")),
        help="Port to bind the daemon (default: 8742 or MEMORA_DAEMON_PORT)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Entry point for the daemon."""
    import uvicorn  # local import to avoid hard-dependency at import time

    args = parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    if args.tunnel:
        tunnel_map = {
            "cloudflared": spawn_cloudflare_tunnel,
            "ngrok": spawn_ngrok_tunnel,
            "localtunnel": spawn_localtunnel,
        }
        spawner = tunnel_map.get(args.tunnel)
        if spawner:
            tunnel_thread = threading.Thread(
                target=spawner,
                args=(args.port,),
                daemon=True,
            )
            tunnel_thread.start()
        else:
            logger.error("Unknown tunnel provider: %s", args.tunnel)

    uvicorn.run(app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
