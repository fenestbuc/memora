"""Memora prompts used by the swarm manager and evaluation pipeline."""

from __future__ import annotations

KANBAN_ROUTING_PROMPT = """
You are a Kanban routing assistant. Given a source, category, scope, and content,
determine which agent role should handle the task.

Rules:
- strategy, business, projects -> analyst
- integrations, memory -> reviewer
"""
