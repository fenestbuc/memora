"""Tests for Memora daemon (Phase 4, Task 4).

Run with: pytest tests/test_daemon.py -v
"""

from __future__ import annotations

import json
import os
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from memora.daemon import (
    app,
    parse_args,
    spawn_cloudflare_tunnel,
    _resolve_cloudflared_binary,
    _should_enable_tunnel,
)

client = TestClient(app)


class TestHealthEndpoint:
    """Tests for the /health liveness probe."""

    def test_health_returns_ok(self):
        """/health must return status ok and service name."""
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "service": "memora-daemon",
        }


class TestDiscordParse:
    """Tests for the /discord/parse endpoint."""

    def test_parse_valid_payload(self):
        """/discord/parse must extract content, author, and channel_id."""
        payload = json.dumps(
            {
                "content": "Hello world",
                "author": {"username": "tester"},
                "channel_id": "123",
            }
        )
        response = client.post("/discord/parse", content=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["content"] == "Hello world"
        assert data["author"] == "tester"
        assert data["channel_id"] == "123"

    def test_parse_invalid_payload(self):
        """/discord/parse must return 400 for malformed JSON."""
        response = client.post("/discord/parse", content="not-json")
        assert response.status_code == 400
        assert "error" in response.json()

    def test_parse_from_bytes(self):
        """/discord/parse must accept raw bytes body."""
        payload = json.dumps(
            {"content": "Byte me", "author": {"username": "bot"}}
        ).encode("utf-8")
        response = client.post("/discord/parse", content=payload)
        assert response.status_code == 200
        assert response.json()["content"] == "Byte me"


class TestDiscordQuery:
    """Tests for the /discord/query endpoint."""

    @patch("memora.daemon._get_search_fn")
    def test_query_runs_search(self, mock_get_search):
        """/discord/query must call search_fn and return the RAG result."""
        mock_search = MagicMock(return_value="Some memory")
        mock_get_search.return_value = mock_search

        response = client.post(
            "/discord/query",
            json={"payload": {"content": "What is our runway?"}},
        )
        assert response.status_code == 200
        assert "Some memory" in response.json()["response"]
        mock_search.assert_called_once_with("What is our runway?")

    def test_query_missing_payload(self):
        """/discord/query must return 400 when 'payload' is missing."""
        response = client.post("/discord/query", json={})
        assert response.status_code == 400
        assert "Missing 'payload'" in response.json()["error"]

    def test_query_non_object_body(self):
        """/discord/query must return 400 when body is not a JSON object."""
        response = client.post("/discord/query", json=[1, 2, 3])
        assert response.status_code == 400
        assert "Expected JSON object" in response.json()["error"]


class TestDiscordWebhook:
    """Tests for the /discord/webhook endpoint."""

    @patch("memora.daemon._get_search_fn")
    def test_webhook_full_flow(self, mock_get_search):
        """/discord/webhook must parse payload, strip mention, proxy query."""
        mock_search = MagicMock(return_value="Relevant memory result")
        mock_get_search.return_value = mock_search

        payload = json.dumps(
            {
                "content": "@Memora what is pricing?",
                "author": {"username": "vaibhav"},
                "channel_id": "456",
            }
        )
        response = client.post("/discord/webhook", content=payload)
        assert response.status_code == 200
        data = response.json()
        assert "Relevant memory result" in data["response"]
        assert data["author"] == "vaibhav"
        assert data["channel_id"] == "456"
        mock_search.assert_called_once_with("what is pricing?")

    def test_webhook_invalid_json(self):
        """/discord/webhook must return 400 for non-JSON body."""
        response = client.post("/discord/webhook", content="bad json")
        assert response.status_code == 400
        assert "error" in response.json()

    def test_webhook_empty_content(self):
        """/discord/webhook must return friendly no-content message."""
        payload = json.dumps(
            {"content": "   ", "author": {"username": "ghost"}}
        )
        response = client.post("/discord/webhook", content=payload)
        assert response.status_code == 200
        assert "No message content" in response.json()["response"]


# ---------------------------------------------------------------------------
# Tunnel support tests
# ---------------------------------------------------------------------------

class TestParseArgs:
    """Tests for CLI argument parsing."""

    def test_default_port(self):
        args = parse_args([])
        assert args.port == 8742
        assert args.tunnel is False

    def test_tunnel_flag(self):
        args = parse_args(["--tunnel"])
        assert args.tunnel is True

    def test_custom_port(self):
        args = parse_args(["--port", "9000"])
        assert args.port == 9000


class TestShouldEnableTunnel:
    """Tests for tunnel enablement logic."""

    def test_enabled_by_flag(self):
        args = parse_args(["--tunnel"])
        assert _should_enable_tunnel(args) is True

    def test_disabled_by_default(self):
        args = parse_args([])
        assert _should_enable_tunnel(args) is False

    @patch.dict(os.environ, {"MEMORA_TUNNEL": "1"})
    def test_enabled_by_env(self):
        args = parse_args([])
        assert _should_enable_tunnel(args) is True

    @patch.dict(os.environ, {"MEMORA_TUNNEL": "0"})
    def test_disabled_by_env_zero(self):
        args = parse_args([])
        assert _should_enable_tunnel(args) is False


class TestResolveCloudflaredBinary:
    """Tests for cloudflared binary resolution."""

    @patch("memora.daemon.os.path.isfile", return_value=True)
    def test_prefers_hermes_bin(self, mock_isfile):
        path = _resolve_cloudflared_binary()
        assert path == os.path.expanduser("~/.hermes/bin/cloudflared")

    @patch("memora.daemon.os.path.isfile", return_value=False)
    def test_falls_back_to_path(self, mock_isfile):
        path = _resolve_cloudflared_binary()
        assert path == "cloudflared"


class TestSpawnCloudflareTunnel:
    """Tests for cloudflared subprocess spawning."""

    @patch("memora.daemon.subprocess.Popen")
    def test_writes_tunnel_url_and_logs(self, mock_popen, tmp_path):
        """When stderr contains a trycloudflare.com URL, write it to file."""
        fake_stderr = StringIO(
            "INF Connection registered connIndex=0 location=SIN\n"
            "INF |  https://abc123.trycloudflare.com  |\n"
        )
        proc = MagicMock()
        proc.stderr = fake_stderr
        mock_popen.return_value = proc

        hermes_home = tmp_path / ".hermes"
        with patch(
            "memora.daemon.os.path.expanduser", return_value=str(hermes_home)
        ):
            spawn_cloudflare_tunnel(8742)

        tunnel_file = hermes_home / "memora_tunnel.txt"
        assert tunnel_file.read_text() == "https://abc123.trycloudflare.com"

    @patch("memora.daemon.subprocess.Popen")
    def test_no_url_terminates_process(self, mock_popen):
        """When no tunnel URL appears, terminate the process."""
        fake_stderr = StringIO("Some irrelevant log line\n")
        proc = MagicMock()
        proc.stderr = fake_stderr
        mock_popen.return_value = proc

        spawn_cloudflare_tunnel(8742)

        proc.terminate.assert_called_once()

    @patch("memora.daemon.subprocess.Popen")
    def test_file_not_found_logged(self, mock_popen, caplog):
        """If cloudflared binary is missing, log an error and return."""
        mock_popen.side_effect = FileNotFoundError("No such file")

        with patch(
            "memora.daemon._resolve_cloudflared_binary",
            return_value="/fake/cloudflared",
        ):
            with caplog.at_level("ERROR", logger="memora.daemon"):
                spawn_cloudflare_tunnel(8742)

        assert "cloudflared binary not found" in caplog.text
