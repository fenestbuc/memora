"""Tests for MCP sync (Notion polling) and Discord proxy (Phase 4, Task 4).

Run with: pytest tests/test_mcp_proxy.py -v
"""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from memora.mcp_sync import poll_notion_database
from memora.discord_proxy import parse_discord_payload, proxy_query


class TestNotionPolling:
    """Tests for Notion database polling."""

    @patch("memora.mcp_sync.urllib.request.urlopen")
    def test_poll_notion_database_calls_api(self, mock_urlopen):
        """poll_notion_database must call the Notion query endpoint."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"results": []}).encode()
        mock_urlopen.return_value = mock_resp

        poll_notion_database("test_token", "db_123")

        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "https://api.notion.com/v1/databases/db_123/query"
        assert req.get_header("Authorization") == "Bearer test_token"
        assert req.get_header("Notion-version") == "2022-06-28"

    @patch("memora.mcp_sync.urllib.request.urlopen")
    def test_poll_notion_database_returns_pages(self, mock_urlopen):
        """poll_notion_database must return page facts with title and url."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(
            {
                "results": [
                    {
                        "id": "page_1",
                        "properties": {
                            "Name": {
                                "type": "title",
                                "title": [
                                    {"plain_text": "NavDhan GTM Strategy"}
                                ],
                            }
                        },
                        "last_edited_time": "2026-06-01T12:00:00.000Z",
                        "url": "https://notion.so/page_1",
                    }
                ]
            }
        ).encode()
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        pages = poll_notion_database("test_token", "db_123")

        assert len(pages) == 1
        assert pages[0]["id"] == "page_1"
        assert pages[0]["title"] == "NavDhan GTM Strategy"
        assert pages[0]["url"] == "https://notion.so/page_1"

    @patch("memora.mcp_sync.urllib.request.urlopen")
    def test_poll_notion_database_respects_last_poll(self, mock_urlopen):
        """poll_notion_database must filter by last_edited_time when last_poll_ts is given."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"results": []}).encode()
        mock_urlopen.return_value = mock_resp

        now = time.time()
        poll_notion_database("test_token", "db_123", last_poll_ts=now)

        req = mock_urlopen.call_args[0][0]
        sent_body = json.loads(req.data.decode("utf-8"))
        assert "filter" in sent_body
        assert sent_body["filter"]["timestamp"] == "last_edited_time"

    @patch("memora.mcp_sync.urllib.request.urlopen", side_effect=Exception("timeout"))
    def test_poll_notion_database_graceful_failure(self, mock_urlopen):
        """On network failure, poll_notion_database must return an empty list."""
        pages = poll_notion_database("test_token", "db_123")
        assert pages == []


class TestDiscordProxy:
    """Tests for Discord webhook proxying to local RAG."""

    def test_parse_discord_payload_from_str(self):
        """parse_discord_payload must extract content, author, and channel from a JSON string."""
        payload = json.dumps(
            {
                "content": "What did we decide about pricing?",
                "author": {"username": "vaibhav"},
                "channel_id": "123456",
            }
        )
        parsed = parse_discord_payload(payload)
        assert parsed["content"] == "What did we decide about pricing?"
        assert parsed["author"] == "vaibhav"
        assert parsed["channel_id"] == "123456"

    def test_parse_discord_payload_from_dict(self):
        """parse_discord_payload must accept a pre-parsed dict."""
        payload = {
            "content": "Hello",
            "author": {"username": "bot"},
            "channel_id": "789",
        }
        parsed = parse_discord_payload(payload)
        assert parsed["content"] == "Hello"
        assert parsed["author"] == "bot"

    def test_proxy_query_calls_search_fn(self):
        """proxy_query must call the provided search function with stripped content."""
        mock_search = MagicMock(return_value="- [business] Pricing is 1.25% on disbursals.")
        payload = {"content": "What did we decide about pricing?"}

        response = proxy_query(payload, mock_search)

        mock_search.assert_called_once_with("What did we decide about pricing?")
        assert "Pricing is 1.25%" in response

    def test_proxy_query_strips_mention_prefix(self):
        """proxy_query must strip @Memora mention prefix from content."""
        mock_search = MagicMock(return_value="result")
        payload = {"content": "@Memora what is our runway?"}

        proxy_query(payload, mock_search)
        mock_search.assert_called_once_with("what is our runway?")

    def test_proxy_query_handles_empty_content(self):
        """proxy_query must return a friendly message when content is empty."""
        mock_search = MagicMock()
        payload = {"content": "   "}

        response = proxy_query(payload, mock_search)
        mock_search.assert_not_called()
        assert "No message content" in response

    def test_proxy_query_handles_search_failure(self):
        """proxy_query must return a graceful error when RAG search fails."""
        mock_search = MagicMock(side_effect=Exception("RAG unreachable"))
        payload = {"content": "Tell me about the Q3 plan"}

        response = proxy_query(payload, mock_search)
        assert "couldn't retrieve memory" in response

    def test_proxy_query_no_results(self):
        """proxy_query must indicate when no memories are found."""
        mock_search = MagicMock(return_value="")
        payload = {"content": "What is the meaning of life?"}

        response = proxy_query(payload, mock_search)
        assert "don't have any relevant memories" in response
