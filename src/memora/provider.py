"""RAG-backed memory provider for Hermes.

Thin orchestrator that delegates queue, mirroring, fact extraction, and
tool dispatch to focused submodules.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Callable, Dict, List

try:
    from agent.memory_provider import MemoryProvider
except ImportError:
    from abc import ABC

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

from .cache import SqliteL1Cache
from .company_rules import load_company_rules, resolve_company_memory_dir
from . import evaluations as _evaluations
from . import swarm_manager, triage
from .fact_extractor import extract_facts
from .http_client import HttpClient, HttpConfig
from .memory_mirror import write as _mirror_write
from .queue import FactQueue
from .tool_dispatcher import dispatch, search_cache_key

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
                "scope": {"type": "string", "description": "Search scope: 'personal' (default) or 'company'."},
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
                "scope": {"type": "string", "description": "Scope: 'personal' (default) or 'company'. Use 'company' for shared team facts."},
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
    {
        "name": "memora_think",
        "description": "Synthesize an answer across retrieved company memory. Returns a cited answer and a list of gaps. Use this when the user asks a question that requires pulling multiple facts together, instead of raw memora_search.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The question to answer."},
                "top_k": {"type": "integer", "description": "Number of facts to retrieve (default: 10)."},
                "scope": {"type": "string", "description": "Search scope: 'personal' or 'company'. Default respects the user's context."},
            },
            "required": ["query"],
        },
    },
]

# Trivial messages — do not persist as memory
_TRIVIAL_RE = __import__("re").compile(
    r"^(\s*(execute|go ahead|ok|yes|no|sure|done|retry|continue|stop|halt"
    r"|test|validate|check|review|analyse|analyze|proceed|next"
    r"|let's execute|let's go|please do|do it|run it"
    r"|hello|hi|hey|thanks|thank you|ty)\s*[.!?]*\s*)$",
    __import__("re").IGNORECASE,
)


class MemoraProvider(MemoryProvider):
    """RAG-backed memory provider that delegates to focused submodules."""

    def __init__(self) -> None:
        self._http: HttpClient | None = None
        self._queue: FactQueue | None = None
        self._l1_cache = SqliteL1Cache()
        self._memory_dir: Path | None = None
        self._company_memory_dir: Path | None = None
        self._session_id = ""
        self._owner_id = "anonymous"
        self._auto_ingest = True
        self._auto_swarm = False
        self._auto_commit = False
        self._metrics: dict[str, int] = {
            "facts_queued": 0,
            "facts_flushed": 0,
            "facts_failed": 0,
            "prefetch_calls": 0,
            "search_calls": 0,
            "circuit_opens": 0,
        }

    # ------------------------------------------------------------------
    # MemoryProvider interface
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "memora"

    def is_available(self) -> bool:
        url = os.environ.get("RAG_WORKER_URL", _DEFAULT_URL)
        token = os.environ.get("RAG_AUTH_TOKEN", _DEFAULT_TOKEN)
        if not url or not token:
            return False
        if "YOUR_" in token or "..." in token:
            return False
        return True

    def initialize(self, session_id: str, **kwargs) -> None:
        self._session_id = session_id
        hermes_home = kwargs.get("hermes_home", str(Path.home() / ".hermes"))
        agent_identity = kwargs.get("agent_identity", "default")
        config = kwargs.get("config", {})
        Path(hermes_home).mkdir(parents=True, exist_ok=True)

        # Load profile for owner_id
        profile_path = Path(hermes_home) / "memora.json"
        try:
            profile = json.loads(profile_path.read_text())
            self._owner_id = profile.get("first_name", "anonymous")
        except Exception:
            self._owner_id = "anonymous"

        self._auto_ingest = config.get("auto_ingest", True)
        self._auto_swarm = config.get("auto_swarm", False)
        self._auto_commit = config.get("auto_commit", False)
        self._memory_dir = Path(config.get("memory_dir", str(Path.home() / "hermes-workspace" / "memory")))
        self._memory_dir.mkdir(parents=True, exist_ok=True)
        self._company_memory_dir = resolve_company_memory_dir(config)

        base_url = os.environ.get("RAG_WORKER_URL", _DEFAULT_URL).rstrip("/")
        token = os.environ.get("RAG_AUTH_TOKEN", _DEFAULT_TOKEN)

        self._http = HttpClient(HttpConfig(base_url=base_url, token=token))

        queue_path = Path(hermes_home) / f"memora_queue_{agent_identity}.db"
        self._queue = FactQueue(queue_path, session_id=session_id)
        self._queue.set_callbacks(
            post_fn=self._http.post,
            mirror_fn=lambda cat, content: _mirror_write(self._memory_dir, cat, content, session_id=self._session_id),
        )
        self._queue.start_background_flush(interval_sec=60.0)

        # Warn if onboarding incomplete
        self._check_onboarding(hermes_home)

    def shutdown(self) -> None:
        if self._queue is not None:
            self._queue.close()
        logger.info("MemoraProvider shutdown. Metrics: %s", self._metrics)

    def system_prompt_block(self) -> str:
        base = (
            "You have access to a persistent long-term memory via the memora_* tools. "
            "Use memora_search to recall past context before answering. "
            "After learning something important, DO NOT use the default memory tool — ALWAYS use memora_add to persist it directly to the RAG backend. "
            "When using memora_add, you MUST explicitly categorize the fact using precise tags (e.g., projects, strategy, business, integrations, user) rather than dumping it into the default 'memory' bucket. "
            "Set scope='company' when the fact should be shared with the team; omit scope for personal notes."
        )
        rules = load_company_rules(self._company_memory_dir)
        if not rules:
            return base
        return f"{base}\n\n{rules}"

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        self._metrics["prefetch_calls"] += 1
        if self._http is None:
            return ""
        try:
            result = self._http.post("/search", {"query": query, "top_k": 5})
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
                text_hash = __import__("hashlib").sha256(text.encode("utf-8")).hexdigest()
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

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        # Raw turns are transcripts, not durable facts. Durable extraction is
        # handled at session boundaries by extract_facts(), and explicit facts
        # are written through memora_add with a precise category and scope.
        return

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return _TOOL_SCHEMAS

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        # memora_add interception (chunking + offline fallback)
        if tool_name == "memora_add" and "content" in args:
            return self._handle_add(args)

        # kanban_reassign interception
        if tool_name == "kanban_reassign":
            return self._handle_reassign(args)

        path, body = dispatch(tool_name, args, owner_id=self._owner_id)
        if self._http is None:
            return json.dumps({"error": "Memora provider is not initialized"})

        # Cache logic
        cache_key = None
        if tool_name == "memora_search":
            cache_key = search_cache_key(body)
            cached = self._l1_cache.get(cache_key)
            if cached is not None:
                self._metrics["search_calls"] += 1
                return json.dumps(cached)
        elif tool_name in ("memora_add", "memora_update", "memora_delete"):
            self._l1_cache.clear()

        try:
            if body is None:
                result = self._http.get(path)
            else:
                result = self._http.post(path, body)
            if (
                tool_name == "memora_search"
                and not result.get("results")
                and "scope" not in args
                and body is not None
                and body.get("owner_id")
            ):
                # The pre-owner migration corpus has no owner_id metadata.
                # Retry unscoped so legacy facts remain searchable while new
                # personal writes continue to use explicit ownership.
                legacy_body = dict(body)
                legacy_body.pop("owner_id", None)
                result = self._http.post(path, legacy_body)
            if tool_name == "memora_search":
                self._metrics["search_calls"] += 1
                if cache_key and "error" not in result:
                    self._l1_cache.set(cache_key, result, ttl_seconds=300)
            return json.dumps(result)
        except Exception as e:
            return json.dumps({"error": str(e)})

    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        pass

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        for fact in extract_facts(messages):
            if self._queue is not None and self._queue.add("memory", fact):
                self._metrics["facts_queued"] += 1
        if self._queue is not None:
            self._queue.flush()

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        return ""

    def on_delegation(self, task: str, result: str, *, child_session_id: str = "", **kwargs) -> None:
        if self._queue is not None:
            if self._queue.add("memory", f"Delegated task: {task[:300]}"):
                self._metrics["facts_queued"] += 1
            if self._queue.add("memory", f"Delegation result: {result[:300]}"):
                self._metrics["facts_queued"] += 1

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return [
            {"key": "worker_url", "description": "RAG Worker URL", "secret": False, "required": False, "default": _DEFAULT_URL},
            {"key": "auth_token", "description": "RAG Worker auth token", "secret": True, "required": False, "default": _DEFAULT_TOKEN, "env_var": "RAG_AUTH_TOKEN"},
            {"key": "auto_ingest", "description": "Auto-ingest facts from every turn", "secret": False, "required": False, "default": True},
            {"key": "auto_commit", "description": "Auto-commit memory markdown files to git after each session", "secret": False, "required": False, "default": True},
            {"key": "memory_dir", "description": "Local markdown memory directory", "secret": False, "required": False, "default": str(Path.home() / "hermes-workspace" / "memory")},
            {"key": "auto_swarm", "description": "Auto-spawn kanban delegate tasks when new facts are ingested", "secret": False, "required": False, "default": False},
        ]

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        config_path = Path(hermes_home) / "memora.json"
        config_path.write_text(json.dumps(values, indent=2))

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        if self._queue is not None:
            category = "user" if target == "user" else "memory"
            if self._queue.add(category, content):
                self._metrics["facts_queued"] += 1

    # ------------------------------------------------------------------
    # Evaluation helpers (non-ABC)
    # ------------------------------------------------------------------

    def add_eval_golden(self, content: str, fact_ids: List[str]) -> Dict[str, Any]:
        return self._http.post("/facts", {
            "content": content,
            "category": "eval_golden",
            "source_session": json.dumps(fact_ids),
        })

    def evaluate(self) -> Dict[str, Any]:
        return self._http.post("/evaluate", {})

    def evaluate_ceo_digest(self, digest_text: str, open_prs: List[dict]) -> Dict[str, Any]:
        evaluator = _evaluations.CeoDigestEvaluator()
        score = evaluator.evaluate(digest_text, open_prs)
        return score.to_dict()

    def evaluate_swarm_triggers(self, trigger_fn: Callable[..., dict] | None = None) -> Dict[str, Any]:
        evaluator = _evaluations.SwarmTriggerEvaluator()
        scores = evaluator.evaluate(trigger_fn)
        correct = sum(1 for s in scores if s.correct)
        return {
            "accuracy": correct / len(scores) if scores else 0.0,
            "total_cases": len(scores),
            "correct_cases": correct,
            "case_details": [s.to_dict() for s in scores],
        }

    def evaluate_rag_comprehensive(self, golden_dataset: List[Dict[str, Any]], k: int = 10) -> Dict[str, Any]:
        evaluator = _evaluations.RAGEvaluator(
            base_url=self._http.cfg.base_url if self._http else "",
            token=self._http.cfg.token if self._http else "",
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
        report = _evaluations.run_full_evaluation(
            provider=self,
            golden_dataset=golden_dataset,
            ceo_digest_text=ceo_digest_text,
            open_prs=open_prs,
            trigger_fn=trigger_fn,
        )
        return report.to_dict()

    def get_metrics(self) -> Dict[str, int]:
        return dict(self._metrics)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _handle_add(self, args: Dict[str, Any]) -> str:
        content_str = args["content"]
        category = args.get("category", "memory")
        parent_id = args.get("parent_id")

        from .importance import compute_importance
        try:
            importance = compute_importance(content_str, category)
        except Exception:
            importance = 0.5

        from .chunker import chunk_semantic
        max_chunk = 4000
        if len(content_str) > max_chunk:
            chunks = chunk_semantic(content_str, max_chars=3600, overlap_chars=200)
            results = []
            first_chunk_id = None
            for i, chunk in enumerate(chunks):
                chunk_parent = parent_id if i == 0 else (first_chunk_id or parent_id)
                try:
                    res = self._http.post("/memory/add", {
                        "content": f"[Part {i+1}/{len(chunks)}] {chunk}",
                        "category": category,
                        "parent_id": chunk_parent,
                        "owner_id": self._owner_id,
                        "scope": "personal",
                        "importance_score": importance,
                    })
                    results.append(res)
                    if i == 0:
                        first_chunk_id = res.get("id")
                except Exception as e:
                    if self._queue is not None:
                        self._queue.add(category, f"[Part {i+1}/{len(chunks)}] {chunk}")
                    results.append({"status": "queued_offline", "error": str(e)})
            self._maybe_trigger_swarm(content_str, category)
            self._l1_cache.clear()
            return json.dumps({"status": "success", "chunks_processed": len(chunks), "results": results})

        try:
            res = self._http.post("/memory/add", {
                "content": content_str,
                "category": category,
                "parent_id": parent_id,
                "owner_id": self._owner_id,
                "scope": "personal",
                "importance_score": importance,
                **({"id": args["id"]} if "id" in args else {}),
            })
            self._maybe_trigger_swarm(content_str, category)
            self._l1_cache.clear()
            return json.dumps(res)
        except Exception as e:
            if self._queue is not None:
                self._queue.add(category, content_str)
            self._maybe_trigger_swarm(content_str, category)
            self._l1_cache.clear()
            return json.dumps({"status": "queued_offline", "error": str(e), "message": "Network unavailable. Fact queued for background sync."})

    def _handle_reassign(self, args: Dict[str, Any]) -> str:
        from .feedback_interceptor import capture_routing_correction
        feedback_jsonl = Path.home() / ".hermes" / "routing_corrections.jsonl"
        correction = capture_routing_correction(args, jsonl_path=str(feedback_jsonl))
        if self._queue is not None:
            self._queue.add("feedback", json.dumps(correction))
        return json.dumps({"status": "feedback_captured", "correction": correction})

    def _maybe_trigger_swarm(self, content: str, category: str) -> None:
        if not self._auto_swarm:
            return
        if triage.should_trigger_swarm(content):
            swarm_manager.trigger(source="rag", content=content, category=category)

    def _check_onboarding(self, hermes_home: str) -> None:
        from . import onboarding as _onboarding
        if _onboarding.load_profile(hermes_home=hermes_home) is None:
            logger.warning(
                "Memora profile missing at %s. Run './install.sh' or "
                "'python -m memora.onboarding' to set up your Digital Twin.",
                Path(hermes_home) / "memora.json",
            )


def register(ctx) -> None:
    """Plugin registration entry point."""
    ctx.register_memory_provider(MemoraProvider())
