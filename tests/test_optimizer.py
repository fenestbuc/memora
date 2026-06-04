"""TDD tests for the autonomous prompt optimization loop.

Tests cover:
- run_optimization_loop is a no-op when the score meets the threshold.
- run_optimization_loop spawns opencode run when the score is low.
- run_optimization_loop gracefully handles a missing prompts file.
- run_optimization_loop increments failure count on opencode/eval failure.
- run_optimization_loop escalates to Kanban after 3 consecutive failures.
- run_optimization_loop resets failure count to 0 on success.
- run_optimization_loop uses shell=True and echo prefix for opencode.
- run_optimization_loop reverts prompts and increments failure on AST compile error.

Run with: pytest tests/test_optimizer.py -v
"""

from __future__ import annotations

import json
import py_compile
import unittest
from pathlib import Path
from unittest.mock import Mock, mock_open, patch

from memora.optimizer import run_optimization_loop


class TestOptimizer(unittest.TestCase):
    """Unit tests for run_optimization_loop."""

    def test_no_op_when_score_equals_threshold(self):
        """Should not spawn opencode when current_score == 0.95."""
        with patch("memora.optimizer.subprocess.run") as mock_run:
            with patch("memora.optimizer._save_failure_count") as mock_save:
                run_optimization_loop(0.95)
                mock_run.assert_not_called()
                mock_save.assert_called_once_with(0)

    def test_no_op_when_score_above_threshold(self):
        """Should not spawn opencode when current_score > 0.95."""
        with patch("memora.optimizer.subprocess.run") as mock_run:
            with patch("memora.optimizer._save_failure_count") as mock_save:
                run_optimization_loop(0.97)
                mock_run.assert_not_called()
                mock_save.assert_called_once_with(0)

    def test_spawns_opencode_when_score_low(self):
        """Should spawn opencode run when current_score < 0.95."""
        with patch("memora.optimizer.subprocess.run") as mock_run:
            with patch("memora.optimizer._load_failure_count", return_value=0):
                with patch("memora.optimizer._save_failure_count") as mock_save:
                    mock_run.return_value.returncode = 0
                    run_optimization_loop(0.85)

                    mock_run.assert_called()
                    # First call should be the opencode run command
                    args, kwargs = mock_run.call_args_list[0]
                    cmd = args[0]
                    self.assertTrue(kwargs.get("shell"))
                    self.assertIn("echo", cmd)
                    self.assertIn("opencode", cmd)
                    self.assertIn("run", cmd)
                    self.assertIn("0.85", cmd)
                    self.assertIn("src/memora/prompts.py", cmd)
                    mock_save.assert_called_once_with(0)

    def test_no_op_when_prompts_file_missing(self):
        """Should not attempt optimization if prompts.py is absent."""
        with patch.object(Path, "exists", return_value=False):
            with patch("memora.optimizer.subprocess.run") as mock_run:
                run_optimization_loop(0.85)
                mock_run.assert_not_called()

    def test_reverts_on_failed_post_eval(self):
        """Should git checkout prompts.py when post-optimization eval fails."""
        with patch("memora.optimizer.subprocess.run") as mock_run:
            with patch("memora.optimizer._load_failure_count", return_value=0):
                with patch("memora.optimizer._save_failure_count") as mock_save:
                    # opencode run succeeds
                    mock_run.side_effect = [
                        Mock(returncode=0, stderr=""),  # opencode
                        Mock(returncode=1, stderr="fail"),  # eval fails
                        Mock(returncode=0, stderr=""),  # git checkout
                    ]
                    run_optimization_loop(0.85)

                    self.assertEqual(mock_run.call_count, 3)
                    # Third call should be git checkout
                    args, _kwargs = mock_run.call_args_list[2]
                    self.assertEqual(args[0][:2], ["git", "checkout"])
                    # Failure count should be incremented and saved
                    mock_save.assert_called_once_with(1)

    def test_uses_shell_true_and_echo_prefix(self):
        """The opencode subprocess must use shell=True and echo prefix."""
        with patch("memora.optimizer.subprocess.run") as mock_run:
            with patch("memora.optimizer._load_failure_count", return_value=0):
                with patch("memora.optimizer._save_failure_count"):
                    mock_run.return_value.returncode = 0
                    run_optimization_loop(0.85)

                    args, kwargs = mock_run.call_args_list[0]
                    self.assertTrue(
                        kwargs.get("shell"),
                        "subprocess.run must use shell=True",
                    )
                    self.assertTrue(
                        args[0].startswith('echo "" | '),
                        "command must start with echo prefix",
                    )

    def test_increments_failure_count_on_opencode_failure(self):
        """On opencode failure, failure count should be loaded, incremented, saved."""
        with patch("memora.optimizer.subprocess.run") as mock_run:
            with patch("memora.optimizer._load_failure_count", return_value=2):
                with patch("memora.optimizer._save_failure_count") as mock_save:
                    mock_run.return_value.returncode = 1
                    run_optimization_loop(0.85)

                    mock_save.assert_called_once_with(3)

    def test_reverts_on_ast_compile_failure(self):
        """Should git checkout prompts.py when py_compile raises PyCompileError."""
        with patch("memora.optimizer.subprocess.run") as mock_run:
            with patch("memora.optimizer._load_failure_count", return_value=0):
                with patch("memora.optimizer._save_failure_count") as mock_save:
                    with patch(
                        "memora.optimizer.py_compile.compile"
                    ) as mock_compile:
                        mock_compile.side_effect = py_compile.PyCompileError(
                            exc_type=SyntaxError,
                            exc_value=SyntaxError("invalid syntax"),
                            file="src/memora/prompts.py",
                            msg="test syntax error",
                        )
                        mock_run.return_value.returncode = 0
                        run_optimization_loop(0.85)

                        mock_compile.assert_called_once_with(
                            "src/memora/prompts.py", doraise=True
                        )
                        # subprocess.run should be called for opencode and git checkout
                        self.assertEqual(mock_run.call_count, 2)
                        args, _kwargs = mock_run.call_args_list[0]
                        self.assertIn("opencode", args[0])
                        args, _kwargs = mock_run.call_args_list[1]
                        self.assertEqual(args[0][:2], ["git", "checkout"])
                        mock_save.assert_called_once_with(1)

    def test_escalates_to_kanban_after_three_failures(self):
        """After 3 consecutive failures, a Kanban task is created for backend-eng."""
        with patch("memora.optimizer.subprocess.run") as mock_run:
            with patch("memora.optimizer._load_failure_count", return_value=2):
                with patch("memora.optimizer._save_failure_count"):
                    with patch("memora.optimizer.swarm_manager") as mock_swarm:
                        mock_swarm.kanban_create = Mock()
                        mock_run.return_value.returncode = 1
                        run_optimization_loop(0.85)

                        mock_swarm.kanban_create.assert_called_once()
                        _args, kwargs = mock_swarm.kanban_create.call_args
                        self.assertEqual(kwargs["assignee"], "backend-eng")
                        self.assertIn("optimizer", kwargs["tags"])

    def test_escalates_on_third_eval_failure(self):
        """Escalate should occur when eval failure pushes count to >= 3."""
        with patch("memora.optimizer.subprocess.run") as mock_run:
            with patch("memora.optimizer._load_failure_count", return_value=2):
                with patch("memora.optimizer._save_failure_count"):
                    with patch("memora.optimizer.swarm_manager") as mock_swarm:
                        mock_swarm.kanban_create = Mock()
                        mock_run.side_effect = [
                            Mock(returncode=0, stderr=""),  # opencode
                            Mock(returncode=1, stderr="fail"),  # eval fails
                            Mock(returncode=0, stderr=""),  # git checkout
                        ]
                        run_optimization_loop(0.85)

                        mock_swarm.kanban_create.assert_called_once()

    def test_resets_failure_count_on_success(self):
        """When opencode and eval both succeed, reset failure count to 0."""
        with patch("memora.optimizer.subprocess.run") as mock_run:
            with patch("memora.optimizer._load_failure_count", return_value=2):
                with patch("memora.optimizer._save_failure_count") as mock_save:
                    mock_run.side_effect = [
                        Mock(returncode=0, stderr=""),  # opencode
                        Mock(returncode=0, stderr=""),  # eval succeeds
                    ]
                    run_optimization_loop(0.85)

                    mock_save.assert_called_once_with(0)

    def test_loads_failure_count_from_state_file(self):
        """_load_failure_count should read from ~/.hermes/optimizer_state.json."""
        from memora.optimizer import _load_failure_count, STATE_PATH

        fake_data = json.dumps({"failure_count": 5})
        mock_path = Mock()
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = fake_data
        with patch("memora.optimizer.STATE_PATH", mock_path):
            count = _load_failure_count()
            self.assertEqual(count, 5)

    def test_saves_failure_count_to_state_file(self):
        """_save_failure_count should write to ~/.hermes/optimizer_state.json."""
        from memora.optimizer import _save_failure_count, STATE_PATH

        mock_path = Mock()
        with patch("memora.optimizer.STATE_PATH", mock_path):
            _save_failure_count(2)
            mock_path.parent.mkdir.assert_called_once_with(
                parents=True, exist_ok=True
            )
            written = mock_path.write_text.call_args[0][0]
            self.assertIn("2", written)
            self.assertIn("failure_count", written)


if __name__ == "__main__":
    unittest.main()
