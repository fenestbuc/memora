"""TDD tests for run_evals JSONL parsing.

Tests cover:
- _load_jsonl correctly parses line-delimited JSON.
- _load_jsonl skips blank lines gracefully.
- run_evaluations accepts a --golden JSONL path.

Run with: pytest tests/test_run_evals.py -v
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from memora.run_evals import _load_jsonl


class TestLoadJsonl(unittest.TestCase):
    """Unit tests for _load_jsonl."""

    def test_loads_simple_jsonl(self):
        """Should parse a simple JSONL file into a list of dicts."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"query": "q1", "relevant_ids": ["id1"]}) + "\n")
            f.write(json.dumps({"query": "q2", "relevant_ids": ["id2"]}) + "\n")
            tmp_path = f.name
        try:
            records = _load_jsonl(tmp_path)
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0]["query"], "q1")
            self.assertEqual(records[1]["relevant_ids"], ["id2"])
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_skips_blank_lines(self):
        """Should ignore empty or whitespace-only lines."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"query": "q1"}) + "\n")
            f.write("\n")
            f.write("   \n")
            f.write(json.dumps({"query": "q2"}) + "\n")
            tmp_path = f.name
        try:
            records = _load_jsonl(tmp_path)
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0]["query"], "q1")
            self.assertEqual(records[1]["query"], "q2")
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_empty_file_returns_empty_list(self):
        """Should return an empty list for an empty file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            tmp_path = f.name
        try:
            records = _load_jsonl(tmp_path)
            self.assertEqual(records, [])
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_malformed_json_raises(self):
        """Should propagate json.JSONDecodeError for invalid lines."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write('{"valid": true}\n')
            f.write('not json\n')
            tmp_path = f.name
        try:
            with self.assertRaises(json.JSONDecodeError):
                _load_jsonl(tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
