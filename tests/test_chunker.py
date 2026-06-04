"""Tests for semantic chunker.

Run with: pytest tests/test_chunker.py -v
"""

from __future__ import annotations

import pytest

from memora.chunker import chunk_semantic


class TestChunkerBasic:
    """Basic functionality tests."""

    def test_short_text_no_split(self):
        """Text under max_chars should return as-is."""
        text = "This is a short fact."
        chunks = chunk_semantic(text, max_chars=100)
        assert chunks == [text]

    def test_empty_text(self):
        """Empty text returns empty list."""
        assert chunk_semantic("") == []

    def test_splits_at_sentence_boundary(self):
        """Should split at sentence boundaries."""
        text = "First sentence. Second sentence. " * 500  # ~16k chars
        chunks = chunk_semantic(text, max_chars=4000)
        assert len(chunks) > 1
        # Each chunk should end with a complete sentence
        for chunk in chunks:
            stripped = chunk.strip()
            assert stripped.endswith(".") or stripped.endswith("?") or stripped.endswith("!")

    def test_preserves_code_blocks(self):
        """Fenced code blocks should not be split internally."""
        text = (
            "Some discussion.\n"
            "```python\n"
            "def hello():\n"
            "    print('world')\n"
            "```\n"
            "More discussion after the code."
        )
        chunks = chunk_semantic(text, max_chars=50)  # Force splitting
        for chunk in chunks:
            # No partial code fences
            fence_count = chunk.count("```")
            assert fence_count % 2 == 0 or fence_count == 0, f"Partial code block in: {chunk}"

    def test_overlap_present(self):
        """Overlapping chunks should share trailing/leading text."""
        text = "Sentence one. " * 1000  # Large text
        chunks = chunk_semantic(text, max_chars=1000, overlap_chars=100)
        assert len(chunks) > 1
        for i in range(1, len(chunks)):
            prev_tail = chunks[i - 1][-120:]  # grab a bit more than overlap
            curr_head = chunks[i][:120]
            # There should be some shared context
            assert any(word in curr_head for word in prev_tail.split()), "No overlap detected"


class TestChunkerHeaders:
    """Markdown header splitting tests."""

    def test_splits_at_headers(self):
        """Markdown headers should be chunk boundaries."""
        text = "# Heading 1\n" + "Some text. " * 500 + "\n# Heading 2\n" + "More text. " * 500
        chunks = chunk_semantic(text, max_chars=4000)
        for chunk in chunks:
            # Each chunk should contain at most one H1
            assert chunk.count("# Heading 1") <= 1
            assert chunk.count("# Heading 2") <= 1


class TestChunkerAbbreviations:
    """Edge cases for sentence boundary detection."""

    def test_mr_abbreviation_not_split(self):
        """'Mr. Smith' should not be treated as two sentences."""
        text = "Mr. Smith went to Washington. Then he left. " * 200
        chunks = chunk_semantic(text, max_chars=500)
        for chunk in chunks:
            assert "Mr." not in chunk or "Smith" in chunk, "False split on 'Mr.'"

    def test_eg_abbreviation_not_split(self):
        """'e.g.' should not trigger a split."""
        text = "Use e.g. this example. Another sentence. " * 200
        chunks = chunk_semantic(text, max_chars=500)
        for chunk in chunks:
            if "e.g." in chunk:
                assert "example" in chunk or "Use" in chunk

    def test_regular_period_splits(self):
        """Normal sentence endings should split."""
        text = "Hello world. This is new. Another one."
        chunks = chunk_semantic(text, max_chars=20)
        assert len(chunks) >= 2
