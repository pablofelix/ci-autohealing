"""Tests for the Build Failure AI regression tester."""

import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from analyzers.build_regression import (
    EXPECTED_CATEGORIES,
    TASK_PATTERNS,
    BuildRegressionTester,
)


def _make_resolved(task_name, category, confidence, hours=24, can_auto_fix=False):
    now = datetime.now()
    return {
        'id': 1,
        'component_name': 'test-comp',
        'application': 'test-app',
        'failed_task_name': task_name,
        'error_type': 'TaskRunFailure',
        'error_message': 'test error',
        'status': 'Failed',
        'resolution_type': 'auto-detected',
        'build_start_time': now - timedelta(hours=hours),
        'resolved_at': now,
        'jira_key': None,
        'resolution_commit_sha': 'abc123',
        'analysis_id': 1,
        'failure_category': category,
        'confidence_score': confidence,
        'root_cause': 'test root cause',
        'recommended_fix': 'test fix',
        'recommended_files': ['Dockerfile'],
        'can_auto_fix': can_auto_fix,
        'human_verdict': None,
        'actual_root_cause': None,
        'model_used': 'test-model',
    }


class TestBuildRegressionTester(unittest.TestCase):

    def setUp(self):
        self.config = MagicMock()
        self.db = MagicMock()
        self.ai_repo = MagicMock()
        self.tester = BuildRegressionTester(self.config, db=self.db, ai_repo=self.ai_repo)

    def test_infer_task_group_known(self):
        self.assertEqual(self.tester._infer_task_group('buildah'), 'buildah')
        self.assertEqual(self.tester._infer_task_group('build-container'), 'buildah')
        self.assertEqual(self.tester._infer_task_group('git-clone'), 'git_clone')
        self.assertEqual(self.tester._infer_task_group('clair-scan'), 'clair_scan')
        self.assertEqual(self.tester._infer_task_group('fips-check'), 'fips_check')
        self.assertEqual(self.tester._infer_task_group('prefetch-dependencies'), 'prefetch')

    def test_infer_task_group_unknown(self):
        self.assertEqual(self.tester._infer_task_group('random-task'), 'other')
        self.assertEqual(self.tester._infer_task_group(None), 'other')
        self.assertEqual(self.tester._infer_task_group(''), 'other')

    def test_evaluate_accuracy_correct(self):
        resolved = [_make_resolved('buildah', 'build_error', 0.85)]
        evals = self.tester.evaluate_accuracy(resolved)
        self.assertEqual(len(evals), 1)
        self.assertTrue(evals[0]['category_correct'])
        self.assertEqual(evals[0]['task_group'], 'buildah')
        self.assertEqual(evals[0]['expected_category'], 'build_error')

    def test_evaluate_accuracy_incorrect(self):
        resolved = [_make_resolved('buildah', 'infrastructure', 0.60)]
        evals = self.tester.evaluate_accuracy(resolved)
        self.assertFalse(evals[0]['category_correct'])

    def test_evaluate_quick_fix(self):
        resolved = [_make_resolved('buildah', 'build_error', 0.85, hours=2, can_auto_fix=True)]
        evals = self.tester.evaluate_accuracy(resolved)
        self.assertTrue(evals[0]['was_quick_fix'])
        self.assertTrue(evals[0]['auto_fix_correct'])

    def test_evaluate_slow_fix(self):
        resolved = [_make_resolved('buildah', 'build_error', 0.85, hours=100, can_auto_fix=True)]
        evals = self.tester.evaluate_accuracy(resolved)
        self.assertFalse(evals[0]['was_quick_fix'])
        self.assertFalse(evals[0]['auto_fix_correct'])

    def test_compute_metrics_empty(self):
        metrics = self.tester.compute_metrics([], [])
        self.assertEqual(metrics.total_resolved, 0)
        self.assertEqual(metrics.accuracy, 0.0)

    def test_compute_metrics_with_data(self):
        resolved = [
            _make_resolved('buildah', 'build_error', 0.85),
            _make_resolved('git-clone', 'git_sync_issue', 0.90),
            _make_resolved('clair-scan', 'infrastructure', 0.60),
        ]
        evals = self.tester.evaluate_accuracy(resolved)
        gaps = [{'task_name': 'buildah', 'total_resolved': 10, 'with_ai': 5, 'coverage_pct': 50.0}]
        metrics = self.tester.compute_metrics(evals, gaps)
        self.assertEqual(metrics.accuracy, 0.67)
        self.assertIn('build_error', metrics.by_category)
        self.assertIn('git_sync_issue', metrics.by_category)

    def test_run_no_data(self):
        self.ai_repo.get_resolved_builds_with_analysis.return_value = []
        result = self.tester.run()
        self.assertFalse(result['analyzed'])

    def test_expected_categories_cover_all_patterns(self):
        for group in TASK_PATTERNS:
            self.assertIn(group, EXPECTED_CATEGORIES,
                          '{} pattern has no expected category'.format(group))


class TestBuildRegressionSuggestions(unittest.TestCase):

    def setUp(self):
        self.config = MagicMock()
        self.db = MagicMock()
        self.ai_repo = MagicMock()
        self.tester = BuildRegressionTester(self.config, db=self.db, ai_repo=self.ai_repo)

    def test_suggests_low_confidence(self):
        from analyzers.models import CategoryMetrics
        by_cat = {'build_error': CategoryMetrics(total=5, correct=5, avg_confidence=0.55)}
        improvements = self.tester._suggest_improvements([], [], by_cat)
        self.assertTrue(any('Low confidence' in i for i in improvements))

    def test_suggests_catch_all(self):
        from analyzers.models import CategoryMetrics
        by_cat = {
            'config_error': CategoryMetrics(total=4),
            'infrastructure': CategoryMetrics(total=3),
        }
        improvements = self.tester._suggest_improvements([], [], by_cat)
        self.assertTrue(any('catch-all' in i for i in improvements))


if __name__ == '__main__':
    unittest.main()
