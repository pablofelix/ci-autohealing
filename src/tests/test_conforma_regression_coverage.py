"""Comprehensive tests for ConformaRegressionTester.

Covers _infer_rule_groups, evaluate_accuracy, compute_metrics,
_suggest_improvements, identify_coverage_gaps, run, and
get_resolved_with_analysis.
"""

import os
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock, Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

with patch.dict('sys.modules', {
    'logger': MagicMock(setup_logger=MagicMock(return_value=MagicMock())),
    'repositories.ai_analysis_repository': MagicMock(),
    'repositories.connection': MagicMock(),
}):
    from analyzers.conforma_regression import (
        EXPECTED_MULTI,
        FAST_RESOLUTION_HOURS,
        RULE_PATTERNS,
        TRANSIENT_RULES,
        ConformaRegressionTester,
    )

from analyzers.models import CategoryMetrics, RegressionMetrics

# ── Helpers ──────────────────────────────────────────────────────────────


def _make_config():
    config = MagicMock()
    config.db = MagicMock()
    return config


def _make_tester(**kwargs):
    config = kwargs.pop('config', _make_config())
    db = kwargs.pop('db', MagicMock())
    ai_repo = kwargs.pop('ai_repo', MagicMock())
    return ConformaRegressionTester(config, db=db, ai_repo=ai_repo)


def _make_violation(**overrides):
    now = datetime.utcnow()
    base = {
        'component_name': 'odh-dashboard',
        'violation_summary': 'hermetic_task check failed',
        'failure_category': 'policy_hermetic_build',
        'confidence_score': 0.85,
        'can_auto_fix': False,
        'first_detected_at': now - timedelta(hours=72),
        'resolved_at': now,
        'human_verdict': None,
        'jira_key': 'RHOAIENG-1234',
    }
    base.update(overrides)
    return base


# ── _infer_rule_groups ───────────────────────────────────────────────────


class TestInferRuleGroups:

    def test_empty_string_returns_other(self):
        tester = _make_tester()
        result = tester._infer_rule_groups('')
        assert result == [('other', 0)]

    def test_none_returns_other(self):
        tester = _make_tester()
        result = tester._infer_rule_groups(None)
        assert result == [('other', 0)]

    def test_single_match(self):
        tester = _make_tester()
        result = tester._infer_rule_groups('hermetic_task check failed')
        assert len(result) >= 1
        assert result[0][0] == 'hermetic_task'
        assert result[0][1] >= 1

    def test_multiple_matches(self):
        tester = _make_tester()
        summary = 'fips check failed, trusted_task not found, slsa provenance missing'
        result = tester._infer_rule_groups(summary)
        groups = [g for g, _ in result]
        assert 'fips' in groups
        assert 'trusted_task' in groups
        assert 'slsa' in groups

    def test_no_pattern_matches_returns_other(self):
        tester = _make_tester()
        result = tester._infer_rule_groups('completely unrelated text about cats')
        assert result == [('other', 0)]

    def test_threshold_filtering(self):
        """Groups with count below max(1, top_count // 5) are excluded."""
        tester = _make_tester()
        # Build a summary where 'fips' appears many times and 'snyk' once.
        # top_count would be high for fips, threshold = max(1, top//5).
        # If fips appears 10 times, threshold = max(1, 2) = 2, so snyk (1) is filtered out.
        summary = ' '.join(['fips'] * 10 + ['snyk'])
        result = tester._infer_rule_groups(summary)
        groups = [g for g, _ in result]
        assert 'fips' in groups
        assert 'snyk' not in groups


# ── evaluate_accuracy ────────────────────────────────────────────────────


class TestEvaluateAccuracy:

    def test_multi_acceptable_category(self):
        """When a violation matches a group in MULTI_ACCEPTABLE, any acceptable cat is correct."""
        tester = _make_tester()
        v = _make_violation(
            violation_summary='base_image_registries check failed',
            failure_category='policy_hermetic_build',
        )
        evals = tester.evaluate_accuracy([v])
        assert len(evals) == 1
        assert evals[0]['category_correct'] is True

    def test_other_group_always_correct(self):
        """When primary group is 'other', category_correct is always True."""
        tester = _make_tester()
        v = _make_violation(
            violation_summary='something completely unknown',
            failure_category='config_error',
        )
        evals = tester.evaluate_accuracy([v])
        assert evals[0]['category_correct'] is True

    def test_no_timestamps_hours_to_resolve_none(self):
        tester = _make_tester()
        v = _make_violation(first_detected_at=None, resolved_at=None)
        evals = tester.evaluate_accuracy([v])
        assert evals[0]['hours_to_resolve'] is None
        assert evals[0]['was_quick_fix'] is False

    def test_correct_auto_fix_transient(self):
        """can_auto_fix=True matches expected for transient/rebuild categories."""
        tester = _make_tester()
        now = datetime.utcnow()
        v = _make_violation(
            violation_summary='trusted_task.trusted check failed',
            failure_category='policy_untrusted_image',
            first_detected_at=now - timedelta(hours=24),
            resolved_at=now,
            can_auto_fix=True,
        )
        evals = tester.evaluate_accuracy([v])
        assert evals[0]['expected_auto_fix'] is True
        assert evals[0]['auto_fix_correct'] is True

    def test_wrong_auto_fix_not_auto_fixable(self):
        """can_auto_fix=True but category is not auto-fixable -> incorrect."""
        tester = _make_tester()
        now = datetime.utcnow()
        v = _make_violation(
            violation_summary='hermetic_task.hermetic check failed',
            failure_category='policy_hermetic_build',
            first_detected_at=now - timedelta(hours=12),
            resolved_at=now,
            can_auto_fix=True,
        )
        evals = tester.evaluate_accuracy([v])
        assert evals[0]['expected_auto_fix'] is False
        assert evals[0]['auto_fix_correct'] is False

    def test_fallback_category_correct_when_not_catchall(self):
        """Without MULTI_ACCEPTABLE and not 'other', actual != config_error/infrastructure is correct."""
        tester = _make_tester()
        v = _make_violation(
            violation_summary='deprecated task used',
            failure_category='policy_deprecated_task',
        )
        evals = tester.evaluate_accuracy([v])
        assert evals[0]['category_correct'] is True

    def test_fallback_category_wrong_when_catchall(self):
        """Without MULTI_ACCEPTABLE, if actual is config_error, it's wrong."""
        tester = _make_tester()
        v = _make_violation(
            violation_summary='deprecated task used',
            failure_category='config_error',
        )
        evals = tester.evaluate_accuracy([v])
        # deprecated is in EXPECTED_MULTI so acceptable_cats is populated;
        # config_error is not in acceptable_cats -> False
        assert evals[0]['category_correct'] is False


# ── compute_metrics ──────────────────────────────────────────────────────


class TestComputeMetrics:

    def test_empty_evaluations(self):
        tester = _make_tester()
        result = tester.compute_metrics([], [])
        assert isinstance(result, RegressionMetrics)
        assert result.total_resolved == 0
        assert result.with_ai_analysis == 0
        assert result.ai_coverage_pct == 0.0

    def test_normal_evaluations(self):
        tester = _make_tester()
        evals = [
            {
                'component': 'comp-a',
                'rule_group': 'fips',
                'expected_category': 'policy_fips_check',
                'actual_category': 'policy_fips_check',
                'category_correct': True,
                'confidence': 0.90,
                'can_auto_fix': True,
                'was_quick_fix': True,
                'auto_fix_correct': True,
                'hours_to_resolve': 24.0,
                'human_verdict': None,
                'jira_key': 'X-1',
            },
            {
                'component': 'comp-b',
                'rule_group': 'slsa',
                'expected_category': 'policy_slsa_provenance',
                'actual_category': 'config_error',
                'category_correct': False,
                'confidence': 0.60,
                'can_auto_fix': False,
                'was_quick_fix': False,
                'auto_fix_correct': True,
                'hours_to_resolve': 100.0,
                'human_verdict': None,
                'jira_key': 'X-2',
            },
        ]
        gaps = [
            {'rule_group': 'fips', 'total_resolved': 10, 'with_ai': 8, 'coverage_pct': 80.0},
            {'rule_group': 'slsa', 'total_resolved': 5, 'with_ai': 0, 'coverage_pct': 0.0},
        ]
        result = tester.compute_metrics(evals, gaps)
        assert result.accuracy == 0.50
        assert result.total_resolved == 15
        assert result.with_ai_analysis == 8
        assert 'slsa' in result.coverage_gaps

    def test_calibration_all_correct(self):
        """When all evaluations are correct, calibration equals avg_correct confidence."""
        tester = _make_tester()
        evals = [
            {
                'component': 'c',
                'rule_group': 'fips',
                'expected_category': 'policy_fips_check',
                'actual_category': 'policy_fips_check',
                'category_correct': True,
                'confidence': 0.80,
                'can_auto_fix': False,
                'was_quick_fix': False,
                'auto_fix_correct': True,
                'hours_to_resolve': 100.0,
                'human_verdict': None,
                'jira_key': None,
            },
        ]
        result = tester.compute_metrics(evals, [])
        # No incorrect_confs, so calibration = avg_correct = 0.80
        assert result.calibration_score == 0.80

    def test_by_category_metrics(self):
        tester = _make_tester()
        evals = [
            {
                'component': 'c1',
                'rule_group': 'fips',
                'expected_category': 'policy_fips_check',
                'actual_category': 'policy_fips_check',
                'category_correct': True,
                'confidence': 0.90,
                'can_auto_fix': False,
                'was_quick_fix': False,
                'auto_fix_correct': True,
                'hours_to_resolve': 100.0,
                'human_verdict': None,
                'jira_key': None,
            },
            {
                'component': 'c2',
                'rule_group': 'fips',
                'expected_category': 'policy_fips_check',
                'actual_category': 'policy_fips_check',
                'category_correct': False,
                'confidence': 0.50,
                'can_auto_fix': False,
                'was_quick_fix': False,
                'auto_fix_correct': True,
                'hours_to_resolve': 100.0,
                'human_verdict': 'partial',
                'jira_key': None,
            },
        ]
        result = tester.compute_metrics(evals, [])
        cat_metrics = result.by_category['policy_fips_check']
        assert cat_metrics.total == 2
        assert cat_metrics.correct == 1
        assert cat_metrics.partial == 1
        assert cat_metrics.incorrect == 0


# ── _suggest_improvements ────────────────────────────────────────────────


class TestSuggestImprovements:

    def test_low_confidence_suggestion(self):
        tester = _make_tester()
        by_category = {
            'policy_fips_check': CategoryMetrics(
                total=5, correct=4, incorrect=1, avg_confidence=0.60
            ),
        }
        result = tester._suggest_improvements([], [], by_category)
        assert any('Low confidence' in s for s in result)

    def test_catch_all_category_suggestion(self):
        tester = _make_tester()
        by_category = {
            'config_error': CategoryMetrics(total=4, correct=2, incorrect=2, avg_confidence=0.90),
            'infrastructure': CategoryMetrics(total=3, correct=1, incorrect=2, avg_confidence=0.90),
        }
        result = tester._suggest_improvements([], [], by_category)
        assert any('catch-all' in s for s in result)

    def test_auto_fix_under_marking(self):
        tester = _make_tester()
        evals = [
            {'can_auto_fix': False, 'was_quick_fix': True} for _ in range(8)
        ] + [
            {'can_auto_fix': True, 'was_quick_fix': True} for _ in range(2)
        ]
        result = tester._suggest_improvements(evals, [], {})
        assert any('Auto-fix under-marked' in s for s in result)

    def test_zero_coverage_gap(self):
        tester = _make_tester()
        gaps = [
            {'rule_group': 'rpm', 'total_resolved': 20, 'with_ai': 0, 'coverage_pct': 0},
        ]
        result = tester._suggest_improvements([], gaps, {})
        assert any('Zero AI coverage' in s and 'rpm' in s for s in result)

    def test_no_suggestions_when_all_ok(self):
        tester = _make_tester()
        by_category = {
            'policy_fips_check': CategoryMetrics(
                total=2, correct=2, incorrect=0, avg_confidence=0.95
            ),
        }
        result = tester._suggest_improvements(
            [{'can_auto_fix': True, 'was_quick_fix': True}],
            [{'rule_group': 'fips', 'total_resolved': 5, 'with_ai': 5, 'coverage_pct': 100}],
            by_category,
        )
        assert result == []


# ── identify_coverage_gaps ───────────────────────────────────────────────


class TestIdentifyCoverageGaps:

    def test_returns_gaps_from_db(self):
        tester = _make_tester()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            ('fips', 10, 8),
            ('slsa', 5, 0),
        ]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        tester.db.connection.return_value.__enter__ = Mock(return_value=mock_conn)
        tester.db.connection.return_value.__exit__ = Mock(return_value=False)

        gaps = tester.identify_coverage_gaps()
        assert len(gaps) == 2
        assert gaps[0]['rule_group'] == 'fips'
        assert gaps[0]['total_resolved'] == 10
        assert gaps[0]['with_ai'] == 8
        assert gaps[0]['coverage_pct'] == 80.0
        assert gaps[1]['coverage_pct'] == 0.0

    def test_with_application_filter(self):
        tester = _make_tester()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        tester.db.connection.return_value.__enter__ = Mock(return_value=mock_conn)
        tester.db.connection.return_value.__exit__ = Mock(return_value=False)

        gaps = tester.identify_coverage_gaps(application='rhoai-v3-5')
        mock_cursor.execute.assert_called_once()
        call_args = mock_cursor.execute.call_args
        assert 'rhoai-v3-5' in call_args[0][1]


# ── run ──────────────────────────────────────────────────────────────────


class TestRun:

    def test_no_resolved_returns_not_analyzed(self):
        tester = _make_tester()
        tester.ai_repo.get_resolved_conforma_with_analysis.return_value = []
        result = tester.run()
        assert result['analyzed'] is False
        assert result['reason'] == 'no_resolved_with_analysis'

    def test_normal_flow(self):
        tester = _make_tester()
        now = datetime.utcnow()
        resolved = [
            _make_violation(
                first_detected_at=now - timedelta(hours=72),
                resolved_at=now,
            ),
        ]
        tester.ai_repo.get_resolved_conforma_with_analysis.return_value = resolved

        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            ('hermetic_task', 5, 4),
        ]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        tester.db.connection.return_value.__enter__ = Mock(return_value=mock_conn)
        tester.db.connection.return_value.__exit__ = Mock(return_value=False)

        result = tester.run()
        assert result['analyzed'] is True
        assert 'metrics' in result
        assert result['evaluations_count'] == 1

    def test_verbose_mode_logs(self):
        """Verbose mode should not raise; it logs each evaluation."""
        tester = _make_tester()
        now = datetime.utcnow()
        resolved = [
            _make_violation(
                first_detected_at=now - timedelta(hours=24),
                resolved_at=now,
            ),
        ]
        tester.ai_repo.get_resolved_conforma_with_analysis.return_value = resolved

        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        tester.db.connection.return_value.__enter__ = Mock(return_value=mock_conn)
        tester.db.connection.return_value.__exit__ = Mock(return_value=False)

        result = tester.run(verbose=True)
        assert result['analyzed'] is True


# ── get_resolved_with_analysis ───────────────────────────────────────────


class TestGetResolvedWithAnalysis:

    def test_delegates_to_ai_repo(self):
        tester = _make_tester()
        tester.ai_repo.get_resolved_conforma_with_analysis.return_value = ['a', 'b']
        result = tester.get_resolved_with_analysis(application='myapp', limit=10)
        tester.ai_repo.get_resolved_conforma_with_analysis.assert_called_once_with(
            application='myapp', limit=10,
        )
        assert result == ['a', 'b']


# ── Module-level constants sanity ────────────────────────────────────────


class TestModuleConstants:

    def test_rule_patterns_are_compiled_regexes(self):
        import re
        for name, pattern in RULE_PATTERNS.items():
            assert isinstance(pattern, re.Pattern), f'{name} is not compiled'

    def test_expected_multi_covers_all_rule_patterns(self):
        for group in RULE_PATTERNS:
            assert group in EXPECTED_MULTI, f'{group} missing from EXPECTED_MULTI'

    def test_transient_rules_are_subset_of_patterns(self):
        assert TRANSIENT_RULES.issubset(set(RULE_PATTERNS.keys()))

    def test_fast_resolution_hours_constant(self):
        assert FAST_RESOLUTION_HOURS == 48
