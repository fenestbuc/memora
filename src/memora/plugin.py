"""Hermes RAG Memory Provider Plugin.

Bridges Hermes agent to the Cloudflare Workers RAG memory backend.
Provides semantic search, fact CRUD, and SQLite write-behind queue.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import subprocess
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

_DEFAULT_URL = "https://your-rag-worker.workers.dev"
_DEFAULT_TOKEN = "your_auth_token_here"

_TOOL_SCHEMAS = [
    {
        "name": "rag_memory_search",
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
        "name": "rag_memory_list",
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
        "name": "rag_memory_add",
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
        "name": "rag_memory_update",
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
        "name": "rag_memory_delete",
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
        "name": "rag_memory_stats",
        "description": "Get memory stats (total facts, by category).",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
]


class HermesRagMemoryProvider(MemoryProvider):
    """RAG-backed memory provider for Hermes."""

    @property
    def name(self) -> str:
        return "hermes-rag-memory"

    def is_available(self) -> bool:
        url = os.environ.get("RAG_WORKER_URL", _DEFAULT_URL)
        token = os.environ.get("RAG_AUTH_TOKEN", _DEFAULT_TOKEN)
        if not url or not token:
            return False
        # Reject placeholder / partial / redacted tokens
        if "YOUR_" in token or "..." in token:
            return False
        return True

    def initialize(self, session_id: str, **kwargs) -> None:
        self._session_id = session_id
        self._hermes_home = kwargs.get("hermes_home", str(Path.home() / ".hermes"))
        self._agent_identity = kwargs.get("agent_identity", "default")
        self._config = kwargs.get("config", {})
        Path(self._hermes_home).mkdir(parents=True, exist_ok=True)

        # Local memory directory (mirrors RAG writes to markdown files)
        self._memory_dir = Path(self._config.get("memory_dir", str(Path.home() / "hermes-workspace" / "memory")))
        self._memory_dir.mkdir(parents=True, exist_ok=True)

        # Auto-ingest flag gates sync_turn
        self._auto_ingest = self._config.get("auto_ingest", True)

        # Auto-commit flag gates git commits after session end
        self._auto_commit = self._config.get("auto_commit", True)
        self._modified_files: set[Path] = set()

        # Prefetch relevance threshold (P2: configurable)
        self._prefetch_threshold = self._config.get("prefetch_threshold", 0.5)

        # SQLite write-behind queue — scoped by agent identity
        self._queue_path = Path(self._hermes_home) / f"rag_memory_queue_{self._agent_identity}.db"
        self._init_queue()

        self._base_url = os.environ.get("RAG_WORKER_URL", _DEFAULT_URL).rstrip("/")
        self._token = os.environ.get("RAG_AUTH_TOKEN", _DEFAULT_TOKEN)
        self._lock = threading.Lock()
        self._seen_hashes: set[str] = set()
        self._flush_thread: threading.Thread | None = None
        self._flush_stop_event = threading.Event()
        # Circuit breaker state
        self._consecutive_failures = 0
        self._circuit_open = False
        self._circuit_open_until = 0.0

        # Metrics counters (P2: observability)
        self._metrics = {
            "facts_queued": 0,
            "facts_flushed": 0,
            "facts_failed": 0,
            "prefetch_calls": 0,
            "search_calls": 0,
            "circuit_opens": 0,
        }

        self._init_queue()
        self._load_seen_hashes()

        self._base_url = os.environ.get("RAG_WORKER_URL", _DEFAULT_URL).rstrip("/")
        self._token = os.environ.get("RAG_AUTH_TOKEN", _DEFAULT_TOKEN)

        # Verify RAG worker is reachable
        self._health_check()

        # Start background flush thread for crash-safe periodic writes
        self._start_background_flush(interval_sec=60.0)

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
        conn.execute(
            """CREATE TABLE IF NOT EXISTS seen_hashes (
                hash TEXT PRIMARY KEY,
                created_at TEXT
            )"""
        )
        conn.commit()
        conn.close()

    def _load_seen_hashes(self) -> None:
        """Load previously queued content hashes from SQLite for cross-session dedup."""
        conn = sqlite3.connect(self._queue_path)
        cursor = conn.execute("SELECT hash FROM seen_hashes")
        for row in cursor:
            self._seen_hashes.add(row[0])
        conn.close()

    def system_prompt_block(self) -> str:
        return (
            "You have access to a persistent long-term memory via the rag_memory_* tools. "
            "Use rag_memory_search to recall past context before answering. "
            "After learning something important, use rag_memory_add to persist it."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        self._metrics["prefetch_calls"] += 1
        try:
            result = self._request("/search", {"query": query, "top_k": 8})
            results = result.get("results", [])
            if not results:
                return ""
            lines = ["## Relevant memories from past sessions:"]
            for r in results:
                score = r.get("rerank_score", r.get("vector_score", 0))
                if score > self._prefetch_threshold:
                    cat = r.get("metadata", {}).get("category", "memory")
                    created = r.get("metadata", {}).get("created_at", "")
                    date_tag = f" [{created[:10]}]" if created else ""
                    lines.append(f"- [{cat}]{date_tag} {r.get('text', '')}")
            return "\n".join(lines) if len(lines) > 1 else ""
        except Exception as e:
            logger.debug("prefetch failed: %s", e)
            return ""

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        pass

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        if not self._auto_ingest:
            return
        # Lightweight: queue both messages for later batch processing
        self._queue_add("memory", f"User: {user_content[:500]}")
        self._queue_add("memory", f"Assistant: {assistant_content[:500]}")

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return _TOOL_SCHEMAS

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        action_map = {
            "rag_memory_search": lambda: ("/search", {"query": args["query"], "top_k": args.get("top_k", 10)}),
            "rag_memory_list": lambda: ("/memory/list", {k: v for k, v in args.items() if v is not None}),
            "rag_memory_add": lambda: ("/memory/add", {
                "content": args["content"],
                "category": args.get("category", "memory"),
                **({"id": args["id"]} if "id" in args else {}),
            }),
            "rag_memory_update": lambda: ("/memory/update", {
                "id": args["id"],
                **({"content": args["content"]} if "content" in args else {}),
                **({"category": args["category"]} if "category" in args else {}),
            }),
            "rag_memory_delete": lambda: ("/memory/delete", {"ids": args.get("ids", [])}),
            "rag_memory_stats": lambda: ("/memory/stats", None),
        }
        if tool_name not in action_map:
            raise NotImplementedError(f"Provider {self.name} does not handle tool {tool_name}")

        path, body = action_map[tool_name]()
        try:
            result = self._request(path, body, method="GET" if body is None else "POST")
            if tool_name == "rag_memory_search":
                self._metrics["search_calls"] += 1
            return json.dumps(result)
        except Exception as e:
            return json.dumps({"error": str(e)})

    def shutdown(self) -> None:
        self._stop_background_flush()
        self._flush_queue()
        self._vacuum_queue()
        logger.info("HermesRagMemoryProvider shutdown. Metrics: %s", self._metrics)

    def get_metrics(self) -> Dict[str, int]:
        """Return current metrics counters."""
        return dict(self._metrics)

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

        self._flush_thread = threading.Thread(target=_run, daemon=True, name="rag-flush")
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
        # Extract key facts from conversation and flush queue
        self._extract_facts(messages)
        self._flush_queue()
        # Auto-commit any modified markdown memory files
        if self._auto_commit:
            self._commit_memory_files()

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        return ""

    def on_delegation(self, task: str, result: str, *, child_session_id: str = "", **kwargs) -> None:
        self._queue_add("memory", f"Delegated task: {task[:300]}")
        self._queue_add("memory", f"Delegation result: {result[:300]}")

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return [
            {
                "key": "worker_url",
                "description": "RAG Worker URL (default uses Cloudflare Workers)",
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
            {
                "key": "auto_commit",
                "description": "Auto-commit memory markdown files to git after each session",
                "secret": False,
                "required": False,
                "default": True,
            },
            {
                "key": "memory_dir",
                "description": "Local markdown memory directory (mirrors RAG writes)",
                "secret": False,
                "required": False,
                "default": str(Path.home() / "hermes-workspace" / "memory"),
            },
            {
                "key": "prefetch_threshold",
                "description": "Minimum relevance score (0-1) for prefetched memories",
                "secret": False,
                "required": False,
                "default": 0.5,
            },
        ]

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        config_path = Path(hermes_home) / "hermes-rag-memory.json"
        config_path.write_text(json.dumps(values, indent=2))

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        # Mirror built-in memory writes to RAG
        category = "user" if target == "user" else "memory"
        self._queue_add(category, content)

    # --- Internal helpers ---

    def _queue_add(self, category: str, content: str) -> None:
        # Content validation: skip empty, short, or whitespace-only content
        stripped = content.strip()
        if len(stripped) < 10:
            return
        # Skip purely whitespace/punctuation
        if not any(c.isalnum() for c in stripped):
            return
        # Deduplication: skip if exact content was already queued this session or in DB
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        with self._lock:
            if content_hash in self._seen_hashes:
                return
            self._seen_hashes.add(content_hash)
            conn = sqlite3.connect(self._queue_path)
            # Persist hash for cross-session dedup
            conn.execute(
                "INSERT OR IGNORE INTO seen_hashes (hash, created_at) VALUES (?, ?)",
                (content_hash, datetime.now(timezone.utc).isoformat()),
            )
            conn.execute(
                "INSERT INTO queue (action, category, content, source_session, created_at) VALUES (?, ?, ?, ?, ?)",
                ("add", category, content, self._session_id, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            conn.close()
            self._metrics["facts_queued"] += 1
            # Mirror to local markdown memory
            self._write_local_memory(category, content)

    def _write_local_memory(self, category: str, content: str) -> None:
        """Append a fact to the local markdown memory file (e.g. memory/business.md)."""
        try:
            self._memory_dir.mkdir(parents=True, exist_ok=True)
            safe_category = re.sub(r"[^a-zA-Z0-9_-]", "_", category).lower()
            file_path = self._memory_dir / f"{safe_category}.md"

            # Ensure file has a header if new
            if not file_path.exists():
                file_path.write_text(f"# {category.title()} Context\n\n")

            # Append under current session heading
            session_heading = f"## From session {self._session_id or 'unknown'}\n\n"
            bullet = f"- {content.strip().replace(chr(10), ' ')}\n"

            with open(file_path, "a", encoding="utf-8") as f:
                # Only add session heading if file doesn't already end with it
                current_text = file_path.read_text(encoding="utf-8")
                if session_heading.strip() not in current_text[-2000:]:
                    f.write("\n" + session_heading)
                f.write(bullet + "\n")
            # Track for auto-commit
            self._modified_files.add(file_path)
        except Exception as e:
            logger.debug("Local memory write failed: %s", e)

    def _commit_memory_files(self) -> None:
        """Git-commit modified markdown memory files (best-effort)."""
        if not self._modified_files:
            return
        try:
            # Find git root from memory_dir
            git_root = self._memory_dir
            while git_root != git_root.parent:
                if (git_root / ".git").exists():
                    break
                git_root = git_root.parent
            if not (git_root / ".git").exists():
                logger.debug("No git repo found for auto-commit")
                return

            files = [str(f) for f in self._modified_files]
            subprocess.run(
                ["git", "add"] + files,
                cwd=str(git_root),
                capture_output=True,
                check=False,
            )
            result = subprocess.run(
                ["git", "diff", "--cached", "--quiet"],
                cwd=str(git_root),
                capture_output=True,
                check=False,
            )
            if result.returncode == 0:
                # Nothing to commit
                self._modified_files.clear()
                return
            subprocess.run(
                ["git", "commit", "-m", f"memory: auto-ingest from session {self._session_id or 'unknown'}"],
                cwd=str(git_root),
                capture_output=True,
                check=False,
            )
            self._modified_files.clear()
        except Exception as e:
            logger.debug("Auto-commit failed: %s", e)

    def _flush_queue(self, chunk_size: int = 100) -> None:
        with self._lock:
            conn = sqlite3.connect(self._queue_path)
            cursor = conn.execute(
                "SELECT id, action, category, content, source_session, source_file, created_at FROM queue ORDER BY id"
            )
            all_rows = cursor.fetchall()
            if not all_rows:
                conn.close()
                return

            ids_to_delete = []

            # Process in chunks to avoid payload size issues
            for i in range(0, len(all_rows), chunk_size):
                chunk = all_rows[i:i + chunk_size]
                facts = []
                for row in chunk:
                    row_id, action, category, content, source_session, source_file, created_at = row
                    facts.append({
                        "id": f"{category}::{self._session_id or 'unknown'}::{row_id}",
                        "category": category,
                        "content": content,
                        "source_session": source_session or self._session_id,
                        "source_file": source_file,
                    })

                try:
                    result = self._request("/memory/import", {"facts": facts})
                    if result.get("success"):
                        ids_to_delete.extend([r[0] for r in chunk])
                    else:
                        raise Exception(f"Batch import failed: {result}")
                except Exception as batch_err:
                    logger.debug("Batch import failed for chunk, falling back to individual adds: %s", batch_err)
                    # Fallback: individual calls with dead-letter for failures
                    for row in chunk:
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
                            self._metrics["facts_failed"] += 1
                            conn.execute(
                                """INSERT INTO failed_queue
                                   (action, category, content, source_session, source_file, created_at, failed_at, error)
                                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                                (action, category, content, source_session, source_file, created_at,
                                 datetime.now(timezone.utc).isoformat(), str(e))
                            )
                            # Do NOT delete from queue on failure — keep for retry
                            # But we must avoid infinite re-flush, so delete anyway
                            # and rely on failed_queue for retry logic
                            # For now: move to failed_queue and delete from queue
                            ids_to_delete.append(row_id)

            if ids_to_delete:
                placeholders = ",".join("?" * len(ids_to_delete))
                conn.execute(f"DELETE FROM queue WHERE id IN ({placeholders})", ids_to_delete)
                conn.commit()
            conn.close()
            self._metrics["facts_flushed"] += len(ids_to_delete)

    def _extract_facts(self, messages: List[Dict[str, Any]]) -> None:
        """Extract preference-like messages and key decisions from conversation.

        Uses expanded keyword heuristics, imperative commands, and structural patterns.
        Filters out short, URL-only, and code-block messages to reduce noise.
        """
        # Expanded indicators beyond simple keywords
        _KEYWORDS = (
            "prefer", "want", "need", "must not", "always", "never",
            "decided", "decision", "should", "should not", "important",
            "critical", "do not", "avoid", "ensure", "make sure",
        )
        # Imperative command patterns (first word is a directive verb)
        _COMMAND_VERBS = (
            "use", "send", "format", "schedule", "set", "enable", "disable",
            "include", "exclude", "follow", "apply", "implement",
        )
        # Patterns to skip
        _URL_RE = re.compile(r"^https?://\S+$")
        _CODE_BLOCK_RE = re.compile(r"^```")

        for msg in messages:
            content = msg.get("content", "")
            if not content:
                continue
            stripped = content.strip()
            # Skip very short, URL-only, or code-block messages
            if len(stripped) < 30:
                continue
            if _URL_RE.match(stripped):
                continue
            if _CODE_BLOCK_RE.search(stripped):
                continue

            lower = stripped.lower()

            # Keyword-based extraction
            if any(k in lower for k in _KEYWORDS):
                self._queue_add("memory", f"Key fact: {stripped[:800]}")
                continue

            # Imperative command extraction: verb at start of sentence
            first_sentence = re.split(r"[.!?]", stripped)[0].strip()
            first_word = first_sentence.split()[0].lower() if first_sentence.split() else ""
            if first_word in _COMMAND_VERBS and len(stripped) > 15:
                self._queue_add("memory", f"Key fact: {stripped[:800]}")

    def _request(self, path: str, body: dict = None, method: str = "POST",
                 max_retries: int = 3, base_delay: float = 1.0) -> dict:
        """Make HTTP request to RAG worker with exponential backoff retry and circuit breaker."""
        # Circuit breaker check
        if self._circuit_open:
            if time.time() < self._circuit_open_until:
                raise Exception("RAG worker circuit breaker is open")
            else:
                self._circuit_open = False
                self._consecutive_failures = 0

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
                    "User-Agent": "hermes-rag-client/1.0",
                },
                method=method,
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                    # Reset circuit breaker on success
                    self._consecutive_failures = 0
                    return result
            except (urllib.error.HTTPError, urllib.error.URLError) as e:
                last_exc = e
                # Retry on 5xx server errors, connection issues, and rate limits (429)
                should_retry = False
                if isinstance(e, urllib.error.HTTPError):
                    if e.code >= 500 or e.code == 429:
                        should_retry = True
                else:
                    should_retry = True

                if not should_retry or attempt == max_retries:
                    self._consecutive_failures += 1
                    if self._consecutive_failures >= 3:
                        self._circuit_open = True
                        self._circuit_open_until = time.time() + 60.0
                        self._metrics["circuit_opens"] += 1
                        logger.warning("RAG worker circuit breaker opened after %d failures", self._consecutive_failures)
                    raise

                delay = base_delay * (2 ** attempt)
                logger.debug("RAG request failed (attempt %d/%d), retrying in %.1fs: %s",
                             attempt + 1, max_retries + 1, delay, e)
                time.sleep(delay)
            except Exception:
                # Non-retryable (e.g., JSON parse after successful HTTP)
                self._consecutive_failures += 1
                if self._consecutive_failures >= 3:
                    self._circuit_open = True
                    self._circuit_open_until = time.time() + 60.0
                    self._metrics["circuit_opens"] += 1
                raise

        # Should never reach here, but satisfy type checker
        raise last_exc  # type: ignore[misc]


def register(ctx) -> None:
    """Plugin registration entry point."""
    ctx.register_memory_provider(HermesRagMemoryProvider())
