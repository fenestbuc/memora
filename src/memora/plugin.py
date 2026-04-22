"""Memora plugin — Hermes MemoryProvider implementation.

Bridges the Hermes agent to a Cloudflare Workers RAG memory backend.
Provides semantic search, fact CRUD, and a SQLite write-behind queue.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from abc import ABC
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

try:
    from agent.memory_provider import MemoryProvider
except ImportError:
    # Fallback for standalone/testing when agent package is not on path
    class MemoryProvider(ABC):
        @property
        def name(self) -> str:
            return ""
        def is_available(self) -> bool:
            return False
        def initialize(self, session_id: str, **kwargs) -> None:
            pass
        def get_tool_schemas(self) -> List[Dict[str, Any]]:
            return []
        def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
            raise NotImplementedError
        def system_prompt_block(self) -> str:
            return ""
        def prefetch(self, query: str, *, session_id: str = "") -> str:
            return ""
        def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
            pass
        def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
            pass
        def shutdown(self) -> None:
            pass
        def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
            pass
        def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
            pass
        def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
            return ""
        def on_delegation(self, task: str, result: str, *, child_session_id: str = "", **kwargs) -> None:
            pass
        def get_config_schema(self) -> List[Dict[str, Any]]:
            return []
        def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
            pass
        def on_memory_write(self, action: str, target: str, content: str) -> None:
            pass

logger = logging.getLogger(__name__)

# Configure these via environment variables or the hermes memory setup wizard.
# See docs/SETUP.md for how to deploy your own RAG worker.
_DEFAULT_URL = "https://your-rag-worker.workers.dev"
_DEFAULT_TOKEN = "YOUR_RAG_AUTH_TOKEN"

_TOOL_SCHEMAS = [
    {
        "name": "memora_search",
        "description": "Semantic search across all indexed memories. Returns ranked results by relevance.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query."},
                "top_k": {"type": "integer", "description": "Max results (default: 10)."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "memora_list",
        "description": "List facts with SQL filters. Good for browsing specific categories.",
        "parameters": {
            "type": "object",
            "properties": {
                "category": {"type": "string"},
                "search": {"type": "string"},
                "limit": {"type": "integer"},
                "offset": {"type": "integer"},
            },
            "required": [],
        },
    },
    {
        "name": "memora_add",
        "description": "Persist a new fact to long-term memory.",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The fact to remember."},
                "category": {"type": "string", "description": "Category tag (default: memory)."},
                "id": {"type": "string"},
            },
            "required": ["content"],
        },
    },
    {
        "name": "memora_update",
        "description": "Update an existing fact by ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "content": {"type": "string"},
                "category": {"type": "string"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "memora_delete",
        "description": "Delete facts by ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["ids"],
        },
    },
    {
        "name": "memora_stats",
        "description": "Get memory stats (total facts, by category).",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
]


class MemoraProvider(MemoryProvider):
    """RAG-backed memory provider for Hermes agents."""

    @property
    def name(self) -> str:
        return "memora"

    def is_available(self) -> bool:
        url = os.environ.get("RAG_WORKER_URL", _DEFAULT_URL)
        token = os.environ.get("RAG_AUTH_TOKEN", _DEFAULT_TOKEN)
        return bool(url and token and "YOUR_" not in token)

    def initialize(self, session_id: str, **kwargs) -> None:
        self._session_id = session_id
        self._hermes_home = kwargs.get("hermes_home", str(Path.home() / ".hermes"))
        self._agent_identity = kwargs.get("agent_identity", "default")
        Path(self._hermes_home).mkdir(parents=True, exist_ok=True)

        # SQLite write-behind queue — scoped by agent identity
        self._queue_path = Path(self._hermes_home) / f"memora_queue_{self._agent_identity}.db"
        self._init_queue()

        self._base_url = os.environ.get("RAG_WORKER_URL", _DEFAULT_URL).rstrip("/")
        self._token = os.environ.get("RAG_AUTH_TOKEN", _DEFAULT_TOKEN)
        self._lock = threading.Lock()
        self._seen_hashes: set[str] = set()
        self._flush_thread: threading.Thread | None = None
        self._flush_stop_event = threading.Event()

        self._health_check()

    def _health_check(self) -> None:
        """Ping RAG worker /health endpoint. Log warning if unreachable."""
        try:
            result = self._request("/health", method="GET")
            logger.debug("RAG worker health ok: %s", result)
        except Exception as e:
            logger.warning("RAG worker health check failed: %s", e)

    def _init_queue(self) -> None:
        conn = sqlite3.connect(self._queue_path)
        conn.execute(
            """CREATE TABLE IF NOT EXISTS queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                category TEXT,
                content TEXT NOT NULL,
                source_session TEXT,
                source_file TEXT,
                created_at TEXT
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS failed_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                category TEXT,
                content TEXT NOT NULL,
                source_session TEXT,
                source_file TEXT,
                created_at TEXT,
                failed_at TEXT,
                error TEXT
            )"""
        )
        conn.commit()
        conn.close()

    def system_prompt_block(self) -> str:
        return (
            "You have access to a persistent long-term memory via the memora_* tools. "
            "Use memora_search to recall past context before answering. "
            "After learning something important, use memora_add to persist it."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        try:
            result = self._request("/search", {"query": query, "top_k": 8})
            results = result.get("results", [])
            if not results:
                return ""
            lines = ["## Relevant memories from past sessions:"]
            for r in results:
                score = r.get("rerank_score", r.get("vector_score", 0))
                if score > 0.5:
                    lines.append(f"- {r.get('text', '')}")
            return "\n".join(lines) if len(lines) > 1 else ""
        except Exception as e:
            logger.debug("prefetch failed: %s", e)
            return ""

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        pass

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        self._queue_add("memory", f"User: {user_content[:500]}")
        self._queue_add("memory", f"Assistant: {assistant_content[:500]}")

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return _TOOL_SCHEMAS

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        action_map = {
            "memora_search": lambda: ("/search", {"query": args["query"], "top_k": args.get("top_k", 10)}),
            "memora_list": lambda: ("/memory/list", {k: v for k, v in args.items() if v is not None}),
            "memora_add": lambda: ("/memory/add", {
                "content": args["content"],
                "category": args.get("category", "memory"),
                **({"id": args["id"]} if "id" in args else {}),
            }),
            "memora_update": lambda: ("/memory/update", {
                "id": args["id"],
                **({"content": args["content"]} if "content" in args else {}),
                **({"category": args["category"]} if "category" in args else {}),
            }),
            "memora_delete": lambda: ("/memory/delete", {"ids": args.get("ids", [])}),
            "memora_stats": lambda: ("/memory/stats", None),
        }
        if tool_name not in action_map:
            raise NotImplementedError(f"Provider {self.name} does not handle tool {tool_name}")

        path, body = action_map[tool_name]()
        try:
            result = self._request(path, body, method="GET" if body is None else "POST")
            return json.dumps(result)
        except Exception as e:
            return json.dumps({"error": str(e)})

    def shutdown(self) -> None:
        self._stop_background_flush()
        self._flush_queue()
        self._vacuum_queue()

    def _start_background_flush(self, interval_sec: float = 60.0) -> None:
        """Start a background thread that flushes the queue periodically."""
        if self._flush_thread is not None and self._flush_thread.is_alive():
            return
        self._flush_stop_event.clear()

        def _run():
            while not self._flush_stop_event.is_set():
                self._flush_stop_event.wait(interval_sec)
                if not self._flush_stop_event.is_set():
                    try:
                        self._flush_queue()
                    except Exception as e:
                        logger.debug("Background flush failed: %s", e)

        self._flush_thread = threading.Thread(target=_run, daemon=True, name="memora-flush")
        self._flush_thread.start()

    def _stop_background_flush(self) -> None:
        """Signal the background flush thread to stop and wait for it."""
        if self._flush_thread is None:
            return
        self._flush_stop_event.set()
        self._flush_thread.join(timeout=5.0)
        if self._flush_thread.is_alive():
            logger.warning("Background flush thread did not stop within 5s")

    def _vacuum_queue(self) -> None:
        """Run VACUUM on the SQLite queue to reclaim deleted space."""
        try:
            conn = sqlite3.connect(self._queue_path)
            conn.execute("VACUUM")
            conn.close()
            logger.debug("Vacuumed queue: %s", self._queue_path)
        except Exception as e:
            logger.debug("Queue vacuum failed: %s", e)

    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        pass

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        self._extract_facts(messages)
        self._flush_queue()

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        return ""

    def on_delegation(self, task: str, result: str, *, child_session_id: str = "", **kwargs) -> None:
        self._queue_add("memory", f"Delegated task: {task[:300]}")
        self._queue_add("memory", f"Delegation result: {result[:300]}")

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return [
            {
                "key": "worker_url",
                "description": "RAG Worker URL (deploy your own via Cloudflare Workers)",
                "secret": False,
                "required": False,
                "default": _DEFAULT_URL,
            },
            {
                "key": "auth_token",
                "description": "RAG Worker auth token",
                "secret": True,
                "required": False,
                "default": _DEFAULT_TOKEN,
                "env_var": "RAG_AUTH_TOKEN",
            },
            {
                "key": "auto_ingest",
                "description": "Auto-ingest facts from every turn",
                "secret": False,
                "required": False,
                "default": True,
            },
        ]

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        config_path = Path(hermes_home) / "memora.json"
        config_path.write_text(json.dumps(values, indent=2))

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        category = "user" if target == "user" else "memory"
        self._queue_add(category, content)

    # --- Internal helpers ---

    def _queue_add(self, category: str, content: str) -> None:
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        with self._lock:
            if content_hash in self._seen_hashes:
                return
            self._seen_hashes.add(content_hash)
            conn = sqlite3.connect(self._queue_path)
            conn.execute(
                "INSERT INTO queue (action, category, content, source_session, created_at) VALUES (?, ?, ?, ?, ?)",
                ("add", category, content, self._session_id, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            conn.close()

    def _flush_queue(self) -> None:
        with self._lock:
            conn = sqlite3.connect(self._queue_path)
            cursor = conn.execute(
                "SELECT id, action, category, content, source_session, source_file, created_at FROM queue ORDER BY id"
            )
            rows = cursor.fetchall()
            if not rows:
                conn.close()
                return

            ids_to_delete = []

            try:
                facts = []
                for row in rows:
                    row_id, action, category, content, source_session, source_file, created_at = row
                    facts.append({
                        "id": f"{category}::{self._session_id or 'unknown'}::{row_id}",
                        "category": category,
                        "content": content,
                        "source_session": source_session or self._session_id,
                        "source_file": source_file,
                    })

                result = self._request("/memory/import", {"facts": facts})
                if result.get("success"):
                    ids_to_delete = [r[0] for r in rows]
                else:
                    raise Exception(f"Batch import failed: {result}")

            except Exception as batch_err:
                logger.debug("Batch import failed, falling back to individual adds: %s", batch_err)
                for row in rows:
                    row_id, action, category, content, source_session, source_file, created_at = row
                    try:
                        self._request("/memory/add", {
                            "content": content,
                            "category": category,
                            "source_session": source_session or self._session_id,
                        })
                        ids_to_delete.append(row_id)
                    except Exception as e:
                        logger.debug("Failed to flush queue item %s: %s", row_id, e)
                        conn.execute(
                            """INSERT INTO failed_queue
                               (action, category, content, source_session, source_file, created_at, failed_at, error)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                            (action, category, content, source_session, source_file, created_at,
                             datetime.now(timezone.utc).isoformat(), str(e))
                        )
                        ids_to_delete.append(row_id)

            if ids_to_delete:
                placeholders = ",".join("?" * len(ids_to_delete))
                conn.execute(f"DELETE FROM queue WHERE id IN ({placeholders})", ids_to_delete)
                conn.commit()
            conn.close()

    def _extract_facts(self, messages: List[Dict[str, Any]]) -> None:
        """Extract preference-like messages and key decisions from conversation."""
        _KEYWORDS = (
            "prefer", "want", "need", "must not", "always", "never",
            "decided", "decision", "should", "should not", "important",
            "critical", "do not", "avoid", "ensure", "make sure",
        )
        _COMMAND_VERBS = (
            "use", "send", "format", "schedule", "set", "enable", "disable",
            "include", "exclude", "follow", "apply", "implement",
        )

        for msg in messages:
            content = msg.get("content", "")
            if not content:
                continue
            lower = content.lower()

            if any(k in lower for k in _KEYWORDS):
                self._queue_add("memory", f"Key fact: {content[:800]}")
                continue

            first_sentence = re.split(r"[.!?]", content)[0].strip()
            first_word = first_sentence.split()[0].lower() if first_sentence.split() else ""
            if first_word in _COMMAND_VERBS and len(content) > 15:
                self._queue_add("memory", f"Key fact: {content[:800]}")

    def _request(self, path: str, body: dict = None, method: str = "POST",
                 max_retries: int = 3, base_delay: float = 1.0) -> dict:
        """Make HTTP request to RAG worker with exponential backoff retry."""
        url = f"{self._base_url}{path}"
        data = json.dumps(body).encode("utf-8") if body else None
        last_exc = None

        for attempt in range(max_retries + 1):
            req = urllib.request.Request(
                url,
                data=data,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                    "User-Agent": "memora-client/1.0",
                },
                method=method,
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except (urllib.error.HTTPError, urllib.error.URLError) as e:
                last_exc = e
                should_retry = False
                if isinstance(e, urllib.error.HTTPError):
                    if e.code >= 500 or e.code == 429:
                        should_retry = True
                else:
                    should_retry = True

                if not should_retry or attempt == max_retries:
                    raise

                delay = base_delay * (2 ** attempt)
                logger.debug("RAG request failed (attempt %d/%d), retrying in %.1fs: %s",
                             attempt + 1, max_retries + 1, delay, e)
                time.sleep(delay)
            except Exception:
                raise

        raise last_exc  # type: ignore[misc]


def register(ctx) -> None:
    """Plugin registration entry point for Hermes."""
    ctx.register_memory_provider(MemoraProvider())
