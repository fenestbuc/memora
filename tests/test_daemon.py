"""Tests for Memora daemon.

Run with: pytest tests/test_daemon.py -v
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
from unittest.mock import MagicMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

from memora.daemon import (
    app,
    parse_args,
    spawn_cloudflare_tunnel,
    spawn_ngrok_tunnel,
    spawn_localtunnel,
    _resolve_binary,
    _spawn_subprocess_with_drain,
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
        assert args.tunnel == ""

    def test_tunnel_cloudflared(self):
        args = parse_args(["--tunnel", "cloudflared"])
        assert args.tunnel == "cloudflared"

    def test_tunnel_ngrok(self):
        args = parse_args(["--tunnel", "ngrok"])
        assert args.tunnel == "ngrok"

    def test_custom_port(self):
        args = parse_args(["--port", "9000"])
        assert args.port == 9000


class TestResolveBinary:
    """Tests for binary resolution."""

    @patch("memora.daemon.os.path.isfile", return_value=True)
    def test_prefers_hermes_bin(self, mock_isfile):
        path = _resolve_binary("cloudflared", "cloudflared")
        assert path == os.path.expanduser("~/.hermes/bin/cloudflared")

    @patch("memora.daemon.subprocess.run")
    def test_falls_back_to_path(self, mock_run):
        mock_run.return_value = Mock(returncode=0)
        path = _resolve_binary("cloudflared")
        assert path == "cloudflared"


class TestSpawnSubprocessWithDrain:
    """Tests for deadlock-safe subprocess spawning."""

    @patch("memora.daemon.subprocess.Popen")
    def test_drains_stdout_and_stderr(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.stdout = MagicMock()
        mock_proc.stdout.readline = MagicMock(side_effect=["line1\n", "line2\n", ""])
        mock_proc.stderr = MagicMock()
        mock_proc.stderr.readline = MagicMock(side_effect=["err1\n", ""])
        mock_popen.return_value = mock_proc

        proc, output_queue = _spawn_subprocess_with_drain(["echo", "test"])
        # Give daemon threads a moment to drain
        import time
        time.sleep(0.1)

        assert proc == mock_proc
        assert not output_queue.empty()


class TestSpawnCloudflareTunnel:
    """Tests for cloudflared subprocess spawning."""

    @patch("memora.daemon._resolve_binary", return_value="/fake/cloudflared")
    @patch("memora.daemon._spawn_subprocess_with_drain")
    def test_writes_tunnel_url_and_logs(self, mock_spawn, mock_resolve, tmp_path):
        """When output contains a trycloudflare.com URL, write it to file."""
        q = queue.Queue()
        q.put("INF Connection registered connIndex=0 location=SIN")
        q.put("INF |  https://abc123.trycloudflare.com  |")
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_spawn.return_value = (mock_proc, q)

        hermes_home = tmp_path / ".hermes"
        with patch(
            "memora.daemon.os.path.expanduser", return_value=str(hermes_home)
        ):
            spawn_cloudflare_tunnel(8742)

        tunnel_file = hermes_home / "memora_tunnel.txt"
        assert tunnel_file.exists()
        assert "https://abc123.trycloudflare.com" in tunnel_file.read_text()

    @patch("memora.daemon._resolve_binary", return_value="/fake/cloudflared")
    @patch("memora.daemon._spawn_subprocess_with_drain")
    def test_no_url_terminates_process(self, mock_spawn, mock_resolve):
        """When no tunnel URL appears, terminate the process."""
        q = queue.Queue()
        q.put("Some irrelevant log line")
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1  # process exited
        mock_spawn.return_value = (mock_proc, q)

        spawn_cloudflare_tunnel(8742)
        mock_proc.terminate.assert_called_once()

    @patch("memora.daemon._resolve_binary", return_value=None)
    def test_binary_not_found_logged(self, mock_resolve, caplog):
        """If cloudflared binary is missing, log an error and return."""
        with caplog.at_level("ERROR", logger="memora.daemon"):
            spawn_cloudflare_tunnel(8742)

        assert "cloudflared not found" in caplog.text


class TestSpawnNgrokTunnel:
    """Tests for ngrok subprocess spawning."""

    @patch("memora.daemon._resolve_binary", return_value="/fake/ngrok")
    @patch("memora.daemon._spawn_subprocess_with_drain")
    def test_writes_ngrok_url(self, mock_spawn, mock_resolve, tmp_path):
        q = queue.Queue()
        q.put("msg=tunnel started url=https://abc.ngrok-free.app")
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_spawn.return_value = (mock_proc, q)

        hermes_home = tmp_path / ".hermes"
        with patch(
            "memora.daemon.os.path.expanduser", return_value=str(hermes_home)
        ):
            spawn_ngrok_tunnel(8742)

        tunnel_file = hermes_home / "memora_tunnel.txt"
        assert tunnel_file.exists()
        assert "https://abc.ngrok-free.app" in tunnel_file.read_text()

    @patch("memora.daemon._resolve_binary", return_value=None)
    def test_ngrok_not_found(self, mock_resolve, caplog):
        with caplog.at_level("ERROR", logger="memora.daemon"):
            spawn_ngrok_tunnel(8742)
        assert "ngrok not found" in caplog.text


class TestSpawnLocaltunnel:
    """Tests for localtunnel subprocess spawning."""

    @patch("memora.daemon._resolve_binary", return_value="/fake/lt")
    @patch("memora.daemon._spawn_subprocess_with_drain")
    def test_writes_loca_lt_url(self, mock_spawn, mock_resolve, tmp_path):
        q = queue.Queue()
        q.put("your url is: https://abc.loca.lt")
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_spawn.return_value = (mock_proc, q)

        hermes_home = tmp_path / ".hermes"
        with patch(
            "memora.daemon.os.path.expanduser", return_value=str(hermes_home)
        ):
            spawn_localtunnel(8742)

        tunnel_file = hermes_home / "memora_tunnel.txt"
        assert tunnel_file.exists()
        assert "https://abc.loca.lt" in tunnel_file.read_text()

    @patch("memora.daemon._resolve_binary", return_value=None)
    def test_lt_not_found(self, mock_resolve, caplog):
        with caplog.at_level("ERROR", logger="memora.daemon"):
            spawn_localtunnel(8742)
        assert "localtunnel (lt) not found" in caplog.text
