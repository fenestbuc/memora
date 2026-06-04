"""Tests for Kanban backend dispatch."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from memora import swarm_manager


class TestBackendSelection:
    def test_defaults_to_hermes(self) -> None:
        backend = swarm_manager._get_configured_backend()
        assert backend in ("hermes", "none")

    @patch.dict(os.environ, {"MEMORA_KANBAN_BACKEND": "linear"})
    def test_env_override(self) -> None:
        backend = swarm_manager._get_configured_backend()
        assert backend == "linear"

    @patch.dict(os.environ, {"MEMORA_KANBAN_BACKEND": "invalid"})
    def test_invalid_fallback(self) -> None:
        backend = swarm_manager._get_configured_backend()
        assert backend == "none"


class TestLinearPayload:
    def test_issue_payload_structure(self) -> None:
        from memora.swarm_manager import _linear_create_issue

        # Mock urllib to capture the payload
        captured = {}
        original_urlopen = __import__("urllib.request").request.urlopen

        class FakeResp:
            def read(self):
                return json.dumps({"data": {"issueCreate": {"issue": {"id": "i1"}}}}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        def fake_urlopen(req, **kwargs):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return FakeResp()

        with patch.dict(os.environ, {"LINEAR_API_KEY": "test-token"}):
            with patch("urllib.request.urlopen", fake_urlopen):
                _linear_create_issue("Test title", "Test body", tags=["memora"])

        assert captured["body"]["variables"]["input"]["title"] == "Test title"
        assert captured["body"]["variables"]["input"]["description"] == "Test body"
