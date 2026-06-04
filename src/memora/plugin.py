"""Hermes RAG Memory Provider Plugin.

Bridges Hermes agent to the Cloudflare Workers RAG memory backend.
Provides semantic search, fact CRUD, and SQLite write-behind queue.
"""

from __future__ import annotations

import hashlib
import json
import logging
from .cache import SqliteL1Cache
from . import swarm_manager, triage, evaluations as _evaluations
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

_DEFAULT_URL = os.environ.get("RAG_WORKER_URL", "")
_DEFAULT_TOKEN = os.environ.get("RAG_AUTH_TOKEN", "")

_TOOL_SCHEMAS = [
    {
        "name": "memora_search",
        "description": "Semantic search across all indexed memories. Returns ranked results by relevance.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query."},
                "top_k": {"type": "integer", "description": "Max results (default: 10)."},
                "use_reranking": {"type": "boolean", "description": "Use BGE cross-encoder for hybrid search (default: true)."},
                "parent_id": {"type": "string", "description": "Filter by Graph Metadata parent_id."},
                "scope": {"type": "string", "description": "Search scope: 'personal' (default) or 'global'."}
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
                "category": {"type": "string", "description": "Category tag (e.g., projects, strategy, business, integrations, user). MUST be specific, avoid the default 'memory' bucket."},
                "parent_id": {"type": "string", "description": "Optional Graph Metadata parent_id for bidirectional linking."},
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
    """RAG-backed memory provider for Hermes."""

    def __init__(self):
        self._base_url = ""
        self._token = ""
        self._circuit_open = False
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0
        self._metrics = {
            "facts_queued": 0,
            "facts_flushed": 0,
            "facts_failed": 0,
            "search_calls": 0,
            "prefetch_calls": 0,
            "circuit_opens": 0,
        }
        self._lock = threading.Lock()
        self._seen_hashes: set[str] = set()
        self._flush_thread: threading.Thread | None = None
        self._flush_stop_event = threading.Event()
        self._l1_cache = SqliteL1Cache()
        self._memory_dir: Path | None = None
        self._auto_swarm = False
        self._owner_id = "anonymous"

    @property
    def worker_url(self) -> str:
        return self._base_url

    @worker_url.setter
    def worker_url(self, value: str) -> None:
        self._base_url = value

    @property
    def worker_token(self) -> str:
        return self._token

    @worker_token.setter
    def worker_token(self, value: str) -> None:
        self._token = value

    @property
    def name(self) -> str:
        return "memora"

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
        self._l1_cache = SqliteL1Cache()
        self._session_id = session_id
        self._hermes_home = kwargs.get("hermes_home", str(Path.home() / ".hermes"))
        self._agent_identity = kwargs.get("agent_identity", "default")
        self._config = kwargs.get("config", {})
        Path(self._hermes_home).mkdir(parents=True, exist_ok=True)

        # Onboarding: warn if profile is missing (non-blocking)
        self._check_onboarding()

        # Read owner_id from profile
        profile_path = Path(self._hermes_home) / "memora.json"
        try:
            profile = json.loads(profile_path.read_text())
            self._owner_id = profile.get("first_name", "anonymous")
        except Exception:
            self._owner_id = "anonymous"

        # Local memory directory (mirrors RAG writes to markdown files)
        self._memory_dir = Path(self._config.get("memory_dir", str(Path.home() / "hermes-workspace" / "memory")))
        self._memory_dir.mkdir(parents=True, exist_ok=True)

        # Auto-ingest flag gates sync_turn
        self._auto_ingest = self._config.get("auto_ingest", True)

        # Auto-swarm flag gates kanban delegate task spawning on new facts
        self._auto_swarm = self._config.get("auto_swarm", False)

        # Prefetch relevance threshold
        self._prefetch_threshold = self._config.get("prefetch_threshold", 0.5)

        # SQLite write-behind queue — scoped by agent identity
        self._queue_path = Path(self._hermes_home) / f"memora_queue_{self._agent_identity}.db"

        # Persistent SQLite connection (one per provider instance)
        self._queue_conn = sqlite3.connect(self._queue_path, check_same_thread=False)
        self._queue_conn.execute("PRAGMA journal_mode=WAL;")
        self._queue_conn.execute("PRAGMA synchronous=NORMAL;")
        self._queue_conn.execute("PRAGMA busy_timeout=5000;")
        self._init_queue()
        self._load_seen_hashes()

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

        # Metrics counters
        self._metrics = {
            "facts_queued": 0,
            "facts_flushed": 0,
            "facts_failed": 0,
            "prefetch_calls": 0,
            "search_calls": 0,
            "circuit_opens": 0,
        }

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

    def _check_onboarding(self) -> None:
        """Warn if onboarding has not been completed.

        Does **not** block with interactive ``input()`` — that would hang
        daemons, CI, and automation.  Users must run ``./install.sh`` or
        ``python -m memora.onboarding`` explicitly beforehand.
        """
        from . import onboarding as _onboarding

        if _onboarding.load_profile(hermes_home=self._hermes_home) is None:
            logger.warning(
                "Memora profile missing at %s. Run './install.sh' or "
                "'python -m memora.onboarding' to set up your Digital Twin.",
                Path(self._hermes_home) / "memora.json",
            )

    def _init_queue(self) -> None:
        # Use persistent connection if available, otherwise create one temporarily
        conn = getattr(self, "_queue_conn", None)
        if conn is None:
            conn = sqlite3.connect(self._queue_path)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
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
        conn.execute(
            """CREATE TABLE IF NOT EXISTS facts (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                category TEXT,
                superseded_by TEXT,
                scope TEXT DEFAULT 'personal',
                created_at TEXT
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS pending_actions (
                id TEXT PRIMARY KEY,
                action_type TEXT,
                payload JSON,
                created_at TEXT,
                status TEXT DEFAULT 'pending'
            )"""
        )
        conn.commit()
        if getattr(self, "_queue_conn", None) is None:
            conn.close()

    def _load_seen_hashes(self) -> None:
        """Load previously queued content hashes from SQLite for cross-session dedup."""
        conn = getattr(self, "_queue_conn", None)
        if conn is None:
            conn = sqlite3.connect(self._queue_path)
        cursor = conn.execute("SELECT hash FROM seen_hashes")
        for row in cursor:
            self._seen_hashes.add(row[0])
        if getattr(self, "_queue_conn", None) is None:
            conn.close()

    def system_prompt_block(self) -> str:
        return (
            "You have access to a persistent long-term memory via the memora_* tools. "
            "Use memora_search to recall past context before answering. "
            "After learning something important, DO NOT use the default memory tool — ALWAYS use memora_add to persist it directly to the RAG backend. "
            "When using memora_add, you MUST explicitly categorize the fact using precise tags (e.g., projects, strategy, business, integrations, user) rather than dumping it into the default 'memory' bucket."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        self._metrics["prefetch_calls"] += 1
        try:
            result = self._request("/search", {"query": query, "top_k": 5})
            results = result.get("results", [])
            if not results:
                return ""
            lines = ["## Relevant memories from past sessions:"]
            seen_hashes: set[str] = set()
            total_chars = len(lines[0])
            max_chars = 2000
            for r in results:
                score = r.get("rerank_score", r.get("vector_score", 0))
                if score < 0.6:
                    continue
                text = r.get("text", "")
                # Deduplicate by content hash
                text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                if text_hash in seen_hashes:
                    continue
                seen_hashes.add(text_hash)
                cat = r.get("metadata", {}).get("category", "memory")
                created = r.get("metadata", {}).get("created_at", "")
                date_tag = f" [{created[:10]}]" if created else ""
                line = f"- [{cat}]{date_tag} {text}"
                if total_chars + len(line) + 1 > max_chars:
                    break
                lines.append(line)
                total_chars += len(line) + 1
            return "\n".join(lines) if len(lines) > 1 else ""
        except Exception as e:
            logger.debug("prefetch failed: %s", e)
            return ""

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        pass

    # Trivial messages that should not be persisted as memory
    _TRIVIAL_RE = re.compile(
        r"^(\s*(execute|go ahead|ok|yes|no|sure|done|retry|continue|stop|halt"
        r"|test|validate|check|review|analyse|analyze|proceed|next"
        r"|let's execute|let's go|please do|do it|run it"
        r"|hello|hi|hey|thanks|thank you|ty)\s*[.!?]*\s*)$",
        re.IGNORECASE,
    )

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        if not self._auto_ingest:
            return
        # Skip trivial user messages
        user_stripped = user_content.strip()
        if len(user_stripped) < 15 or self._TRIVIAL_RE.match(user_stripped):
            pass  # Do not queue trivial user messages
        else:
            self._queue_add("memory", f"User: {user_content[:500]}")
        # Queue assistant response (always, unless it's a tool error or very short)
        if len(assistant_content.strip()) >= 15:
            self._queue_add("memory", f"Assistant: {assistant_content[:500]}")

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return _TOOL_SCHEMAS

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        # Intercept memora_add for chunking and offline-fallback
        if tool_name == "memora_add":
            content_str = args["content"]
            category = args.get("category", "memory")
            parent_id = args.get("parent_id")
            
            # Chunking logic for large texts (e.g. > 4000 chars)
            max_chunk = 4000
            if len(content_str) > max_chunk:
                chunks = [content_str[i:i+max_chunk] for i in range(0, len(content_str), max_chunk)]
                results = []
                for i, chunk in enumerate(chunks):
                    # For chunks after the first, we use the provided parent_id, or we could link them.
                    # Simple approach: just add them all with the same parent_id and category.
                    try:
                        res = self._request("/memory/add", {
                            "content": f"[Part {i+1}/{len(chunks)}] {chunk}",
                            "category": category,
                            "parent_id": parent_id,
                            "owner_id": self._owner_id,
                            "tenant_id": "kubar",
                        })
                        results.append(res)
                    except Exception as e:
                        # Fallback to queue if network fails
                        self._queue_add(category, f"[Part {i+1}/{len(chunks)}] {chunk}")
                        results.append({"status": "queued_offline", "error": str(e)})
                self._maybe_trigger_swarm(content_str, category)
                self._l1_cache.clear()
                return json.dumps({"status": "success", "chunks_processed": len(chunks), "results": results})

            # Normal size handling with offline fallback
            try:
                res = self._request("/memory/add", {
                    "content": content_str,
                    "category": category,
                    "parent_id": parent_id,
                    "owner_id": self._owner_id,
                    "tenant_id": "kubar",
                    **({"id": args["id"]} if "id" in args else {})
                })
                self._maybe_trigger_swarm(content_str, category)
                self._l1_cache.clear()
                return json.dumps(res)
            except Exception as e:
                self._queue_add(category, content_str)
                self._maybe_trigger_swarm(content_str, category)
                self._l1_cache.clear()
                return json.dumps({"status": "queued_offline", "error": str(e), "message": "Network unavailable. Fact queued for background sync."})

        # Intercept kanban_reassign for routing-feedback learning
        if tool_name == "kanban_reassign":
            from .feedback_interceptor import capture_routing_correction

            feedback_jsonl = Path(self._hermes_home) / "routing_corrections.jsonl"
            correction = capture_routing_correction(args, jsonl_path=str(feedback_jsonl))
            self._queue_add("feedback", json.dumps(correction))
            return json.dumps({"status": "feedback_captured", "correction": correction})

        action_map = {
            "memora_search": lambda: ("/search", {
                "query": args["query"], 
                "top_k": args.get("top_k", 10),
                "owner_id": self._owner_id,
                "tenant_id": "kubar",
                **({"use_reranking": args["use_reranking"]} if "use_reranking" in args else {}),
                **({"parent_id": args["parent_id"]} if "parent_id" in args else {}),
                **({"metadata_filter": {"owner_id": self._owner_id}} if args.get("scope", "personal") == "personal" else {}),
            }),
            "memora_list": lambda: ("/memory/list", {k: v for k, v in args.items() if v is not None}),
            "memora_add": lambda: ("/memory/add", {
                "content": args["content"],
                "category": args.get("category", "memory"),
                "owner_id": self._owner_id,
                "tenant_id": "kubar",
                **({"parent_id": args["parent_id"]} if "parent_id" in args else {}),
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
        
        # Local L1 Cache logic
        cache_key = None
        if tool_name == "memora_search":
            hasher = hashlib.sha256()
            hasher.update(json.dumps(body, sort_keys=True).encode("utf-8"))
            cache_key = f"search:{hasher.hexdigest()}"
            cached_result = self._l1_cache.get(cache_key)
            if cached_result is not None:
                self._metrics["search_calls"] += 1
                return json.dumps(cached_result)
        elif tool_name in ("memora_add", "memora_update", "memora_delete"):
            self._l1_cache.clear()

        try:
            result = self._request(path, body, method="GET" if body is None else "POST")
            if tool_name == "memora_search":
                self._metrics["search_calls"] += 1
                if cache_key and "error" not in result:
                    self._l1_cache.set(cache_key, result, ttl_seconds=300) # 5 min TTL
            return json.dumps(result)
        except Exception as e:
            return json.dumps({"error": str(e)})

    def shutdown(self) -> None:
        self._stop_background_flush()
        self._flush_queue()
        self._vacuum_queue()
        try:
            self._queue_conn.close()
        except Exception as e:
            logger.debug("Queue connection close failed: %s", e)
        logger.info("MemoraProvider shutdown. Metrics: %s", self._metrics)

    def get_metrics(self) -> Dict[str, int]:
        """Return current metrics counters."""
        return dict(self._metrics)

    def _maybe_trigger_swarm(self, content: str, category: str) -> None:
        """Gate swarm dispatch behind the LLM triage check."""
        if not self._auto_swarm:
            return
        if triage.should_trigger_swarm(content):
            swarm_manager.trigger(source="rag", content=content, category=category)

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
            # VACUUM cannot run inside a transaction, so we need a fresh connection
            self._queue_conn.commit()
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
            {
                "key": "auto_swarm",
                "description": "Auto-spawn kanban delegate tasks when new facts are ingested",
                "secret": False,
                "required": False,
                "default": False,
            },
        ]

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        config_path = Path(hermes_home) / "memora.json"
        config_path.write_text(json.dumps(values, indent=2))

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        # Mirror built-in memory writes to RAG
        category = "user" if target == "user" else "memory"
        self._queue_add(category, content)

    def add_eval_golden(self, content: str, fact_ids: List[str]) -> Dict[str, Any]:
        """Add an eval golden dataset entry.

        Args:
            content: The eval query content.
            fact_ids: List of canonical fact IDs that answer the query.

        Returns:
            The parsed JSON response from the RAG worker.
        """
        payload = {
            "content": content,
            "category": "eval_golden",
            "source_session": json.dumps(fact_ids),
        }
        return self._request("/facts", payload)

    def evaluate(self) -> Dict[str, Any]:
        """Run evaluation against the golden dataset.

        Returns:
            The parsed JSON response containing metrics such as mrr and hit_rate.
        """
        return self._request("/evaluate", {}, method="POST")

    def evaluate_ceo_digest(
        self,
        digest_text: str,
        open_prs: List[dict],
    ) -> Dict[str, Any]:
        """Evaluate CEO Digest quality via LLM-as-a-Judge or heuristic fallback.

        Args:
            digest_text: The generated CEO digest string.
            open_prs: Ground-truth list of open PR dicts from GitHub.

        Returns:
            Score dict with completeness, accuracy, conciseness, actionability,
            and overall keys.
        """
        evaluator = _evaluations.CeoDigestEvaluator()
        score = evaluator.evaluate(digest_text, open_prs)
        return score.to_dict()

    def evaluate_swarm_triggers(
        self,
        trigger_fn: Callable[..., dict] | None = None,
    ) -> Dict[str, Any]:
        """Evaluate kanban swarm trigger accuracy against a ground-truth dataset.

        Args:
            trigger_fn: Optional callable matching ``swarm_manager.trigger``.
                If omitted, the evaluator inspects ``swarm_manager`` directly.

        Returns:
            Dict with ``accuracy`` (float), ``total_cases`` (int),
            ``correct_cases`` (int), and ``case_details`` (list).
        """
        evaluator = _evaluations.SwarmTriggerEvaluator()
        scores = evaluator.evaluate(trigger_fn)
        correct = sum(1 for s in scores if s.correct)
        return {
            "accuracy": correct / len(scores) if scores else 0.0,
            "total_cases": len(scores),
            "correct_cases": correct,
            "case_details": [s.to_dict() for s in scores],
        }

    def evaluate_rag_comprehensive(
        self,
        golden_dataset: List[Dict[str, Any]],
        k: int = 10,
    ) -> Dict[str, Any]:
        """Run comprehensive RAG retrieval metrics against a golden dataset.

        Args:
            golden_dataset: List of {"query": str, "relevant_ids": List[str]}.
            k: Cut-off rank for metrics (default 10).

        Returns:
            Dict of RAGMetrics fields.
        """
        evaluator = _evaluations.RAGEvaluator(
            base_url=self._base_url,
            token=self._token,
            k=k,
        )
        metrics = evaluator.evaluate(golden_dataset)
        return metrics.to_dict()

    def run_eval_suite(
        self,
        golden_dataset: List[Dict[str, Any]] | None = None,
        ceo_digest_text: str = "",
        open_prs: List[dict] | None = None,
        trigger_fn: Callable[..., dict] | None = None,
    ) -> Dict[str, Any]:
        """Execute the full Memora evaluation suite and return a report.

        Args:
            golden_dataset: RAG golden dataset.
            ceo_digest_text: Optional CEO digest to judge.
            open_prs: Ground-truth PRs for digest eval.
            trigger_fn: Optional swarm trigger callable.

        Returns:
            Full evaluation report dict.
        """
        report = _evaluations.run_full_evaluation(
            provider=self,
            golden_dataset=golden_dataset,
            ceo_digest_text=ceo_digest_text,
            open_prs=open_prs,
            trigger_fn=trigger_fn,
        )
        return report.to_dict()

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
            conn = getattr(self, "_queue_conn", None)
            close_after = False
            if conn is None:
                conn = sqlite3.connect(self._queue_path)
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=NORMAL;")
                conn.execute("PRAGMA busy_timeout=5000;")
                close_after = True
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
            if close_after:
                conn.close()
            self._metrics["facts_queued"] += 1
            # Mirror to local markdown memory
            self._write_local_memory(category, content)

    def _write_local_memory(self, category: str, content: str) -> None:
        """Mirror queued fact to a local markdown file.

        Each category gets its own ``.md`` file under ``self._memory_dir``.
        Entries are appended so the file grows monotonically.
        """
        if self._memory_dir is None:
            return
        safe_category = category.lower().replace(" ", "_")
        file_path = self._memory_dir / f"{safe_category}.md"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).isoformat()
        session = getattr(self, "_session_id", "unknown")
        entry = f"- [{timestamp}] [{session}] {content}\n"
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(entry)

    def _commit_memory_files(self) -> None:
        """DEPRECATED: Markdown auto-commits are disabled."""
        self._modified_files.clear()
        pass
    def _flush_queue(self, chunk_size: int = 100) -> None:
        with self._lock:
            conn = getattr(self, "_queue_conn", None)
            close_after = False
            if conn is None:
                conn = sqlite3.connect(self._queue_path)
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=NORMAL;")
                conn.execute("PRAGMA busy_timeout=5000;")
                close_after = True
            cursor = conn.execute(
                "SELECT id, action, category, content, source_session, source_file, created_at FROM queue ORDER BY id"
            )
            all_rows = cursor.fetchall()
            if not all_rows:
                if close_after:
                    conn.close()
                return

            ids_to_delete = []
            facts_flushed_success = 0

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
                        facts_flushed_success += len(chunk)
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
                            facts_flushed_success += 1
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
                            # Move to failed_queue and delete from queue to avoid infinite retry
                            ids_to_delete.append(row_id)

            if ids_to_delete:
                placeholders = ",".join("?" * len(ids_to_delete))
                conn.execute(f"DELETE FROM queue WHERE id IN ({placeholders})", ids_to_delete)
                conn.commit()
            if close_after:
                conn.close()
            self._metrics["facts_flushed"] += facts_flushed_success

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
    ctx.register_memory_provider(MemoraProvider())
