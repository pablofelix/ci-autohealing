"""Tests for analyzers/onboarding_regression.py — regression testing harness."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Mock logger before importing the module under test
sys.modules['logger'] = MagicMock(setup_logger=MagicMock(return_value=MagicMock()))

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from analyzers.onboarding_regression import (
    CATEGORY_MAPPING,
    FAST_RESOLUTION_HOURS,
    SLOW_RESOLUTION_STEPS,
    OnboardingRegressionTester,
)


@pytest.fixture
def config():
    cfg = MagicMock()
    cfg.application_name = 'test-app'
    cfg.llm = MagicMock()
    return cfg


@pytest.fixture
def tester(config):
    return OnboardingRegressionTester(config)


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

def test_category_mapping_has_expected_keys():
    assert 'retry_storm' in CATEGORY_MAPPING
    assert 'branch_exists' in CATEGORY_MAPPING
    assert CATEGORY_MAPPING['retry_storm'] == 'automation_stuck'


def test_slow_resolution_steps_is_set():
    assert isinstance(SLOW_RESOLUTION_STEPS, set)
    assert 'operator_pr' in SLOW_RESOLUTION_STEPS


def test_fast_resolution_hours_value():
    assert FAST_RESOLUTION_HOURS == 72


# ---------------------------------------------------------------------------
# _infer_expected_category
# ---------------------------------------------------------------------------

def test_infer_no_error_categories(tester):
    evaluation = {'error_categories_actual': []}
    assert tester._infer_expected_category(evaluation) is None


def test_infer_single_mapped_category(tester):
    evaluation = {'error_categories_actual': ['retry_storm']}
    assert tester._infer_expected_category(evaluation) == 'automation_stuck'


def test_infer_multiple_categories_voting(tester):
    # Two votes for infrastructure_issue, one for automation_stuck
    evaluation = {
        'error_categories_actual': [
            'cluster_connectivity',   # infrastructure_issue
            'ci_environment',         # infrastructure_issue
            'retry_storm',            # automation_stuck
        ]
    }
    assert tester._infer_expected_category(evaluation) == 'infrastructure_issue'


def test_infer_unmapped_categories_returns_none(tester):
    evaluation = {'error_categories_actual': ['totally_new_pattern']}
    assert tester._infer_expected_category(evaluation) is None


# ---------------------------------------------------------------------------
# evaluate_component
# ---------------------------------------------------------------------------

def test_evaluate_component_no_bot_errors(tester):
    data = {'component': 'comp-a', 'progress': 100, 'bot_error_analysis': {}}
    ev = tester.evaluate_component(data)
    assert ev['component'] == 'comp-a'
    assert ev['had_errors'] is False
    assert ev['error_categories_actual'] == []
    assert ev['analysis_available'] is False


def test_evaluate_component_with_errors_no_analyzer(tester):
    data = {
        'component': 'comp-b',
        'progress': 95,
        'bot_error_analysis': {
            'has_errors': True,
            'error_categories': [{'category': 'retry_storm'}],
            'stuck_steps': {'operator_pr': True},
        },
    }
    ev = tester.evaluate_component(data, analyzer=None)
    assert ev['had_errors'] is True
    assert ev['error_categories_actual'] == ['retry_storm']
    assert ev['stuck_steps_actual'] == ['operator_pr']
    assert ev['analysis_available'] is False
    assert ev['category_correct'] is None


def test_evaluate_component_with_analyzer_correct(tester):
    analyzer = MagicMock()
    analyzer.analyze.return_value = {
        'failure_category': 'automation_stuck',
        'confidence_score': 0.9,
        'recommended_fix': 'retry',
        'can_auto_fix': True,
    }
    data = {
        'component': 'comp-c',
        'progress': 100,
        'bot_error_analysis': {
            'has_errors': True,
            'error_categories': [{'category': 'retry_storm'}],
            'stuck_steps': {},
        },
    }
    ev = tester.evaluate_component(data, analyzer=analyzer)
    assert ev['analysis_available'] is True
    assert ev['analysis_category'] == 'automation_stuck'
    assert ev['category_correct'] is True
    assert ev['expected_category'] == 'automation_stuck'


def test_evaluate_component_analyzer_raises(tester):
    analyzer = MagicMock()
    analyzer.analyze.side_effect = RuntimeError('boom')
    data = {
        'component': 'comp-d',
        'progress': 92,
        'bot_error_analysis': {
            'has_errors': True,
            'error_categories': [{'category': 'retry_storm'}],
            'stuck_steps': {},
        },
    }
    ev = tester.evaluate_component(data, analyzer=analyzer)
    assert ev['analysis_available'] is False
    assert ev.get('analysis_error') == 'boom'


# ---------------------------------------------------------------------------
# compute_metrics
# ---------------------------------------------------------------------------

def test_compute_metrics_empty(tester):
    metrics = tester.compute_metrics([])
    assert metrics['total_completed'] == 0
    assert metrics['accuracy'] == 0
    assert metrics['coverage_pct'] == 0


def test_compute_metrics_single_correct(tester):
    evals = [{
        'had_errors': True,
        'analysis_available': True,
        'category_correct': True,
        'analysis_confidence': 0.85,
        'error_categories_actual': ['retry_storm'],
        'stuck_steps_actual': [],
    }]
    metrics = tester.compute_metrics(evals)
    assert metrics['total_completed'] == 1
    assert metrics['with_errors'] == 1
    assert metrics['correct'] == 1
    assert metrics['accuracy'] == 1.0
    assert metrics['coverage_pct'] == 100.0


def test_compute_metrics_mixed_correct_wrong(tester):
    evals = [
        {
            'had_errors': True,
            'analysis_available': True,
            'category_correct': True,
            'analysis_confidence': 0.9,
            'error_categories_actual': ['retry_storm'],
            'stuck_steps_actual': [],
        },
        {
            'had_errors': True,
            'analysis_available': True,
            'category_correct': False,
            'analysis_confidence': 0.5,
            'error_categories_actual': ['branch_exists'],
            'stuck_steps_actual': ['operator_pr'],
        },
    ]
    metrics = tester.compute_metrics(evals)
    assert metrics['evaluated'] == 2
    assert metrics['correct'] == 1
    assert metrics['accuracy'] == 0.5
    assert metrics['stuck_steps'] == {'operator_pr': 1}


def test_compute_metrics_calibration_all_correct(tester):
    evals = [
        {
            'had_errors': True,
            'analysis_available': True,
            'category_correct': True,
            'analysis_confidence': 0.8,
            'error_categories_actual': [],
            'stuck_steps_actual': [],
        },
    ]
    metrics = tester.compute_metrics(evals)
    # When no wrong predictions, calibration = avg_correct confidence
    assert metrics['calibration_score'] == 0.8


# ---------------------------------------------------------------------------
# suggest_improvements
# ---------------------------------------------------------------------------

def test_suggest_wrong_categories(tester):
    evals = [
        {'category_correct': False, 'analysis_category': 'wrong_cat',
         'had_errors': True, 'analysis_can_auto_fix': False,
         'error_categories_actual': []},
    ]
    metrics = {'error_categories': {}, 'stuck_steps': {}, 'with_errors': 1}
    improvements = tester.suggest_improvements(evals, metrics)
    assert any('wrong_cat' in s and 'misclassified' in s for s in improvements)


def test_suggest_unmapped_error_pattern(tester):
    evals = [
        {'category_correct': None, 'had_errors': True,
         'analysis_can_auto_fix': False, 'error_categories_actual': []},
    ]
    metrics = {
        'error_categories': {'new_pattern': 3},
        'stuck_steps': {},
        'with_errors': 1,
    }
    improvements = tester.suggest_improvements(evals, metrics)
    assert any('new_pattern' in s and 'no category mapping' in s for s in improvements)


def test_suggest_stuck_step(tester):
    evals = [
        {'category_correct': None, 'had_errors': False,
         'analysis_can_auto_fix': False, 'error_categories_actual': []},
    ]
    metrics = {
        'error_categories': {},
        'stuck_steps': {'tekton_pr': 4},
        'with_errors': 0,
    }
    improvements = tester.suggest_improvements(evals, metrics)
    assert any('tekton_pr' in s and 'stuck' in s for s in improvements)


def test_suggest_mostly_clean(tester):
    # 8 out of 10 had no errors -> 80% > 70% threshold
    evals = [{'had_errors': False, 'category_correct': None,
              'analysis_can_auto_fix': False, 'error_categories_actual': []}
             for _ in range(8)]
    evals += [{'had_errors': True, 'category_correct': None,
               'analysis_can_auto_fix': False, 'error_categories_actual': []}
              for _ in range(2)]
    metrics = {'error_categories': {}, 'stuck_steps': {}, 'with_errors': 2}
    improvements = tester.suggest_improvements(evals, metrics)
    assert any('unnecessary' in s for s in improvements)


def test_suggest_low_auto_fix_rate(tester):
    # 7 with errors, 0 auto-fixable -> 0% < 20%
    evals = [{'had_errors': True, 'category_correct': None,
              'analysis_can_auto_fix': False, 'error_categories_actual': []}
             for _ in range(7)]
    metrics = {'error_categories': {}, 'stuck_steps': {}, 'with_errors': 7}
    improvements = tester.suggest_improvements(evals, metrics)
    assert any('auto-fixable' in s for s in improvements)


# ---------------------------------------------------------------------------
# get_completed_onboardings
# ---------------------------------------------------------------------------

def test_get_completed_onboardings_normal(tester):
    with patch.dict('sys.modules', {
        'api': MagicMock(),
        'api.routes': MagicMock(),
        'api.routes.onboarding': MagicMock(
            get_onboarding_status=MagicMock(return_value={
                'components': [
                    {'name': 'a', 'progress': 100},
                    {'name': 'b', 'progress': 50},
                    {'name': 'c', 'progress': 92},
                ],
            })
        ),
    }):
        result = tester.get_completed_onboardings()
    assert len(result) == 2
    assert result[0]['name'] == 'a'
    assert result[1]['name'] == 'c'


@patch.dict('sys.modules', {
    'api': MagicMock(),
    'api.routes': MagicMock(),
    'api.routes.onboarding': MagicMock(
        get_onboarding_status=MagicMock(return_value={'error': 'timeout'})
    ),
})
def test_get_completed_onboardings_error_response(tester):
    result = tester.get_completed_onboardings()
    assert result == []


@patch.dict('sys.modules', {
    'api': MagicMock(),
    'api.routes': MagicMock(),
    'api.routes.onboarding': MagicMock(
        get_onboarding_status=MagicMock(return_value={
            'components': [
                {'name': 'x', 'progress': 10},
                {'name': 'y', 'progress': 89},
            ],
        })
    ),
})
def test_get_completed_onboardings_none_above_90(tester):
    result = tester.get_completed_onboardings()
    assert result == []


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

def test_run_no_completed(tester):
    with patch.object(tester, 'get_completed_onboardings', return_value=[]):
        result = tester.run()
    assert result['analyzed'] is False
    assert result['reason'] == 'no_completed_onboardings'


def test_run_normal_without_llm(tester):
    completed = [{'name': 'comp-a', 'progress': 100}]
    detail = {
        'component': 'comp-a',
        'progress': 100,
        'bot_error_analysis': {},
    }
    with patch.object(tester, 'get_completed_onboardings', return_value=completed), \
         patch.object(tester, 'get_component_details', return_value=detail):
        result = tester.run(use_llm=False)
    assert result['analyzed'] is True
    assert result['evaluations_count'] == 1
    assert 'metrics' in result


def test_run_normal_with_llm(tester):
    completed = [{'name': 'comp-a', 'progress': 100}]
    detail = {
        'component': 'comp-a',
        'progress': 100,
        'bot_error_analysis': {
            'has_errors': True,
            'error_categories': [{'category': 'retry_storm'}],
            'stuck_steps': {},
        },
    }
    mock_analyzer = MagicMock()
    mock_analyzer.analyze.return_value = {
        'failure_category': 'automation_stuck',
        'confidence_score': 0.9,
        'recommended_fix': 'retry',
        'can_auto_fix': True,
    }
    mock_llm_provider = MagicMock()

    with patch.object(tester, 'get_completed_onboardings', return_value=completed), \
         patch.object(tester, 'get_component_details', return_value=detail), \
         patch.dict('sys.modules', {
             'analyzers': MagicMock(),
             'analyzers.onboarding_analyzer': MagicMock(
                 OnboardingAnalyzer=MagicMock(return_value=mock_analyzer)
             ),
             'clients': MagicMock(),
             'clients.llm_provider': MagicMock(
                 create_llm_provider=MagicMock(return_value=mock_llm_provider)
             ),
         }):
        result = tester.run(use_llm=True)
    assert result['analyzed'] is True
    assert result['evaluations_count'] == 1


def test_run_verbose_mode(tester):
    completed = [
        {'name': 'comp-ok', 'progress': 100},
        {'name': 'comp-err', 'progress': 95},
    ]
    detail_ok = {
        'component': 'comp-ok',
        'progress': 100,
        'bot_error_analysis': {},
    }
    detail_err = {
        'component': 'comp-err',
        'progress': 95,
        'bot_error_analysis': {
            'has_errors': True,
            'error_categories': [{'category': 'retry_storm'}],
            'stuck_steps': {'operator_pr': True},
        },
    }

    def mock_details(name, app=None):
        if name == 'comp-ok':
            return detail_ok
        return detail_err

    with patch.object(tester, 'get_completed_onboardings', return_value=completed), \
         patch.object(tester, 'get_component_details', side_effect=mock_details):
        result = tester.run(verbose=True)
    assert result['analyzed'] is True
    assert result['evaluations_count'] == 2
