"""Coverage tests for BuildConfigAnalyzer — covers constructor branches,
data gathering methods, prompt sections, analyze flow, and run entry point.

Targets uncovered lines: 97, 101-103, 107-108, 120-142, 145-166, 169-171,
177-197, 212-214, 243-246, 268-270, 330-395, 405-411.
"""

import os
import sys
from dataclasses import dataclass
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _make_analyzer(db=None, llm=None, langfuse=None):
    """Build an analyzer with explicit mocks for all deps."""
    from analyzers.build_config_analyzer import BuildConfigAnalyzer
    config = MagicMock()
    config.db = MagicMock()
    config.llm = MagicMock()
    return BuildConfigAnalyzer(
        config,
        db=db or MagicMock(),
        llm=llm or MagicMock(),
        langfuse=langfuse or MagicMock(),
    )


def _make_db_with_rows(rows, col_names):
    """Return a mock db whose connection().cursor() returns the given rows."""
    cursor = MagicMock()
    cursor.description = [(name,) for name in col_names]
    cursor.fetchall.return_value = rows

    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)

    db = MagicMock()
    db.connection.return_value = conn
    return db


@dataclass
class FakeHealthWarning:
    component_name: str
    application: str
    signal_type: str
    severity: str
    message: str
    evidence: Dict[str, Any]


# ═══════════════════════════════════════════════════════════════════════
# Constructor branch coverage (lines 97, 101-103, 107-108)
# ═══════════════════════════════════════════════════════════════════════

class TestConstructorBranches:
    """Cover the three db/llm/langfuse is-None branches."""

    @patch('analyzers.build_config_analyzer.DatabaseConnection')
    def test_db_none_creates_database_connection(self, mock_db_cls):
        from analyzers.build_config_analyzer import BuildConfigAnalyzer
        config = MagicMock()
        config.db = MagicMock()
        config.llm = MagicMock()
        mock_db_cls.return_value = MagicMock()

        analyzer = BuildConfigAnalyzer(
            config, db=None, llm=MagicMock(), langfuse=MagicMock(),
        )
        mock_db_cls.assert_called_once_with(config.db)
        assert analyzer.db is mock_db_cls.return_value

    @patch('analyzers.build_config_analyzer.create_llm_provider')
    def test_llm_none_creates_provider(self, mock_create):
        from analyzers.build_config_analyzer import BuildConfigAnalyzer
        config = MagicMock()
        config.db = MagicMock()
        config.llm = MagicMock()  # truthy
        mock_create.return_value = MagicMock()

        analyzer = BuildConfigAnalyzer(
            config, db=MagicMock(), llm=None, langfuse=MagicMock(),
        )
        mock_create.assert_called_once_with(config.llm)
        assert analyzer.llm is mock_create.return_value

    def test_llm_none_raises_when_config_llm_falsy(self):
        from analyzers.build_config_analyzer import BuildConfigAnalyzer
        config = MagicMock()
        config.db = MagicMock()
        config.llm = None  # falsy

        with pytest.raises(ValueError, match="LLM not configured"):
            BuildConfigAnalyzer(
                config, db=MagicMock(), llm=None, langfuse=MagicMock(),
            )

    @patch('analyzers.build_config_analyzer.LangfuseTracker')
    def test_langfuse_none_creates_tracker(self, mock_lf_cls):
        from analyzers.build_config_analyzer import BuildConfigAnalyzer
        config = MagicMock()
        config.db = MagicMock()
        config.llm = MagicMock()
        mock_lf_cls.return_value = MagicMock()

        analyzer = BuildConfigAnalyzer(
            config, db=MagicMock(), llm=MagicMock(), langfuse=None,
        )
        mock_lf_cls.assert_called_once()
        assert analyzer.langfuse is mock_lf_cls.return_value

    @patch.dict(os.environ, {'LANGFUSE_PUBLIC_KEY': 'pk-test'})
    @patch('analyzers.build_config_analyzer.LangfuseTracker')
    def test_langfuse_none_checks_env_key(self, mock_lf_cls):
        from analyzers.build_config_analyzer import BuildConfigAnalyzer
        config = MagicMock()
        config.db = MagicMock()
        config.llm = MagicMock()
        mock_lf_cls.return_value = MagicMock()

        BuildConfigAnalyzer(config, db=MagicMock(), llm=MagicMock(), langfuse=None)
        mock_lf_cls.assert_called_once_with(enabled=True)


# ═══════════════════════════════════════════════════════════════════════
# _gather_recurring_transient_data (lines 120-142)
# ═══════════════════════════════════════════════════════════════════════

class TestGatherRecurringTransientData:
    def test_returns_list_of_dicts_from_db(self):
        db = _make_db_with_rows(
            [('comp-a', 'signing_error', 5, '2026-07-01', '2026-06-25')],
            ['component_name', 'error_type', 'failure_count', 'latest', 'earliest'],
        )
        analyzer = _make_analyzer(db=db)
        result = analyzer._gather_recurring_transient_data('rhoai-v3-5')

        assert len(result) == 1
        assert result[0]['component_name'] == 'comp-a'
        assert result[0]['failure_count'] == 5

    def test_returns_multiple_rows(self):
        db = _make_db_with_rows(
            [
                ('comp-a', 'signing_error', 5, '2026-07-01', '2026-06-25'),
                ('comp-b', 'build_timeout', 3, '2026-07-01', '2026-06-28'),
            ],
            ['component_name', 'error_type', 'failure_count', 'latest', 'earliest'],
        )
        analyzer = _make_analyzer(db=db)
        result = analyzer._gather_recurring_transient_data('rhoai-v3-5')
        assert len(result) == 2

    def test_returns_empty_on_exception(self):
        db = MagicMock()
        db.connection.side_effect = Exception("DB connection failed")
        analyzer = _make_analyzer(db=db)
        result = analyzer._gather_recurring_transient_data('rhoai-v3-5')
        assert result == []

    def test_returns_empty_when_no_rows(self):
        db = _make_db_with_rows(
            [],
            ['component_name', 'error_type', 'failure_count', 'latest', 'earliest'],
        )
        analyzer = _make_analyzer(db=db)
        result = analyzer._gather_recurring_transient_data('rhoai-v3-5')
        assert result == []


# ═══════════════════════════════════════════════════════════════════════
# _gather_build_duration_data (lines 145-166)
# ═══════════════════════════════════════════════════════════════════════

class TestGatherBuildDurationData:
    def test_returns_list_of_dicts(self):
        db = _make_db_with_rows(
            [('slow-comp', 2400.0, 3600.0, 10)],
            ['component_name', 'avg_duration', 'max_duration', 'build_count'],
        )
        analyzer = _make_analyzer(db=db)
        result = analyzer._gather_build_duration_data('rhoai-v3-5')

        assert len(result) == 1
        assert result[0]['component_name'] == 'slow-comp'
        assert result[0]['avg_duration'] == 2400.0

    def test_returns_empty_on_exception(self):
        db = MagicMock()
        db.connection.side_effect = RuntimeError("cursor boom")
        analyzer = _make_analyzer(db=db)
        result = analyzer._gather_build_duration_data('rhoai-v3-5')
        assert result == []

    def test_returns_empty_when_no_rows(self):
        db = _make_db_with_rows(
            [],
            ['component_name', 'avg_duration', 'max_duration', 'build_count'],
        )
        analyzer = _make_analyzer(db=db)
        result = analyzer._gather_build_duration_data('rhoai-v3-5')
        assert result == []


# ═══════════════════════════════════════════════════════════════════════
# _gather_webhook_issues (lines 169-171)
# ═══════════════════════════════════════════════════════════════════════

class TestGatherWebhookIssues:
    def test_returns_empty_when_stale_data_is_none(self):
        analyzer = _make_analyzer()
        assert analyzer._gather_webhook_issues(None) == []

    def test_returns_empty_when_stale_key_missing(self):
        analyzer = _make_analyzer()
        assert analyzer._gather_webhook_issues({'error': 'some error'}) == []

    def test_returns_empty_when_no_pac_issues(self):
        analyzer = _make_analyzer()
        stale_data = {
            'stale': [
                {'component': 'comp-a', 'diagnosis': {'cause': 'some_other_cause'}},
            ],
        }
        assert analyzer._gather_webhook_issues(stale_data) == []

    def test_filters_missing_pac_repository(self):
        analyzer = _make_analyzer()
        stale_data = {
            'stale': [
                {'component': 'comp-a', 'diagnosis': {'cause': 'missing_pac_repository'}},
                {'component': 'comp-b', 'diagnosis': {'cause': 'other_cause'}},
            ],
        }
        result = analyzer._gather_webhook_issues(stale_data)
        assert len(result) == 1
        assert result[0]['component'] == 'comp-a'


# ═══════════════════════════════════════════════════════════════════════
# _gather_build_chain_data (lines 177-197)
# ═══════════════════════════════════════════════════════════════════════

class TestGatherBuildChainData:
    @patch('analyzers.build_config_analyzer.BuildFailureRepository')
    def test_returns_chain_issues_when_upstream_match(self, mock_repo_cls):
        mock_repo = MagicMock()
        mock_repo.find_failing_component_names.return_value = {'comp-upstream'}
        mock_repo_cls.return_value = mock_repo

        analyzer = _make_analyzer()
        stale_data = {
            'stale': [
                {
                    'component': 'comp-downstream',
                    'diagnosis': {
                        'cause': 'upstream_failure',
                        'detail': 'comp-upstream is failing',
                    },
                },
            ],
        }
        result = analyzer._gather_build_chain_data('rhoai-v3-5', stale_data)
        assert len(result) == 1
        assert result[0]['upstream'] == 'comp-upstream is failing'
        assert 'comp-downstream' in result[0]['downstream']

    @patch('analyzers.build_config_analyzer.BuildFailureRepository')
    def test_returns_empty_when_no_failing_components(self, mock_repo_cls):
        mock_repo = MagicMock()
        mock_repo.find_failing_component_names.return_value = set()
        mock_repo_cls.return_value = mock_repo

        analyzer = _make_analyzer()
        result = analyzer._gather_build_chain_data('rhoai-v3-5', {'stale': []})
        assert result == []

    @patch('analyzers.build_config_analyzer.BuildFailureRepository')
    def test_nudge_misconfiguration_cause_also_matched(self, mock_repo_cls):
        mock_repo = MagicMock()
        mock_repo.find_failing_component_names.return_value = {'dep-comp'}
        mock_repo_cls.return_value = mock_repo

        analyzer = _make_analyzer()
        stale_data = {
            'stale': [
                {
                    'name': 'my-downstream',
                    'diagnosis': {
                        'cause': 'nudge_misconfiguration',
                        'detail': 'dep-comp nudge broken',
                    },
                },
            ],
        }
        result = analyzer._gather_build_chain_data('rhoai-v3-5', stale_data)
        assert len(result) == 1
        assert 'my-downstream' in result[0]['downstream']

    @patch('analyzers.build_config_analyzer.BuildFailureRepository')
    def test_returns_empty_on_exception(self, mock_repo_cls):
        mock_repo_cls.side_effect = Exception("repo init failed")

        analyzer = _make_analyzer()
        result = analyzer._gather_build_chain_data('rhoai-v3-5', {'stale': []})
        assert result == []

    @patch('analyzers.build_config_analyzer.BuildFailureRepository')
    def test_skips_stale_with_non_matching_cause(self, mock_repo_cls):
        mock_repo = MagicMock()
        mock_repo.find_failing_component_names.return_value = {'comp-x'}
        mock_repo_cls.return_value = mock_repo

        analyzer = _make_analyzer()
        stale_data = {
            'stale': [
                {
                    'component': 'comp-y',
                    'diagnosis': {
                        'cause': 'build_timeout',
                        'detail': 'comp-x is mentioned',
                    },
                },
            ],
        }
        result = analyzer._gather_build_chain_data('rhoai-v3-5', stale_data)
        # cause is 'build_timeout', not nudge/upstream, so no chain issue
        assert result == []


# ═══════════════════════════════════════════════════════════════════════
# _gather_repeat_failure_data except handler (lines 212-214)
# ═══════════════════════════════════════════════════════════════════════

class TestGatherRepeatFailureData:
    @patch('analyzers.build_config_analyzer.HealthMonitor')
    def test_returns_dicts_from_warnings(self, mock_hm_cls):
        mock_hm = MagicMock()
        warning = FakeHealthWarning(
            component_name='comp-fail',
            application='rhoai-v3-5',
            signal_type='repeat_failure',
            severity='critical',
            message='comp-fail still broken after 3 fix attempts',
            evidence={},
        )
        mock_hm.get_repeat_failures.return_value = [warning]
        mock_hm_cls.return_value = mock_hm

        analyzer = _make_analyzer()
        result = analyzer._gather_repeat_failure_data()

        assert len(result) == 1
        assert result[0]['component'] == 'comp-fail'
        assert result[0]['severity'] == 'critical'

    @patch('analyzers.build_config_analyzer.HealthMonitor')
    def test_returns_empty_on_exception(self, mock_hm_cls):
        mock_hm = MagicMock()
        mock_hm.get_repeat_failures.side_effect = Exception("db error")
        mock_hm_cls.return_value = mock_hm

        analyzer = _make_analyzer()
        result = analyzer._gather_repeat_failure_data()
        assert result == []


# ═══════════════════════════════════════════════════════════════════════
# build_analysis_prompt: duration + repeat sections (lines 243-246, 268-270)
# ═══════════════════════════════════════════════════════════════════════

class TestBuildAnalysisPromptCoverage:
    def test_prompt_includes_duration_section(self):
        analyzer = _make_analyzer()
        gathered = {
            'application': 'rhoai-v3-5',
            'stale_components': {'stale': [], 'stale_count': 0},
            'recurring_transient': [],
            'build_durations': [
                {'component_name': 'slow-comp', 'avg_duration': 2400.0},
            ],
            'webhook_issues': [],
            'build_chain': [],
            'repeat_failures': [],
        }
        _, user_prompt = analyzer.build_analysis_prompt(gathered)
        assert 'Quota/Duration Bottlenecks' in user_prompt
        assert 'slow-comp' in user_prompt
        assert '2400' in user_prompt
        assert '40' in user_prompt  # 2400/60 = 40 min

    def test_prompt_includes_repeat_failures_section(self):
        analyzer = _make_analyzer()
        gathered = {
            'application': 'rhoai-v3-5',
            'stale_components': {'stale': [], 'stale_count': 0},
            'recurring_transient': [],
            'build_durations': [],
            'webhook_issues': [],
            'build_chain': [],
            'repeat_failures': [
                {'component': 'comp-fail', 'message': 'still broken after 3 fixes'},
            ],
        }
        _, user_prompt = analyzer.build_analysis_prompt(gathered)
        assert 'Repeat Failures' in user_prompt
        assert 'comp-fail' in user_prompt
        assert 'still broken' in user_prompt

    def test_duration_with_none_avg_handled(self):
        analyzer = _make_analyzer()
        gathered = {
            'application': 'rhoai-v3-5',
            'stale_components': {'stale': [], 'stale_count': 0},
            'recurring_transient': [],
            'build_durations': [
                {'component_name': 'null-comp', 'avg_duration': None},
            ],
            'webhook_issues': [],
            'build_chain': [],
            'repeat_failures': [],
        }
        _, user_prompt = analyzer.build_analysis_prompt(gathered)
        assert 'null-comp' in user_prompt

    def test_duration_empty_dict_not_rendered(self):
        """build_durations={} (dict, not list) should not render section."""
        analyzer = _make_analyzer()
        gathered = {
            'application': 'rhoai-v3-5',
            'stale_components': {'stale': [], 'stale_count': 0},
            'recurring_transient': [],
            'build_durations': {},
            'webhook_issues': [],
            'build_chain': [],
            'repeat_failures': [],
        }
        _, user_prompt = analyzer.build_analysis_prompt(gathered)
        assert 'Quota/Duration' not in user_prompt


# ═══════════════════════════════════════════════════════════════════════
# analyze() full flow (lines 330-395)
# ═══════════════════════════════════════════════════════════════════════

class TestAnalyzeFlow:
    def _build_llm_response(self):
        response = MagicMock()
        response.tool_calls = [{
            'name': 'record_build_config_analysis',
            'input': {
                'findings': [{
                    'title': 'Stale nudge for comp-a',
                    'severity': 'warning',
                    'category': 'stale_component',
                    'description': 'Component comp-a is 5 days behind HEAD',
                    'recommendation': 'Re-trigger nudge propagation',
                }],
                'overall_severity': 'warning',
                'confidence_score': 0.8,
                'summary': 'Found 1 stale component issue',
                'auto_rebuild_candidates': ['comp-a'],
            },
        }]
        response.input_tokens = 1000
        response.output_tokens = 500
        return response

    @patch('analyzers.build_config_analyzer.HealthMonitor')
    @patch('analyzers.build_config_analyzer.BuildFailureRepository')
    def test_analyze_returns_full_result(self, mock_repo_cls, mock_hm_cls):
        # Set up HealthMonitor stubs
        mock_hm = MagicMock()
        mock_hm.get_stale_components.return_value = {
            'stale': [], 'stale_count': 0,
        }
        mock_hm.get_repeat_failures.return_value = []
        mock_hm_cls.return_value = mock_hm

        # Set up repo stub
        mock_repo = MagicMock()
        mock_repo.find_failing_component_names.return_value = set()
        mock_repo_cls.return_value = mock_repo

        # Set up DB for recurring/duration queries
        db = _make_db_with_rows(
            [],
            ['component_name', 'error_type', 'failure_count', 'latest', 'earliest'],
        )

        llm = MagicMock()
        llm.create_message.return_value = self._build_llm_response()
        llm.model_name.return_value = 'claude-sonnet-4-20250514'

        langfuse = MagicMock()
        langfuse.create_trace.return_value = MagicMock()

        analyzer = _make_analyzer(db=db, llm=llm, langfuse=langfuse)
        result = analyzer.analyze(application='test-app')

        assert result['analyzed'] is True
        assert 'analysis' in result
        assert result['analysis']['overall_severity'] == 'warning'
        assert result['analysis']['confidence_score'] == 0.8
        assert len(result['analysis']['findings']) == 1
        assert result['model'] == 'claude-sonnet-4-20250514'
        assert result['cost_usd'] > 0
        assert result['duration'] >= 0

        # Verify LLM was called with the tool
        llm.create_message.assert_called_once()
        call_kwargs = llm.create_message.call_args
        assert call_kwargs.kwargs.get('max_tokens') == 8192 or call_kwargs[1].get('max_tokens') == 8192

        # Verify Langfuse lifecycle
        langfuse.create_trace.assert_called_once()
        langfuse.record_generation.assert_called_once()
        langfuse.end_trace.assert_called_once()
        langfuse.flush.assert_called_once()

    @patch('analyzers.build_config_analyzer.HealthMonitor')
    @patch('analyzers.build_config_analyzer.BuildFailureRepository')
    def test_analyze_uses_env_default_application(self, mock_repo_cls, mock_hm_cls):
        mock_hm = MagicMock()
        mock_hm.get_stale_components.return_value = {'stale': [], 'stale_count': 0}
        mock_hm.get_repeat_failures.return_value = []
        mock_hm_cls.return_value = mock_hm
        mock_repo = MagicMock()
        mock_repo.find_failing_component_names.return_value = set()
        mock_repo_cls.return_value = mock_repo

        db = _make_db_with_rows([], ['component_name', 'error_type', 'failure_count', 'latest', 'earliest'])

        llm = MagicMock()
        llm.create_message.return_value = self._build_llm_response()
        llm.model_name.return_value = 'test-model'

        langfuse = MagicMock()
        langfuse.create_trace.return_value = MagicMock()

        analyzer = _make_analyzer(db=db, llm=llm, langfuse=langfuse)

        with patch.dict(os.environ, {'APPLICATION_NAME': 'env-app'}):
            result = analyzer.analyze(application=None)

        # The langfuse trace should show the env-app application
        trace_call = langfuse.create_trace.call_args
        assert trace_call.kwargs.get('input_data', {}).get('application') == 'env-app' or \
               trace_call[1].get('input_data', {}).get('application') == 'env-app'
        assert result['analyzed'] is True

    @patch('analyzers.build_config_analyzer.HealthMonitor')
    @patch('analyzers.build_config_analyzer.BuildFailureRepository')
    def test_analyze_cost_calculation(self, mock_repo_cls, mock_hm_cls):
        mock_hm = MagicMock()
        mock_hm.get_stale_components.return_value = {'stale': [], 'stale_count': 0}
        mock_hm.get_repeat_failures.return_value = []
        mock_hm_cls.return_value = mock_hm
        mock_repo = MagicMock()
        mock_repo.find_failing_component_names.return_value = set()
        mock_repo_cls.return_value = mock_repo

        db = _make_db_with_rows([], ['component_name', 'error_type', 'failure_count', 'latest', 'earliest'])

        response = self._build_llm_response()
        response.input_tokens = 10000
        response.output_tokens = 2000

        llm = MagicMock()
        llm.create_message.return_value = response
        llm.model_name.return_value = 'test-model'

        langfuse = MagicMock()
        langfuse.create_trace.return_value = MagicMock()

        analyzer = _make_analyzer(db=db, llm=llm, langfuse=langfuse)
        result = analyzer.analyze(application='test-app')

        expected_cost = (10000 * 0.000003) + (2000 * 0.000015)
        assert abs(result['cost_usd'] - expected_cost) < 1e-10


# ═══════════════════════════════════════════════════════════════════════
# run() entry point (lines 405-411)
# ═══════════════════════════════════════════════════════════════════════

class TestRunEntryPoint:
    @patch('analyzers.build_config_analyzer.HealthMonitor')
    @patch('analyzers.build_config_analyzer.BuildFailureRepository')
    def test_run_delegates_to_analyze(self, mock_repo_cls, mock_hm_cls):
        mock_hm = MagicMock()
        mock_hm.get_stale_components.return_value = {'stale': [], 'stale_count': 0}
        mock_hm.get_repeat_failures.return_value = []
        mock_hm_cls.return_value = mock_hm
        mock_repo = MagicMock()
        mock_repo.find_failing_component_names.return_value = set()
        mock_repo_cls.return_value = mock_repo

        db = _make_db_with_rows([], ['component_name', 'error_type', 'failure_count', 'latest', 'earliest'])

        response = MagicMock()
        response.tool_calls = [{
            'name': 'record_build_config_analysis',
            'input': {
                'findings': [],
                'overall_severity': 'info',
                'confidence_score': 0.9,
                'summary': 'All builds healthy, no issues found',
                'auto_rebuild_candidates': [],
            },
        }]
        response.input_tokens = 500
        response.output_tokens = 200

        llm = MagicMock()
        llm.create_message.return_value = response
        llm.model_name.return_value = 'test-model'

        langfuse = MagicMock()
        langfuse.create_trace.return_value = MagicMock()

        analyzer = _make_analyzer(db=db, llm=llm, langfuse=langfuse)
        result = analyzer.run(application='test-app')

        assert result['analyzed'] is True
        assert result['analysis']['overall_severity'] == 'info'

    @patch('analyzers.build_config_analyzer.HealthMonitor')
    @patch('analyzers.build_config_analyzer.BuildFailureRepository')
    def test_run_without_application_arg(self, mock_repo_cls, mock_hm_cls):
        mock_hm = MagicMock()
        mock_hm.get_stale_components.return_value = {'stale': [], 'stale_count': 0}
        mock_hm.get_repeat_failures.return_value = []
        mock_hm_cls.return_value = mock_hm
        mock_repo = MagicMock()
        mock_repo.find_failing_component_names.return_value = set()
        mock_repo_cls.return_value = mock_repo

        db = _make_db_with_rows([], ['component_name', 'error_type', 'failure_count', 'latest', 'earliest'])

        response = MagicMock()
        response.tool_calls = [{
            'name': 'record_build_config_analysis',
            'input': {
                'findings': [],
                'overall_severity': 'info',
                'confidence_score': 0.9,
                'summary': 'All builds healthy, no issues found',
                'auto_rebuild_candidates': [],
            },
        }]
        response.input_tokens = 500
        response.output_tokens = 200

        llm = MagicMock()
        llm.create_message.return_value = response
        llm.model_name.return_value = 'test-model'

        langfuse = MagicMock()
        langfuse.create_trace.return_value = MagicMock()

        analyzer = _make_analyzer(db=db, llm=llm, langfuse=langfuse)
        # Passing None exercises the if-not-application branch in run()
        result = analyzer.run(application=None)
        assert result['analyzed'] is True

    @patch('analyzers.build_config_analyzer.HealthMonitor')
    @patch('analyzers.build_config_analyzer.BuildFailureRepository')
    def test_run_with_application_logs_it(self, mock_repo_cls, mock_hm_cls):
        """Exercises line 408: logger.info("Application: %s", application)."""
        mock_hm = MagicMock()
        mock_hm.get_stale_components.return_value = {'stale': [], 'stale_count': 0}
        mock_hm.get_repeat_failures.return_value = []
        mock_hm_cls.return_value = mock_hm
        mock_repo = MagicMock()
        mock_repo.find_failing_component_names.return_value = set()
        mock_repo_cls.return_value = mock_repo

        db = _make_db_with_rows([], ['component_name', 'error_type', 'failure_count', 'latest', 'earliest'])

        response = MagicMock()
        response.tool_calls = [{
            'name': 'record_build_config_analysis',
            'input': {
                'findings': [],
                'overall_severity': 'info',
                'confidence_score': 0.95,
                'summary': 'All builds healthy and passing',
                'auto_rebuild_candidates': [],
            },
        }]
        response.input_tokens = 100
        response.output_tokens = 50

        llm = MagicMock()
        llm.create_message.return_value = response
        llm.model_name.return_value = 'test-model'

        langfuse = MagicMock()
        langfuse.create_trace.return_value = MagicMock()

        analyzer = _make_analyzer(db=db, llm=llm, langfuse=langfuse)
        # Passing a string exercises the `if application:` branch on line 407
        result = analyzer.run(application='my-specific-app')
        assert result['analyzed'] is True
