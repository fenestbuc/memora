"""Tests for Memora evaluation framework (src/memora/evaluations.py).

Uses unittest.mock for all external dependencies (Vertex AI, urllib, etc.).
Run with: pytest tests/test_evaluations.py -v
"""

from __future__ import annotations

import json
import os
import unittest
from datetime import datetime, timezone
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from memora.evaluations import (
    CeoDigestEvaluator,
    CeoDigestScore,
    EvaluationReport,
    RAGEvaluator,
    RAGMetrics,
    SwarmTriggerEvaluator,
    SwarmTriggerScore,
    _compute_mrr,
    _compute_ndcg,
    _heuristic_judge,
    _parse_judge_output,
    default_llm_judge,
    run_full_evaluation,
)
from memora.plugin import MemoraProvider


# ---------------------------------------------------------------------------
# Data model tests
# ---------------------------------------------------------------------------


class TestCeoDigestScore(unittest.TestCase):
    def test_to_dict_rounding(self):
        score = CeoDigestScore(
            completeness=0.1234567,
            accuracy=0.9876543,
            conciseness=0.5,
            actionability=0.7777777,
            overall=0.6666666,
        )
        d = score.to_dict()
        self.assertEqual(d["completeness"], 0.123)
        self.assertEqual(d["accuracy"], 0.988)
        self.assertEqual(d["overall"], 0.667)


class TestSwarmTriggerScore(unittest.TestCase):
    def test_to_dict(self):
        score = SwarmTriggerScore(
            source="rag",
            content_preview="Pivot to B2B lending",
            expected_role="analyst",
            actual_role="analyst",
            correct=True,
        )
        d = score.to_dict()
        self.assertEqual(d["source"], "rag")
        self.assertTrue(d["correct"])


class TestRAGMetrics(unittest.TestCase):
    def test_to_dict_rounding(self):
        metrics = RAGMetrics(
            mrr=0.12345678,
            hit_rate_at_k=0.99,
            precision_at_k=0.333333,
            recall_at_k=0.666666,
            ndcg_at_k=0.888888,
            latency_ms_avg=42.555,
            total_queries=10,
            k=5,
        )
        d = metrics.to_dict()
        self.assertEqual(d["mrr"], 0.1235)
        self.assertEqual(d["precision_at_k"], 0.3333)
        self.assertEqual(d["latency_ms_avg"], 42.55)  # banker's rounding
        self.assertEqual(d["total_queries"], 10)


class TestEvaluationReport(unittest.TestCase):
    def test_swarm_accuracy_zero_when_empty(self):
        report = EvaluationReport(started_at="2024-01-01T00:00:00Z")
        self.assertEqual(report.swarm_accuracy, 0.0)

    def test_swarm_accuracy_computed(self):
        report = EvaluationReport(
            started_at="2024-01-01T00:00:00Z",
            swarm_triggers=[
                SwarmTriggerScore("a", "x", "analyst", "analyst", True),
                SwarmTriggerScore("b", "y", "reviewer", "analyst", False),
                SwarmTriggerScore("c", "z", "reviewer", "reviewer", True),
            ],
        )
        self.assertAlmostEqual(report.swarm_accuracy, 2 / 3)

    def test_to_dict_with_none_fields(self):
        report = EvaluationReport(started_at="2024-01-01T00:00:00Z")
        d = report.to_dict()
        self.assertIsNone(d["ceo_digest"])
        self.assertIsNone(d["rag_metrics"])
        self.assertIsNone(d["swarm_accuracy"])

    def test_summary_includes_all_sections(self):
        report = EvaluationReport(
            started_at="2024-01-01T00:00:00Z",
            finished_at="2024-01-01T00:01:00Z",
            ceo_digest=CeoDigestScore(0.8, 0.9, 0.7, 0.6, 0.75),
            swarm_triggers=[
                SwarmTriggerScore("src", "content", "analyst", "analyst", True),
            ],
            rag_metrics=RAGMetrics(1.0, 1.0, 1.0, 1.0, 1.0, 10.0, 1, 10),
            errors=["Something went wrong"],
        )
        summary = report.summary()
        self.assertIn("CEO Digest Scores", summary)
        self.assertIn("Swarm Trigger Accuracy", summary)
        self.assertIn("RAG Metrics", summary)
        self.assertIn("Errors", summary)
        self.assertIn("Something went wrong", summary)

    def test_summary_without_optional_sections(self):
        report = EvaluationReport(
            started_at="2024-01-01T00:00:00Z",
            finished_at="2024-01-01T00:01:00Z",
        )
        summary = report.summary()
        self.assertNotIn("CEO Digest Scores", summary)
        self.assertNotIn("Swarm Trigger Accuracy", summary)


# ---------------------------------------------------------------------------
# _parse_judge_output tests
# ---------------------------------------------------------------------------


class TestParseJudgeOutput(unittest.TestCase):
    def test_json_parsing(self):
        text = json.dumps(
            {"completeness": 0.8, "accuracy": 0.9, "conciseness": 0.7, "actionability": 0.6}
        )
        scores = _parse_judge_output(text)
        self.assertEqual(scores["completeness"], 0.8)
        self.assertEqual(scores["accuracy"], 0.9)

    def test_json_missing_keys_defaults_to_zero(self):
        text = json.dumps({"completeness": 0.5})
        scores = _parse_judge_output(text)
        self.assertEqual(scores["completeness"], 0.5)
        self.assertEqual(scores["accuracy"], 0.0)

    def test_regex_fallback(self):
        text = "Completeness: 0.75, Accuracy: 0.85\nConciseness: 0.65\nActionability 0.55"
        scores = _parse_judge_output(text)
        self.assertEqual(scores["completeness"], 0.75)
        self.assertEqual(scores["accuracy"], 0.85)
        self.assertEqual(scores["conciseness"], 0.65)
        self.assertEqual(scores["actionability"], 0.55)

    def test_regex_fallback_defaults_to_half(self):
        scores = _parse_judge_output("no scores here")
        self.assertEqual(scores["completeness"], 0.5)
        self.assertEqual(scores["accuracy"], 0.5)


# ---------------------------------------------------------------------------
# _heuristic_judge tests
# ---------------------------------------------------------------------------


class TestHeuristicJudge(unittest.TestCase):
    def test_completeness_all_pr_numbers_mentioned(self):
        prs = [{"number": 1, "url": "http://x"}, {"number": 2, "url": "http://y"}]
        digest = "PR #1 and PR #2 are open."
        score = _heuristic_judge(digest, "", "ctx", prs)
        self.assertEqual(score.completeness, 1.0)

    def test_completeness_partial_mention(self):
        prs = [{"number": 1, "url": "http://x"}, {"number": 2, "url": "http://y"}]
        digest = "PR #1 is open."
        score = _heuristic_judge(digest, "", "ctx", prs)
        self.assertEqual(score.completeness, 0.5)

    def test_accuracy_url_presence(self):
        prs = [{"number": 1, "url": "http://example.com/1"}]
        digest = "See http://example.com/1 for details."
        score = _heuristic_judge(digest, "", "ctx", prs)
        self.assertEqual(score.accuracy, 1.0)

    def test_conciseness_ideal_length(self):
        digest = "x" * 500
        score = _heuristic_judge(digest, "", "ctx", [])
        self.assertEqual(score.conciseness, 1.0)

    def test_conciseness_too_short(self):
        digest = "x" * 100
        score = _heuristic_judge(digest, "", "ctx", [])
        self.assertEqual(score.conciseness, 100 / 200)

    def test_conciseness_too_long(self):
        digest = "x" * 3000
        score = _heuristic_judge(digest, "", "ctx", [])
        expected = max(0.0, 1.0 - (3000 - 800) / 2000)
        self.assertEqual(score.conciseness, expected)

    def test_actionability_detects_verbs(self):
        digest = "Please review and approve the changes."
        score = _heuristic_judge(digest, "", "ctx", [])
        self.assertGreater(score.actionability, 0.0)

    def test_actionability_capped_at_one(self):
        digest = "review approve merge discuss schedule check follow-up action review"
        score = _heuristic_judge(digest, "", "ctx", [])
        self.assertEqual(score.actionability, 1.0)

    def test_overall_weighted_sum(self):
        digest = "PR #1 is at http://x. Please review." + "x" * 400
        prs = [{"number": 1, "url": "http://x"}]
        score = _heuristic_judge(digest, "", "ctx", prs)
        expected = (
            score.completeness * 0.3
            + score.accuracy * 0.3
            + score.conciseness * 0.2
            + score.actionability * 0.2
        )
        self.assertAlmostEqual(score.overall, expected)


# ---------------------------------------------------------------------------
# CeoDigestEvaluator tests
# ---------------------------------------------------------------------------


class TestCeoDigestEvaluator(unittest.TestCase):
    def test_uses_custom_judge(self):
        mock_judge = MagicMock(return_value=CeoDigestScore(1.0, 1.0, 1.0, 1.0, 1.0))
        evaluator = CeoDigestEvaluator(judge=mock_judge)
        prs = [{"number": 1}]
        result = evaluator.evaluate("digest", prs)
        mock_judge.assert_called_once()
        self.assertEqual(result.overall, 1.0)

    def test_passes_correct_args_to_judge(self):
        mock_judge = MagicMock(return_value=CeoDigestScore(0, 0, 0, 0, 0))
        evaluator = CeoDigestEvaluator(judge=mock_judge, project_context="TestCtx")
        prs = [{"number": 42}]
        evaluator.evaluate("my digest", prs)
        _digest, gt_json, ctx, open_prs = mock_judge.call_args[0]
        self.assertEqual(_digest, "my digest")
        self.assertEqual(ctx, "TestCtx")
        self.assertEqual(open_prs, prs)
        self.assertIn("42", gt_json)


# ---------------------------------------------------------------------------
# SwarmTriggerEvaluator tests
# ---------------------------------------------------------------------------


class TestSwarmTriggerEvaluator(unittest.TestCase):
    def test_default_ground_truth_all_cases(self):
        evaluator = SwarmTriggerEvaluator()
        scores = evaluator.evaluate(trigger_fn=None)
        self.assertEqual(len(scores), 5)
        # All should be correct under the default heuristic mapping
        self.assertTrue(all(s.correct for s in scores))

    def test_custom_ground_truth(self):
        gt = [{"source": "x", "category": "memory", "scope": "personal", "content": "c", "expected_role": "reviewer"}]
        evaluator = SwarmTriggerEvaluator(ground_truth=gt)
        scores = evaluator.evaluate(trigger_fn=None)
        self.assertEqual(len(scores), 1)
        self.assertTrue(scores[0].correct)

    def test_with_mock_trigger_fn_success(self):
        evaluator = SwarmTriggerEvaluator()

        def mock_trigger(**kwargs):
            return {"title": "[analyst] Do something", "body": "..."}

        scores = evaluator.evaluate(trigger_fn=mock_trigger)
        for s in scores:
            if s.expected_role == "analyst":
                self.assertTrue(s.correct)
            else:
                # reviewer cases won't match because mock returns analyst in title
                self.assertFalse(s.correct)

    def test_with_mock_trigger_fn_error_key(self):
        evaluator = SwarmTriggerEvaluator()

        def mock_trigger(**kwargs):
            return {"error": "kanban failed"}

        scores = evaluator.evaluate(trigger_fn=mock_trigger)
        # When error is present, actual defaults to expected, so all correct
        self.assertTrue(all(s.correct for s in scores))

    def test_with_trigger_fn_exception(self):
        evaluator = SwarmTriggerEvaluator()

        def bad_trigger(**kwargs):
            raise RuntimeError("boom")

        scores = evaluator.evaluate(trigger_fn=bad_trigger)
        for s in scores:
            self.assertEqual(s.actual_role, "unknown")
            self.assertFalse(s.correct)

    def test_content_preview_truncated(self):
        gt = [{"source": "s", "category": "c", "scope": "p", "content": "x" * 100, "expected_role": "analyst"}]
        evaluator = SwarmTriggerEvaluator(ground_truth=gt)
        scores = evaluator.evaluate(trigger_fn=None)
        self.assertEqual(len(scores[0].content_preview), 60)


# ---------------------------------------------------------------------------
# RAG metric computation tests
# ---------------------------------------------------------------------------


class TestComputeMrr(unittest.TestCase):
    def test_empty_list(self):
        self.assertEqual(_compute_mrr([]), 0.0)

    def test_single_relevant_at_one(self):
        self.assertEqual(_compute_mrr([1]), 1.0)

    def test_multiple_relevants(self):
        self.assertEqual(_compute_mrr([1, 2]), (1.0 + 0.5) / 2)


class TestComputeNdcg(unittest.TestCase):
    def test_empty_relevances(self):
        self.assertEqual(_compute_ndcg([], 10), 0.0)

    def test_perfect_relevances(self):
        rels = [1.0, 1.0, 1.0]
        self.assertEqual(_compute_ndcg(rels, 3), 1.0)

    def test_zero_relevances(self):
        rels = [0.0, 0.0, 0.0]
        self.assertEqual(_compute_ndcg(rels, 3), 0.0)


# ---------------------------------------------------------------------------
# RAGEvaluator tests
# ---------------------------------------------------------------------------


class TestRAGEvaluator(unittest.TestCase):
    def test_empty_golden_dataset_raises(self):
        evaluator = RAGEvaluator("http://test", "token")
        with self.assertRaises(ValueError):
            evaluator.evaluate([])

    @patch("memora.evaluations._ranked_results_for_query")
    def test_evaluate_computes_metrics(self, mock_ranked):
        # Query 1: 2 relevant, both retrieved in top 2
        # Query 2: 1 relevant, not retrieved
        mock_ranked.side_effect = [
            (["id1", "id2", "id3"], 12.0),
            (["id4", "id5"], 20.0),
        ]
        golden = [
            {"query": "q1", "relevant_ids": ["id1", "id2"]},
            {"query": "q2", "relevant_ids": ["id6"]},
        ]
        evaluator = RAGEvaluator("http://test/", "token", k=10)
        metrics = evaluator.evaluate(golden)

        self.assertEqual(metrics.total_queries, 2)
        self.assertEqual(metrics.hit_rate_at_k, 0.5)  # only q1 hit
        self.assertEqual(metrics.mrr, 0.5)  # (1.0 + 0) / 2
        self.assertAlmostEqual(metrics.precision_at_k, (2 / 3 + 0.0) / 2, places=4)
        self.assertAlmostEqual(metrics.recall_at_k, (2 / 2 + 0.0) / 2, places=4)
        self.assertEqual(metrics.latency_ms_avg, 16.0)

    @patch("memora.evaluations._ranked_results_for_query")
    def test_skips_queries_with_errors(self, mock_ranked):
        mock_ranked.side_effect = [
            (["id1"], 10.0),
            Exception("network down"),
        ]
        golden = [
            {"query": "q1", "relevant_ids": ["id1"]},
            {"query": "q2", "relevant_ids": ["id2"]},
        ]
        evaluator = RAGEvaluator("http://test/", "token", k=10)
        metrics = evaluator.evaluate(golden)

        self.assertEqual(metrics.total_queries, 2)
        # Only 1 query succeeded for metric computation
        self.assertEqual(metrics.hit_rate_at_k, 1.0)
        self.assertEqual(metrics.mrr, 1.0)

    @patch("memora.evaluations._ranked_results_for_query")
    def test_skips_empty_relevant_set(self, mock_ranked):
        mock_ranked.return_value = (["id1"], 5.0)
        golden = [
            {"query": "q1", "relevant_ids": []},
            {"query": "q2", "relevant_ids": ["id1"]},
        ]
        evaluator = RAGEvaluator("http://test/", "token", k=10)
        metrics = evaluator.evaluate(golden)
        # q1 skipped due to empty relevant_ids; only q2 counted for avg
        self.assertEqual(metrics.total_queries, 2)
        self.assertEqual(metrics.hit_rate_at_k, 1.0)


# ---------------------------------------------------------------------------
# default_llm_judge tests
# ---------------------------------------------------------------------------


class TestDefaultLLMJudge(unittest.TestCase):
    @patch.dict(os.environ, {}, clear=True)
    def test_fallback_when_no_project_env(self):
        score = default_llm_judge("digest", "[]", "ctx", [])
        self.assertIsInstance(score, CeoDigestScore)
        # Should use heuristic fallback
        self.assertGreaterEqual(score.completeness, 0.0)

    @patch("memora.evaluations._call_vertex_judge")
    @patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"})
    def test_uses_llm_when_project_set(self, mock_call):
        mock_call.return_value = json.dumps(
            {"completeness": 0.9, "accuracy": 0.8, "conciseness": 0.7, "actionability": 0.6}
        )
        score = default_llm_judge("digest", "[]", "ctx", [])
        mock_call.assert_called_once()
        self.assertEqual(score.completeness, 0.9)
        self.assertEqual(score.overall, 0.9 * 0.3 + 0.8 * 0.3 + 0.7 * 0.2 + 0.6 * 0.2)

    @patch("memora.evaluations._call_vertex_judge")
    @patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"})
    def test_fallback_on_llm_exception(self, mock_call):
        mock_call.side_effect = RuntimeError("Vertex AI unavailable")
        score = default_llm_judge("digest", "[]", "ctx", [])
        self.assertIsInstance(score, CeoDigestScore)
        # Should have fallen back to heuristic


# ---------------------------------------------------------------------------
# run_full_evaluation orchestrator tests
# ---------------------------------------------------------------------------


class TestRunFullEvaluation(unittest.TestCase):
    @patch("memora.evaluations.CeoDigestEvaluator")
    @patch("memora.evaluations.SwarmTriggerEvaluator")
    @patch("memora.evaluations.RAGEvaluator")
    def test_all_sections_populated(self, mock_rag_cls, mock_swarm_cls, mock_ceo_cls):
        mock_ceo_cls.return_value.evaluate.return_value = CeoDigestScore(1, 1, 1, 1, 1)
        mock_swarm_cls.return_value.evaluate.return_value = [
            SwarmTriggerScore("s", "c", "analyst", "analyst", True),
        ]
        mock_rag_cls.return_value.evaluate.return_value = RAGMetrics(
            1.0, 1.0, 1.0, 1.0, 1.0, 10.0, 1, 10
        )

        report = run_full_evaluation(
            base_url="http://test",
            token="tok",
            golden_dataset=[{"query": "q", "relevant_ids": ["id1"]}],
            ceo_digest_text="digest",
            open_prs=[{"number": 1}],
        )

        self.assertIsNotNone(report.ceo_digest)
        self.assertEqual(len(report.swarm_triggers), 1)
        self.assertIsNotNone(report.rag_metrics)
        self.assertEqual(report.golden_dataset_size, 1)
        self.assertTrue(report.finished_at)

    def test_minimal_run(self):
        report = run_full_evaluation()
        self.assertIsNone(report.ceo_digest)
        # swarm triggers always run with default ground truth
        self.assertGreater(len(report.swarm_triggers), 0)
        self.assertIsNone(report.rag_metrics)
        self.assertTrue(report.finished_at)

    @patch("memora.evaluations.CeoDigestEvaluator")
    def test_ceo_error_captured(self, mock_cls):
        mock_cls.return_value.evaluate.side_effect = RuntimeError("bad digest")
        report = run_full_evaluation(
            ceo_digest_text="digest",
            open_prs=[{"number": 1}],
        )
        self.assertIsNone(report.ceo_digest)
        self.assertTrue(any("CEO digest eval failed" in e for e in report.errors))

    @patch("memora.evaluations.SwarmTriggerEvaluator")
    def test_swarm_error_captured(self, mock_cls):
        mock_cls.return_value.evaluate.side_effect = RuntimeError("bad swarm")
        report = run_full_evaluation()
        self.assertTrue(any("Swarm trigger eval failed" in e for e in report.errors))

    @patch("memora.evaluations.RAGEvaluator")
    def test_rag_error_captured(self, mock_cls):
        mock_cls.return_value.evaluate.side_effect = RuntimeError("bad rag")
        report = run_full_evaluation(
            base_url="http://test",
            token="tok",
            golden_dataset=[{"query": "q", "relevant_ids": ["id1"]}],
        )
        self.assertIsNone(report.rag_metrics)
        self.assertTrue(any("RAG eval failed" in e for e in report.errors))

    def test_provider_url_token_resolution(self):
        provider = MagicMock()
        provider.worker_url = "http://provider"
        provider.worker_token = "provider_tok"

        with patch("memora.evaluations.RAGEvaluator") as mock_rag_cls:
            mock_rag_cls.return_value.evaluate.return_value = RAGMetrics(
                0, 0, 0, 0, 0, 0, 0, 10
            )
            run_full_evaluation(
                provider=provider,
                golden_dataset=[{"query": "q", "relevant_ids": ["id1"]}],
            )
            mock_rag_cls.assert_called_once_with("http://provider", "provider_tok", k=10)


# ---------------------------------------------------------------------------
# Legacy provider integration tests
# ---------------------------------------------------------------------------


class TestMemoraProviderEvaluations(unittest.TestCase):
    def setUp(self):
        self.provider = MemoraProvider()
        self.provider.worker_url = "https://memora.test"
        self.provider.worker_token = "test_token"

    @patch("urllib.request.urlopen")
    def test_add_eval_golden(self, mock_urlopen):
        """Test adding an eval golden dataset correctly formats the request."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"success": true, "id": "fact_123"}'
        mock_resp.status = 200
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        result = self.provider.add_eval_golden("What is NavDhan?", ["fact_abc123"])
        self.assertTrue(result.get("success"))

        # Verify the request payload
        call_args = mock_urlopen.call_args[0][0]
        self.assertEqual(call_args.full_url, "https://memora.test/facts")
        self.assertEqual(call_args.method, "POST")
        payload = json.loads(call_args.data.decode("utf-8"))

        self.assertEqual(payload["content"], "What is NavDhan?")
        self.assertEqual(payload["category"], "eval_golden")
        self.assertEqual(payload["source_session"], '["fact_abc123"]')

    @patch("urllib.request.urlopen")
    def test_evaluate(self, mock_urlopen):
        """Test the evaluate endpoint is called and parses results."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"mrr": 1.0, "hit_rate": 1.0, "total_evals": 1, "details": []}'
        mock_resp.status = 200
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        metrics = self.provider.evaluate()

        self.assertEqual(metrics["mrr"], 1.0)
        self.assertEqual(metrics["hit_rate"], 1.0)

        call_args = mock_urlopen.call_args[0][0]
        self.assertEqual(call_args.full_url, "https://memora.test/evaluate")
        self.assertEqual(call_args.method, "POST")


if __name__ == "__main__":
    unittest.main()
