"""Memora background daemon — HTTP wrapper for Discord/MCP listeners.

Provides a lightweight FastAPI server that exposes the Discord webhook
parsing and RAG proxy logic so it can run continuously as a systemd
service.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import sys
import threading
from typing import Any, Callable, Dict

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from memora.discord_proxy import parse_discord_payload, proxy_query

logger = logging.getLogger(__name__)

app = FastAPI(title="Memora Daemon", version="0.2.0")

# Lazy-initialized search callable
_search_fn: Callable[[str], str] | None = None


def _get_search_fn() -> Callable[[str], str]:
    """Return a callable that runs RAG search.

    Tries to use an initialized MemoraProvider if environment variables
    are present, otherwise returns a stub.
    """
    global _search_fn
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
    """Receive a Discord webhook payload, parse it, and proxy through RAG."""
    body_bytes = await request.body()
    try:
        payload = parse_discord_payload(body_bytes)
    except Exception as exc:
        logger.warning("Failed to parse Discord payload: %s", exc)
        return JSONResponse({"error": "Invalid payload"}, status_code=400)

    search_fn = _get_search_fn()
    response_text = proxy_query(payload, search_fn)
    return JSONResponse({
        "response": response_text,
        "author": payload.get("author", "unknown"),
        "channel_id": payload.get("channel_id", ""),
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
# Tunnel support
# ---------------------------------------------------------------------------

TUNNEL_REGEX = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")


def _resolve_cloudflared_binary() -> str:
    """Return the path to the cloudflared binary.

    Prefers ~/.hermes/bin/cloudflared (where install.sh drops it) and
    falls back to whatever is on $PATH.
    """
    hermes_bin = os.path.expanduser("~/.hermes/bin/cloudflared")
    if os.path.isfile(hermes_bin):
        return hermes_bin
    return "cloudflared"


def spawn_cloudflare_tunnel(port: int) -> None:
    """Run cloudflared and capture the trycloudflare.com URL from stderr.

    Writes the discovered URL to ~/.hermes/memora_tunnel.txt and logs it.
    """
    binary = _resolve_cloudflared_binary()
    cmd = [binary, "tunnel", "--url", f"http://localhost:{port}"]
    logger.info("Spawning cloudflared tunnel: %s", " ".join(cmd))

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError:
        logger.error("cloudflared binary not found: %s", binary)
        return

    tunnel_url: str | None = None
    # cloudflared prints the public URL in its log output on stderr.
    for line in proc.stderr:  # type: ignore[union-attr]
        line = line.strip()
        logger.debug("cloudflared output: %s", line)
        match = TUNNEL_REGEX.search(line)
        if match:
            tunnel_url = match.group(0)
            break

    if tunnel_url:
        hermes_home = os.path.expanduser("~/.hermes")
        os.makedirs(hermes_home, exist_ok=True)
        tunnel_file = os.path.join(hermes_home, "memora_tunnel.txt")
        with open(tunnel_file, "w") as f:
            f.write(tunnel_url)
        logger.info(
            "Cloudflare tunnel active: %s (written to %s)",
            tunnel_url,
            tunnel_file,
        )
    else:
        logger.warning(
            "Could not discover tunnel URL from cloudflared output."
        )
        proc.terminate()


def _should_enable_tunnel(args: argparse.Namespace) -> bool:
    """Return True if tunnel mode should be enabled."""
    return args.tunnel or os.environ.get("MEMORA_TUNNEL", "") == "1"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Memora Daemon")
    parser.add_argument(
        "--tunnel",
        action="store_true",
        help="Expose daemon via a Cloudflare tunnel (also set MEMORA_TUNNEL=1)",
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

    if _should_enable_tunnel(args):
        tunnel_thread = threading.Thread(
            target=spawn_cloudflare_tunnel,
            args=(args.port,),
            daemon=True,
        )
        tunnel_thread.start()

    uvicorn.run(app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
