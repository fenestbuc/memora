"""TDD tests for the safe prompt-suggestion engine.

Tests cover:
- run_optimization_loop is a no-op when the score meets the threshold.
- run_optimization_loop writes a suggestion file when the score is low.
- run_optimization_loop gracefully handles a missing prompts file.
- run_optimization_loop creates a Kanban task for CEO review.
- run_optimization_loop never calls subprocess.run (no shell injection).

Run with: pytest tests/test_optimizer.py -v
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from memora.optimizer import run_optimization_loop, SUGGESTIONS_DIR


class TestOptimizer(unittest.TestCase):
    """Unit tests for run_optimization_loop."""

    def test_no_op_when_score_equals_threshold(self):
        """Should not write suggestions when current_score == 0.95."""
        with patch("memora.optimizer._write_suggestion") as mock_write:
            with patch("memora.optimizer.swarm_manager.trigger") as mock_trigger:
                run_optimization_loop(0.95)
                mock_write.assert_not_called()
                mock_trigger.assert_not_called()

    def test_no_op_when_score_above_threshold(self):
        """Should not write suggestions when current_score > 0.95."""
        with patch("memora.optimizer._write_suggestion") as mock_write:
            with patch("memora.optimizer.swarm_manager.trigger") as mock_trigger:
                run_optimization_loop(0.97)
                mock_write.assert_not_called()
                mock_trigger.assert_not_called()

    def test_writes_suggestion_when_score_low(self):
        """Should write a suggestion markdown file when current_score < 0.95."""
        fake_path = Path("/fake/suggestion.md")
        with patch("memora.optimizer._write_suggestion", return_value=fake_path) as mock_write:
            with patch("memora.optimizer.swarm_manager.trigger") as mock_trigger:
                run_optimization_loop(0.85)

                mock_write.assert_called_once()
                score, golden_path, prompts_path = mock_write.call_args[0]
                self.assertEqual(score, 0.85)
                self.assertEqual(str(golden_path), "data/eval_golden.jsonl")
                self.assertEqual(str(prompts_path), "src/memora/prompts.py")
                mock_trigger.assert_called_once()

    def test_creates_kanban_task_for_review(self):
        """Should create a Kanban task for the CEO when score is low."""
        fake_path = Path("/fake/suggestion.md")
        with patch("memora.optimizer._write_suggestion", return_value=fake_path):
            with patch("memora.optimizer.swarm_manager.trigger") as mock_trigger:
                run_optimization_loop(0.85)

                mock_trigger.assert_called_once()
                kwargs = mock_trigger.call_args[1]
                self.assertEqual(kwargs["source"], "optimizer")
                self.assertIn("85.00%", kwargs["content"])
                self.assertEqual(kwargs["category"], "optimizer")
                self.assertEqual(kwargs["scope"], "company")
                self.assertEqual(kwargs["agent_role"], "reviewer")

    def test_no_subprocess_called(self):
        """The optimizer must never invoke shell commands (safety guard)."""
        with patch("memora.optimizer._write_suggestion", return_value=Path("/fake/suggestion.md")):
            with patch("memora.optimizer.swarm_manager.trigger"):
                # Ensure no subprocess module is used at all
                import memora.optimizer as opt_mod
                self.assertFalse(hasattr(opt_mod, "subprocess"))
                run_optimization_loop(0.85)

    def test_suggestion_file_content(self):
        """The written suggestion should contain expected sections."""
        from memora.optimizer import _write_suggestion
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            golden_path = Path(tmpdir) / "golden.jsonl"
            golden_path.write_text('{"query": "test", "relevant_ids": ["x"]}\n')
            prompts_path = Path(tmpdir) / "prompts.py"
            prompts_path.write_text("KANBAN_ROUTING_PROMPT = 'test'")

            result_path = _write_suggestion(0.85, golden_path, prompts_path)
            self.assertTrue(result_path.exists())
            text = result_path.read_text()
            self.assertIn("Swarm Routing accuracy", text)
            self.assertIn("Current Prompt", text)
            self.assertIn("Recommended Action", text)
            self.assertIn("does **not** modify any source files", text)
            self.assertIn("test", text)
            # Cleanup
            result_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
