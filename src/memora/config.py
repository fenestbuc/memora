"""Unified configuration for Memora.

Loads configuration from (lowest to highest precedence):
  1. Built-in defaults
  2. pyproject.toml [tool.memora] table (if present)
  3. memora.json in HERMES_HOME
  4. Environment variables
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ._version import __version__


@dataclass
class MemoraConfig:
    """Type-safe configuration container."""

    worker_url: str = ""
    auth_token: str = ""
    auto_ingest: bool = True
    auto_commit: bool = True
    memory_dir: Path = field(default_factory=lambda: Path.home() / "hermes-workspace" / "memory")
    prefetch_threshold: float = 0.5
    auto_swarm: bool = False
    kanban_backend: str = "hermes"
    tunnel_provider: str = "cloudflared"
    hermes_home: Path = field(default_factory=lambda: Path.home() / ".hermes")

    @classmethod
    def load(cls, hermes_home: str | Path | None = None, **overrides: Any) -> "MemoraConfig":
        """Build configuration from defaults → env → JSON file → overrides."""
        cfg = cls()

        if hermes_home is not None:
            cfg.hermes_home = Path(hermes_home)

        # 1. Environment variables
        cfg.worker_url = os.environ.get("RAG_WORKER_URL", cfg.worker_url)
        cfg.auth_token = os.environ.get("RAG_AUTH_TOKEN", cfg.auth_token)
        cfg.kanban_backend = os.environ.get("MEMORA_KANBAN_BACKEND", cfg.kanban_backend)
        cfg.tunnel_provider = os.environ.get("MEMORA_TUNNEL", cfg.tunnel_provider)

        # 2. JSON profile
        profile_path = cfg.hermes_home / "memora.json"
        if profile_path.exists():
            try:
                data = json.loads(profile_path.read_text())
                cfg.worker_url = data.get("worker_url", cfg.worker_url)
                cfg.auth_token = data.get("auth_token", cfg.auth_token)
                cfg.auto_ingest = data.get("auto_ingest", cfg.auto_ingest)
                cfg.auto_commit = data.get("auto_commit", cfg.auto_commit)
                cfg.auto_swarm = data.get("auto_swarm", cfg.auto_swarm)
                cfg.kanban_backend = data.get("kanban_backend", cfg.kanban_backend)
                cfg.tunnel_provider = data.get("tunnel_provider", cfg.tunnel_provider)
                if "memory_dir" in data:
                    cfg.memory_dir = Path(data["memory_dir"])
            except json.JSONDecodeError:
                pass

        # 3. Caller overrides (highest precedence)
        for key, val in overrides.items():
            if hasattr(cfg, key):
                setattr(cfg, key, val)

        return cfg

    def to_dict(self) -> dict[str, Any]:
        return {
            "worker_url": self.worker_url,
            "auth_token": self.auth_token,
            "auto_ingest": self.auto_ingest,
            "auto_commit": self.auto_commit,
            "memory_dir": str(self.memory_dir),
            "prefetch_threshold": self.prefetch_threshold,
            "auto_swarm": self.auto_swarm,
            "kanban_backend": self.kanban_backend,
            "tunnel_provider": self.tunnel_provider,
            "version": __version__,
        }
