"""Semantic chunker — splits text at sentence boundaries while preserving
Markdown structure and respecting a max character budget.

Uses regex-based heuristics (zero dependencies) to avoid pulling in NLTK
or spaCy, keeping Memora lightweight for open-source users.
"""

from __future__ import annotations

import re
from typing import Generator, List

# Sentence terminators that should trigger a split.
# The negative lookahead avoids splitting "Mr. Smith" or "e.g. hello".
_SENTENCE_RE = re.compile(
    r"(?<=[.!?])(\s+)(?=[A-Z])"  # period/question/exclamation + space + capital
)

# Markdown headers are always split points.
_HEADER_RE = re.compile(r"(\n#{1,6}\s+)")

# Code block boundaries.
_CODE_FENCE_RE = re.compile(r"^```[a-zA-Z0-9]*$")

# Simple heuristics for non-sentence-ending periods.
_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr",
    "vs", "etc", "eg", "ie", "fig", "vol", "inc", "ltd",
}


def _is_sentence_end(text: str, pos: int) -> bool:
    """Heuristic: is the period at *pos* a real sentence terminator?"""
    # Look back for abbreviation
    lookback = text[max(0, pos - 10):pos].lower()
    for abbrev in _ABBREVIATIONS:
        if lookback.endswith(abbrev):
            return False
    # Look ahead for lowercase (likely not a new sentence)
    if pos + 1 < len(text) and text[pos + 1].islower():
        return False
    return True


def chunk_semantic(
    text: str,
    max_chars: int = 3600,
    overlap_chars: int = 200,
) -> List[str]:
    """Split *text* into coherent chunks at sentence boundaries.

    Strategy (in order of priority):
    1. **Code blocks** — fenced ``` blocks are kept intact if under *max_chars*.
    2. **Markdown headers** — always used as split points.
    3. **Sentence boundaries** — split at ``.!?`` followed by space + capital.
    4. **Word boundaries** — if no sentence boundary exists, split at word.

    Each chunk after the first includes *overlap_chars* from the previous
    chunk to preserve context.

    Args:
        max_chars: Target maximum characters per chunk.  The default 3600
            leaves headroom for the ``"[Part N/M] "`` prefix in RAG payloads.
        overlap_chars: Number of trailing characters from the previous
            chunk to prepend to the next chunk.

    Returns:
        Non-empty list of chunk strings.
    """
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    # Step 1: Extract code blocks as atomic units
    segments: List[str] = []
    in_code = False
    code_buffer: List[str] = []
    normal_buffer: List[str] = []

    for line in text.splitlines(keepends=True):
        if _CODE_FENCE_RE.match(line.strip()):
            if in_code:
                # End of code block
                code_buffer.append(line)
                segments.append("".join(code_buffer))
                code_buffer = []
                in_code = False
            else:
                # Start of code block — flush normal buffer first
                if normal_buffer:
                    segments.extend(_split_normal("".join(normal_buffer), max_chars))
                    normal_buffer = []
                code_buffer.append(line)
                in_code = True
        elif in_code:
            code_buffer.append(line)
        else:
            normal_buffer.append(line)

    if normal_buffer:
        segments.extend(_split_normal("".join(normal_buffer), max_chars))
    if code_buffer:
        segments.extend(_split_normal("".join(code_buffer), max_chars))

    # Step 2: Apply overlap between consecutive chunks
    if len(segments) <= 1 or overlap_chars <= 0:
        return [s for s in segments if s.strip()]

    overlapped: List[str] = [segments[0]]
    for i in range(1, len(segments)):
        prev = segments[i - 1]
        overlap = prev[-overlap_chars:] if len(prev) > overlap_chars else prev
        overlapped.append(overlap + segments[i])

    return [s for s in overlapped if s.strip()]


def _split_normal(text: str, max_chars: int) -> Generator[str, None, None]:
    """Split plain text (non-code) into chunks respecting headers + sentences."""
    if len(text) <= max_chars:
        yield text
        return

    # Split at headers first, preserving the header due to capture group
    parts = _HEADER_RE.split(text)
    
    # Re-combine the header delimiters with their subsequent text blocks
    combined_parts = []
    current_part = ""
    for part in parts:
        if _HEADER_RE.match(part):
            current_part = part
        else:
            combined_parts.append(current_part + part)
            current_part = ""
            
    if current_part:
        combined_parts.append(current_part)

    for part in combined_parts:
        part = part.strip()
        if not part:
            continue
        if len(part) <= max_chars:
            yield part
            continue

        # Split at sentence boundaries
        sentences = _split_sentences(part)
        chunk = ""
        for sentence in sentences:
            if len(sentence) > max_chars:
                # A single sentence is too long — hard-split at word boundaries
                if chunk:
                    yield chunk
                    chunk = ""
                yield from _hard_split(sentence, max_chars)
                continue
            if len(chunk) + len(sentence) > max_chars and chunk:
                yield chunk
                chunk = sentence
            else:
                chunk += sentence
        if chunk:
            yield chunk


def _hard_split(text: str, max_chars: int) -> Generator[str, None, None]:
    """Split *text* into chunks of at most *max_chars* at word boundaries."""
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        # Try to back up to a word boundary
        if end < len(text):
            while end > start and text[end] not in " \t\n":
                end -= 1
            if end == start:  # no word boundary found; force split
                end = start + max_chars
        yield text[start:end]
        start = end


def _split_sentences(text: str) -> List[str]:
    """Split text into sentences using regex heuristics."""
    # Use the regex split with capture group to keep the delimiter spaces
    raw = _SENTENCE_RE.split(text)
    if len(raw) <= 1:
        return [text]

    # Recombine parts with their trailing whitespace matches
    merged_raw = []
    # parts will be [sentence, space, sentence, space, sentence]
    for i in range(0, len(raw) - 1, 2):
        merged_raw.append(raw[i] + raw[i+1])
    if len(raw) % 2 != 0:
        merged_raw.append(raw[-1])

    result: List[str] = [merged_raw[0]]
    for fragment in merged_raw[1:]:
        # Check if the split was at a false sentence boundary
        last_chunk = result[-1]
        boundary_pos = len(last_chunk.rstrip()) - 1
        while boundary_pos >= 0 and last_chunk[boundary_pos] != ".":
            boundary_pos -= 1

        if boundary_pos >= 0 and not _is_sentence_end(last_chunk, boundary_pos):
            # Merge back (whitespace already preserved by capture group)
            result[-1] = last_chunk + fragment
        else:
            result.append(fragment)

    return result
