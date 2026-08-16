"""Memora — persistent semantic memory for AI agents.

A second-brain plugin that gives your AI assistant long-term memory
across sessions via a Cloudflare Workers RAG backend.
"""

from ._version import __version__
from .provider import MemoraProvider

__author__ = "Memora Contributors"


def register(ctx) -> None:
    ctx.register_memory_provider(MemoraProvider())


__all__ = ["__version__", "MemoraProvider", "register"]
