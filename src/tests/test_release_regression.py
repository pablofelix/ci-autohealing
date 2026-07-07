"""Tests for the Release Failure AI regression tester."""

import unittest
from datetime import datetime
from unittest.mock import MagicMock

from analyzers.release_regression import (
    ACTIONABLE_CATEGORIES,
    RELEASE_CATEGORIES,
    TRANSIENT_CATEGORIES,
    ReleaseRegressionTester,
)


def _make_analysis(category, confidence, owner_team=None, affected_images=None,
                   fix_action_type=None):
    analysis_json = [{'input': {}}]
    if owner_team:
        analysis_json[0]['input']['owner_team'] = owner_team
    if affected_images:
        analysis_json[0]['input']['affected_images'] = affected_images
    if fix_action_type:
        analysis_json[0]['input']['fix_action_type'] = fix_action_type
    return {
        'id': 1,
        'release_name': 'test-release',
        'failure_category': category,
        'confidence_score': confidence,
        'root_cause': 'test root cause',
        'recommended_fix': 'test fix',
        'recommended_files': ['mapping.yaml'],
        'can_auto_fix': False,
        'human_verdict': None,
        'actual_root_cause': None,
        'model_used': 'test-model',
        'analyzed_at': datetime.now(),
        'analysis_json': analysis_json,
    }


class TestReleaseRegressionTester(unittest.TestCase):

    def setUp(self):
        self.config = MagicMock()
        self.db = MagicMock()
        self.ai_repo = MagicMock()
        self.tester = ReleaseRegressionTester(self.config, db=self.db, ai_repo=self.ai_repo)

    def test_evaluate_valid_category(self):
        analyses = [_make_analysis('unmapped_image', 0.87)]
        evals = self.tester.evaluate_quality(analyses)
        self.assertTrue(evals[0]['valid_category'])
        self.assertTrue(evals[0]['is_actionable'])

    def test_evaluate_invalid_category(self):
        analyses = [_make_analysis('not_a_category', 0.50)]
        evals = self.tester.evaluate_quality(analyses)
        self.assertFalse(evals[0]['valid_category'])

    def test_completeness_full(self):
        analyses = [_make_analysis(
            'unmapped_image', 0.90,
            owner_team='RelEng',
            affected_images=['image:latest'],
            fix_action_type='file_change',
        )]
        evals = self.tester.evaluate_quality(analyses)
        self.assertEqual(evals[0]['completeness_score'], 1.0)

    def test_completeness_partial(self):
        analyses = [_make_analysis('unmapped_image', 0.90)]
        evals = self.tester.evaluate_quality(analyses)
        self.assertLess(evals[0]['completeness_score'], 1.0)
        self.assertGreater(evals[0]['completeness_score'], 0.0)

    def test_transient_classification(self):
        analyses = [_make_analysis('infrastructure', 0.70)]
        evals = self.tester.evaluate_quality(analyses)
        self.assertTrue(evals[0]['is_transient'])
        self.assertFalse(evals[0]['is_actionable'])

    def test_compute_metrics_empty(self):
        metrics = self.tester.compute_metrics([])
        self.assertEqual(metrics.total_resolved, 0)

    def test_compute_metrics_with_data(self):
        analyses = [
            _make_analysis('unmapped_image', 0.87, owner_team='RelEng'),
            _make_analysis('cross_product_dependency', 0.90,
                           affected_images=['img:1'], fix_action_type='multi_step'),
        ]
        evals = self.tester.evaluate_quality(analyses)
        metrics = self.tester.compute_metrics(evals)
        self.assertEqual(metrics.accuracy, 1.0)
        self.assertIn('unmapped_image', metrics.by_category)
        self.assertIn('cross_product_dependency', metrics.by_category)

    def test_run_no_data(self):
        self.ai_repo.get_release_analyses.return_value = []
        result = self.tester.run()
        self.assertFalse(result['analyzed'])

    def test_analysis_json_dict_format(self):
        a = _make_analysis('unmapped_image', 0.90)
        a['analysis_json'] = {'affected_images': ['img:1'], 'owner_team': 'RelEng'}
        evals = self.tester.evaluate_quality([a])
        self.assertTrue(evals[0]['has_affected_images'])
        self.assertTrue(evals[0]['has_owner_team'])

    def test_analysis_json_none(self):
        a = _make_analysis('unmapped_image', 0.90)
        a['analysis_json'] = None
        evals = self.tester.evaluate_quality([a])
        self.assertFalse(evals[0]['has_affected_images'])

    def test_category_sets_consistent(self):
        self.assertTrue(TRANSIENT_CATEGORIES.issubset(RELEASE_CATEGORIES))
        self.assertTrue(ACTIONABLE_CATEGORIES.issubset(RELEASE_CATEGORIES))
        self.assertEqual(len(TRANSIENT_CATEGORIES & ACTIONABLE_CATEGORIES), 0)


if __name__ == '__main__':
    unittest.main()
