"""Comprehensive tests for ConfigAnalyzer.

Covers constructor, every public/private method, and the full orchestration
flow. All external dependencies are mocked to avoid real DB/LLM/K8s calls.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(llm=True):
    """Build a mock config object matching the real AppConfig shape."""
    config = MagicMock()
    config.db = MagicMock()
    if llm:
        config.llm = MagicMock()
        config.llm.provider = 'anthropic'
    else:
        config.llm = None
    return config


def _make_llm_response(tool_input=None, tool_name='record_config_analysis',
                        input_tokens=2000, output_tokens=800):
    """Build a mock LLMResponse matching the real shape."""
    if tool_input is None:
        tool_input = {
            'findings': [{
                'title': 'Expired exception found',
                'severity': 'critical',
                'category': 'expired_exceptions',
                'description': 'Exception for rule X expired 10 days ago',
                'recommendation': 'Renew the exception or fix the underlying issue',
                'affected_components': ['comp-a'],
                'can_auto_fix': False,
                'fix_action': 'exception',
            }],
            'overall_severity': 'critical',
            'confidence_score': 0.90,
            'summary': 'Found 1 critical issue: expired policy exception',
            'auto_rebuild_candidates': ['comp-b'],
        }
    resp = MagicMock()
    resp.tool_calls = [{'name': tool_name, 'input': tool_input}]
    resp.input_tokens = input_tokens
    resp.output_tokens = output_tokens
    return resp


def _make_db_mocks():
    """Build mock db, connection, and cursor for _gather_violation_data."""
    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_db = MagicMock()
    mock_db.connection.return_value = mock_conn
    return mock_db, mock_conn, mock_cursor


# ---------------------------------------------------------------------------
# 1. Constructor tests
# ---------------------------------------------------------------------------

@patch('analyzers.config_analyzer.CONFIG_SYSTEM_PROMPT', 'system prompt')
class TestConfigAnalyzerConstructor:
    """Constructor dependency injection and defaults."""

    @patch('analyzers.config_analyzer.LangfuseTracker')
    def test_all_deps_injected(self, mock_tracker_cls):
        from analyzers.config_analyzer import ConfigAnalyzer

        config = _make_config()
        db = MagicMock()
        llm = MagicMock()
        langfuse = MagicMock()

        analyzer = ConfigAnalyzer(config, db=db, llm=llm, langfuse=langfuse)

        assert analyzer.config is config
        assert analyzer.db is db
        assert analyzer.llm is llm
        assert analyzer.langfuse is langfuse

    @patch('analyzers.config_analyzer.LangfuseTracker')
    @patch('analyzers.config_analyzer.DatabaseConnection')
    def test_db_none_creates_default(self, mock_db_cls, mock_tracker_cls):
        from analyzers.config_analyzer import ConfigAnalyzer

        config = _make_config()
        llm = MagicMock()
        mock_db_inst = MagicMock()
        mock_db_cls.return_value = mock_db_inst

        analyzer = ConfigAnalyzer(config, db=None, llm=llm, langfuse=MagicMock())

        mock_db_cls.assert_called_once_with(config.db)
        assert analyzer.db is mock_db_inst

    @patch('analyzers.config_analyzer.LangfuseTracker')
    def test_llm_none_config_llm_none_raises(self, mock_tracker_cls):
        from analyzers.config_analyzer import ConfigAnalyzer

        config = _make_config(llm=False)

        with pytest.raises(ValueError, match="LLM not configured"):
            ConfigAnalyzer(config, db=MagicMock(), llm=None, langfuse=MagicMock())

    @patch('analyzers.config_analyzer.LangfuseTracker')
    @patch('analyzers.config_analyzer.create_llm_provider')
    def test_llm_none_creates_from_config(self, mock_create, mock_tracker_cls):
        from analyzers.config_analyzer import ConfigAnalyzer

        config = _make_config()
        mock_llm = MagicMock()
        mock_create.return_value = mock_llm

        analyzer = ConfigAnalyzer(config, db=MagicMock(), llm=None, langfuse=MagicMock())

        mock_create.assert_called_once_with(config.llm)
        assert analyzer.llm is mock_llm


# ---------------------------------------------------------------------------
# 2. _gather_ec_policy_data tests
# ---------------------------------------------------------------------------

@patch('analyzers.config_analyzer.CONFIG_SYSTEM_PROMPT', 'system prompt')
class TestGatherEcPolicyData:

    def _make_analyzer(self):
        from analyzers.config_analyzer import ConfigAnalyzer
        config = _make_config()
        return ConfigAnalyzer(config, db=MagicMock(), llm=MagicMock(), langfuse=MagicMock())

    @patch.dict(os.environ, {'NAMESPACE': 'test-ns'}, clear=False)
    def test_no_policies_empty(self):
        analyzer = self._make_analyzer()
        mock_client = MagicMock()
        mock_client.get_ec_policies.return_value = []
        with patch('clients.konflux_client.KonfluxClient', return_value=mock_client):
            result = analyzer._gather_ec_policy_data()

        assert result['policies_count'] == 0
        assert result['total_exceptions'] == 0
        assert result['active'] == []
        assert result['expired'] == []
        assert result['expiring'] == []

    def test_with_active_expired_expiring_exceptions(self):
        analyzer = self._make_analyzer()
        mock_client = MagicMock()
        policies = [MagicMock(), MagicMock()]
        mock_client.get_ec_policies.return_value = policies
        exceptions = [
            {'rule': 'r1', 'days_left': None, 'value': 'exc-a', 'policy': 'p1'},      # active (no expiry)
            {'rule': 'r2', 'days_left': 45, 'value': 'exc-b', 'policy': 'p1'},         # active (>30d)
            {'rule': 'r3', 'days_left': 10, 'value': 'exc-c', 'policy': 'p2'},         # expiring
            {'rule': 'r4', 'days_left': -5, 'value': 'exc-d', 'policy': 'p2'},         # expired
        ]
        mock_client.extract_exceptions.side_effect = [exceptions[:2], exceptions[2:]]

        with patch('clients.konflux_client.KonfluxClient', return_value=mock_client):
            result = analyzer._gather_ec_policy_data()

        assert result['policies_count'] == 2
        assert result['total_exceptions'] == 4
        assert len(result['active']) == 3  # days_left=None, 45, 10
        assert len(result['expired']) == 1
        assert len(result['expiring']) == 1
        assert result['expiring'][0]['value'] == 'exc-c'

    def test_exception_returns_error_dict(self):
        analyzer = self._make_analyzer()
        with patch('clients.konflux_client.KonfluxClient', side_effect=RuntimeError("cluster down")):
            result = analyzer._gather_ec_policy_data()

        assert 'error' in result
        assert 'cluster down' in result['error']

    def test_expiring_sorted_by_days_left(self):
        analyzer = self._make_analyzer()
        mock_client = MagicMock()
        mock_client.get_ec_policies.return_value = [MagicMock()]
        exceptions = [
            {'rule': 'r1', 'days_left': 25, 'value': 'exc-25d', 'policy': 'p1'},
            {'rule': 'r2', 'days_left': 5, 'value': 'exc-5d', 'policy': 'p1'},
            {'rule': 'r3', 'days_left': 15, 'value': 'exc-15d', 'policy': 'p1'},
        ]
        mock_client.extract_exceptions.return_value = exceptions

        with patch('clients.konflux_client.KonfluxClient', return_value=mock_client):
            result = analyzer._gather_ec_policy_data()

        assert result['expiring'][0]['days_left'] == 5
        assert result['expiring'][1]['days_left'] == 15
        assert result['expiring'][2]['days_left'] == 25


# ---------------------------------------------------------------------------
# 3. _gather_scenario_data tests
# ---------------------------------------------------------------------------

@patch('analyzers.config_analyzer.CONFIG_SYSTEM_PROMPT', 'system prompt')
class TestGatherScenarioData:

    def _make_analyzer(self):
        from analyzers.config_analyzer import ConfigAnalyzer
        config = _make_config()
        return ConfigAnalyzer(config, db=MagicMock(), llm=MagicMock(), langfuse=MagicMock())

    def test_with_scenarios(self):
        analyzer = self._make_analyzer()
        mock_kc_cls = MagicMock()
        mock_client = MagicMock()
        scenarios = [MagicMock(), MagicMock(), MagicMock()]
        mock_client.get_integration_test_scenarios.return_value = scenarios
        metadata = [
            {'name': 's1', 'application': 'app-a', 'is_disabled': False, 'is_conforma': True},
            {'name': 's2', 'application': 'app-a', 'is_disabled': True, 'is_conforma': True},
            {'name': 's3', 'application': 'app-b', 'is_disabled': False, 'is_conforma': False},
        ]
        mock_kc_cls.return_value = mock_client
        mock_kc_cls.extract_its_metadata = MagicMock(side_effect=metadata)

        with patch('clients.konflux_client.KonfluxClient', mock_kc_cls):
            result = analyzer._gather_scenario_data('app-a')

        assert result['total'] == 3
        assert result['active'] == 2
        assert result['disabled'] == 1
        assert result['conforma'] == 2
        assert 'app-b' in result['gaps']  # app-b has no active conforma

    def test_with_coverage_gaps(self):
        analyzer = self._make_analyzer()
        mock_kc_cls = MagicMock()
        mock_client = MagicMock()
        mock_client.get_integration_test_scenarios.return_value = [MagicMock()]
        metadata = [
            {'name': 's1', 'application': 'app-x', 'is_disabled': False, 'is_conforma': False},
        ]
        mock_kc_cls.return_value = mock_client
        mock_kc_cls.extract_its_metadata = MagicMock(side_effect=metadata)

        with patch('clients.konflux_client.KonfluxClient', mock_kc_cls):
            result = analyzer._gather_scenario_data('app-x')

        assert result['gaps'] == ['app-x']

    def test_empty_scenarios(self):
        analyzer = self._make_analyzer()
        mock_client = MagicMock()
        mock_client.get_integration_test_scenarios.return_value = []

        with patch('clients.konflux_client.KonfluxClient', return_value=mock_client):
            result = analyzer._gather_scenario_data('app-z')

        assert result['total'] == 0
        assert result['active'] == 0
        assert result['gaps'] == []

    def test_exception_returns_error_dict(self):
        analyzer = self._make_analyzer()
        with patch('clients.konflux_client.KonfluxClient', side_effect=ConnectionError("timeout")):
            result = analyzer._gather_scenario_data('app-z')

        assert 'error' in result
        assert 'timeout' in result['error']


# ---------------------------------------------------------------------------
# 4. _gather_violation_data tests
# ---------------------------------------------------------------------------

@patch('analyzers.config_analyzer.CONFIG_SYSTEM_PROMPT', 'system prompt')
class TestGatherViolationData:

    def _make_analyzer_with_db(self):
        from analyzers.config_analyzer import ConfigAnalyzer
        config = _make_config()
        db, conn, cursor = _make_db_mocks()
        analyzer = ConfigAnalyzer(config, db=db, llm=MagicMock(), langfuse=MagicMock())
        return analyzer, cursor

    def test_with_unresolved_violations(self):
        analyzer, cursor = self._make_analyzer_with_db()
        cursor.fetchall.side_effect = [
            # First query: unresolved
            [
                ('comp-a', 'hermetic_task', 3, '2025-06-01', 'scenario-1'),
                ('comp-b', 'fips_check', 1, '2025-06-15', 'scenario-2'),
            ],
            # Second query: resolved patterns
            [],
        ]

        result = analyzer._gather_violation_data('rhoai-v3-5')

        assert result['unresolved_count'] == 2
        assert result['unresolved'][0]['component'] == 'comp-a'
        assert result['unresolved'][0]['rule'] == 'hermetic_task'
        assert result['unresolved'][0]['count'] == 3
        assert result['unresolved'][1]['component'] == 'comp-b'
        assert result['resolved_patterns'] == []

    def test_with_resolved_patterns(self):
        analyzer, cursor = self._make_analyzer_with_db()
        cursor.fetchall.side_effect = [
            [],  # unresolved
            [
                ('hermetic_task', 10, 24.5),
                ('fips', 5, 48.0),
            ],
        ]

        result = analyzer._gather_violation_data('rhoai-v3-5')

        assert result['unresolved_count'] == 0
        assert len(result['resolved_patterns']) == 2
        assert result['resolved_patterns'][0]['rule_group'] == 'hermetic_task'
        assert result['resolved_patterns'][0]['count'] == 10
        assert result['resolved_patterns'][0]['avg_hours_to_resolve'] == 24.5

    def test_empty_results(self):
        analyzer, cursor = self._make_analyzer_with_db()
        cursor.fetchall.side_effect = [[], []]

        result = analyzer._gather_violation_data('rhoai-v3-5')

        assert result['unresolved_count'] == 0
        assert result['unresolved'] == []
        assert result['resolved_patterns'] == []


# ---------------------------------------------------------------------------
# 5. _classify_rebuild_candidates tests
# ---------------------------------------------------------------------------

@patch('analyzers.config_analyzer.CONFIG_SYSTEM_PROMPT', 'system prompt')
class TestClassifyRebuildCandidates:

    def _make_analyzer(self):
        from analyzers.config_analyzer import ConfigAnalyzer
        config = _make_config()
        return ConfigAnalyzer(config, db=MagicMock(), llm=MagicMock(), langfuse=MagicMock())

    def test_empty_violations(self):
        analyzer = self._make_analyzer()
        result = analyzer._classify_rebuild_candidates([])
        assert result == []

    def test_all_transient_rules_rebuild_candidate(self):
        analyzer = self._make_analyzer()
        violations = [
            {'component': 'comp-a', 'rule': 'builtin.attestation.signature_check: failed'},
            {'component': 'comp-a', 'rule': 'tasks.successful_pipeline_tasks: 1 erred'},
        ]
        result = analyzer._classify_rebuild_candidates(violations)
        assert result == ['comp-a']

    def test_mixed_transient_non_transient_not_candidate(self):
        analyzer = self._make_analyzer()
        violations = [
            {'component': 'comp-a', 'rule': 'builtin.attestation.signature_check: failed'},
            {'component': 'comp-a', 'rule': 'fips_check.some_custom_rule: not compliant'},
        ]
        result = analyzer._classify_rebuild_candidates(violations)
        assert result == []

    def test_multiple_components_only_some_qualify(self):
        analyzer = self._make_analyzer()
        violations = [
            # comp-a: all transient -> qualifies
            {'component': 'comp-a', 'rule': 'builtin.attestation.signature_check: failed'},
            {'component': 'comp-a', 'rule': 'test.no_erred_tests: 1 erred'},
            # comp-b: mixed -> does not qualify
            {'component': 'comp-b', 'rule': 'builtin.attestation.signature_check: failed'},
            {'component': 'comp-b', 'rule': 'custom.non_transient_rule: failing'},
            # comp-c: all transient -> qualifies
            {'component': 'comp-c', 'rule': 'rpm_packages.unique_version: duplicate'},
        ]
        result = analyzer._classify_rebuild_candidates(violations)
        assert result == ['comp-a', 'comp-c']


# ---------------------------------------------------------------------------
# 6. build_analysis_prompt tests
# ---------------------------------------------------------------------------

@patch('analyzers.config_analyzer.CONFIG_SYSTEM_PROMPT', 'system prompt')
class TestBuildAnalysisPrompt:

    def _make_analyzer(self):
        from analyzers.config_analyzer import ConfigAnalyzer
        config = _make_config()
        return ConfigAnalyzer(config, db=MagicMock(), llm=MagicMock(), langfuse=MagicMock())

    def test_returns_tuple(self):
        analyzer = self._make_analyzer()
        result = analyzer.build_analysis_prompt({'application': 'test-app'})
        assert isinstance(result, tuple)
        assert len(result) == 2
        system_prompt, user_prompt = result
        assert system_prompt == 'system prompt'
        assert 'test-app' in user_prompt

    def test_minimal_data(self):
        analyzer = self._make_analyzer()
        system, user = analyzer.build_analysis_prompt({'application': 'my-app'})
        assert 'my-app' in user
        assert 'record_config_analysis' in user

    def test_full_data(self):
        analyzer = self._make_analyzer()
        gathered = {
            'application': 'rhoai-v3-5',
            'ec_policy': {
                'policies_count': 2,
                'active': [{'value': 'exc-a', 'days_left': None, 'policy': 'p1'}],
                'expired': [{'value': 'exc-x', 'days_left': -10, 'policy': 'p2'}],
                'expiring': [{'value': 'exc-soon', 'days_left': 5, 'policy': 'p2'}],
            },
            'scenarios': {
                'total': 10, 'active': 8, 'disabled': 2, 'conforma': 5,
                'gaps': ['app-z'],
                'disabled_conforma': [{'name': 'sc-disabled', 'application': 'app-y'}],
            },
            'violations': {
                'unresolved_count': 2,
                'unresolved': [
                    {'component': 'comp-a', 'rule': 'fips', 'count': 3, 'since': '2025-06-01'},
                ],
                'resolved_patterns': [
                    {'rule_group': 'hermetic_task', 'count': 5, 'avg_hours_to_resolve': 12.0},
                ],
            },
            'rebuild_candidates': ['comp-b'],
        }
        system, user = analyzer.build_analysis_prompt(gathered)

        assert '## EC Policy Summary' in user
        assert 'Policies: 2' in user
        assert 'exc-soon' in user
        assert '5d left' in user
        assert 'exc-x' in user
        assert 'expired 10d ago' in user
        assert '## ITS Scenario Coverage' in user
        assert 'app-z' in user
        assert 'sc-disabled' in user
        assert '## Unresolved Violations (2)' in user
        assert 'comp-a' in user
        assert '## Resolved Patterns' in user
        assert '## Auto-rebuild Candidates' in user
        assert 'comp-b' in user

    def test_with_ec_policy_error(self):
        analyzer = self._make_analyzer()
        gathered = {
            'application': 'test',
            'ec_policy': {'error': 'cluster unavailable'},
        }
        system, user = analyzer.build_analysis_prompt(gathered)
        # error key prevents the EC section from rendering
        assert '## EC Policy Summary' not in user

    def test_with_expired_exceptions_details(self):
        analyzer = self._make_analyzer()
        gathered = {
            'application': 'test',
            'ec_policy': {
                'policies_count': 1,
                'active': [],
                'expired': [
                    {'value': 'rule-old', 'days_left': -30, 'policy': 'prod-policy'},
                ],
                'expiring': [],
            },
        }
        system, user = analyzer.build_analysis_prompt(gathered)
        assert 'Expired:' in user
        assert 'rule-old' in user
        assert 'expired 30d ago' in user
        assert 'prod-policy' in user


# ---------------------------------------------------------------------------
# 7. parse_analysis_response tests
# ---------------------------------------------------------------------------

@patch('analyzers.config_analyzer.CONFIG_SYSTEM_PROMPT', 'system prompt')
class TestParseAnalysisResponse:

    def _make_analyzer(self):
        from analyzers.config_analyzer import ConfigAnalyzer
        config = _make_config()
        return ConfigAnalyzer(config, db=MagicMock(), llm=MagicMock(), langfuse=MagicMock())

    def test_valid_response(self):
        analyzer = self._make_analyzer()
        resp = _make_llm_response()
        result = analyzer.parse_analysis_response(resp)

        assert result['overall_severity'] == 'critical'
        assert result['confidence_score'] == 0.90
        assert len(result['findings']) == 1
        assert result['findings'][0]['title'] == 'Expired exception found'
        assert result['auto_rebuild_candidates'] == ['comp-b']

    def test_empty_tool_calls_raises(self):
        analyzer = self._make_analyzer()
        resp = MagicMock()
        resp.tool_calls = []

        with pytest.raises(ValueError, match="LLM did not return tool_use"):
            analyzer.parse_analysis_response(resp)

    def test_none_tool_calls_raises(self):
        analyzer = self._make_analyzer()
        resp = MagicMock()
        resp.tool_calls = None

        with pytest.raises(ValueError, match="LLM did not return tool_use"):
            analyzer.parse_analysis_response(resp)

    def test_wrong_tool_name_raises(self):
        analyzer = self._make_analyzer()
        resp = _make_llm_response(tool_name='wrong_tool')

        with pytest.raises(ValueError, match="did not call record_config_analysis"):
            analyzer.parse_analysis_response(resp)

    def test_invalid_data_falls_back_to_defaults(self):
        analyzer = self._make_analyzer()
        # Missing required fields and invalid values trigger Pydantic error
        resp = _make_llm_response(tool_input={
            'findings': 'not-a-list',
            'overall_severity': 'invalid',
            'confidence_score': 'not-a-number',
            'summary': 'ok',
        })

        result = analyzer.parse_analysis_response(resp)

        assert result['findings'] == []
        assert result['overall_severity'] == 'info'
        assert result['confidence_score'] == 0.0
        assert result['auto_rebuild_candidates'] == []

    def test_multiple_tool_calls_picks_correct_one(self):
        analyzer = self._make_analyzer()
        resp = MagicMock()
        resp.tool_calls = [
            {'name': 'some_other_tool', 'input': {'data': 'irrelevant'}},
            {'name': 'record_config_analysis', 'input': {
                'findings': [],
                'overall_severity': 'info',
                'confidence_score': 0.50,
                'summary': 'All systems are healthy, no issues detected',
                'auto_rebuild_candidates': [],
            }},
        ]

        result = analyzer.parse_analysis_response(resp)

        assert result['overall_severity'] == 'info'
        assert result['confidence_score'] == 0.50
        assert result['summary'] == 'All systems are healthy, no issues detected'


# ---------------------------------------------------------------------------
# 8. analyze tests
# ---------------------------------------------------------------------------

@patch('analyzers.config_analyzer.CONFIG_SYSTEM_PROMPT', 'system prompt')
class TestAnalyze:

    def _make_analyzer(self):
        from analyzers.config_analyzer import ConfigAnalyzer
        config = _make_config()
        llm = MagicMock()
        langfuse = MagicMock()
        langfuse.create_trace.return_value = MagicMock()
        analyzer = ConfigAnalyzer(config, db=MagicMock(), llm=llm, langfuse=langfuse)
        return analyzer

    def test_happy_path(self):
        analyzer = self._make_analyzer()

        # Mock all gather methods
        analyzer._gather_ec_policy_data = MagicMock(return_value={
            'policies_count': 1, 'total_exceptions': 0,
            'active': [], 'expired': [], 'expiring': [],
        })
        analyzer._gather_scenario_data = MagicMock(return_value={
            'total': 5, 'active': 5, 'disabled': 0, 'conforma': 3,
            'applications': ['app-a'], 'gaps': [], 'disabled_conforma': [],
        })
        analyzer._gather_violation_data = MagicMock(return_value={
            'unresolved': [], 'unresolved_count': 0, 'resolved_patterns': [],
        })
        analyzer._classify_rebuild_candidates = MagicMock(return_value=[])

        # Mock LLM response
        resp = _make_llm_response()
        analyzer.llm.create_message.return_value = resp
        analyzer.llm.model_name.return_value = 'claude-sonnet-4-20250514'

        result = analyzer.analyze(application='test-app')

        assert result['analyzed'] is True
        assert result['analysis']['overall_severity'] == 'critical'
        assert result['model'] == 'claude-sonnet-4-20250514'
        assert 'duration' in result
        assert 'cost_usd' in result
        analyzer._gather_ec_policy_data.assert_called_once()
        analyzer._gather_scenario_data.assert_called_once_with('test-app')
        analyzer._gather_violation_data.assert_called_once_with('test-app')

    @patch.dict(os.environ, {'APPLICATION_NAME': 'rhoai-v3-5'}, clear=False)
    def test_default_application_from_env(self):
        analyzer = self._make_analyzer()
        analyzer._gather_ec_policy_data = MagicMock(return_value={})
        analyzer._gather_scenario_data = MagicMock(return_value={})
        analyzer._gather_violation_data = MagicMock(return_value={
            'unresolved': [], 'unresolved_count': 0, 'resolved_patterns': [],
        })
        analyzer._classify_rebuild_candidates = MagicMock(return_value=[])

        resp = _make_llm_response()
        analyzer.llm.create_message.return_value = resp
        analyzer.llm.model_name.return_value = 'test-model'

        result = analyzer.analyze()  # no application param

        analyzer._gather_scenario_data.assert_called_once_with('rhoai-v3-5')

    def test_error_in_gather_step_handled(self):
        analyzer = self._make_analyzer()
        analyzer._gather_ec_policy_data = MagicMock(return_value={'error': 'no access'})
        analyzer._gather_scenario_data = MagicMock(return_value={'error': 'timeout'})
        analyzer._gather_violation_data = MagicMock(return_value={
            'unresolved': [], 'unresolved_count': 0, 'resolved_patterns': [],
        })
        analyzer._classify_rebuild_candidates = MagicMock(return_value=[])

        resp = _make_llm_response(tool_input={
            'findings': [],
            'overall_severity': 'info',
            'confidence_score': 0.50,
            'summary': 'Analysis completed with limited data available',
            'auto_rebuild_candidates': [],
        })
        analyzer.llm.create_message.return_value = resp
        analyzer.llm.model_name.return_value = 'test-model'

        result = analyzer.analyze(application='test-app')

        assert result['analyzed'] is True
        assert result['analysis']['overall_severity'] == 'info'

    def test_returns_expected_structure(self):
        analyzer = self._make_analyzer()
        analyzer._gather_ec_policy_data = MagicMock(return_value={})
        analyzer._gather_scenario_data = MagicMock(return_value={})
        analyzer._gather_violation_data = MagicMock(return_value={
            'unresolved': [{'component': 'c1', 'rule': 'r1', 'count': 2, 'since': '2025-06-01', 'scenario': 'prod'}],
            'unresolved_count': 1,
            'resolved_patterns': [],
        })
        analyzer._classify_rebuild_candidates = MagicMock(return_value=['c1'])

        resp = _make_llm_response()
        analyzer.llm.create_message.return_value = resp
        analyzer.llm.model_name.return_value = 'test-model'

        result = analyzer.analyze(application='test-app')

        assert 'analyzed' in result
        assert 'analysis' in result
        assert 'duration' in result
        assert 'cost_usd' in result
        assert 'model' in result
        assert 'gathered_data_summary' in result
        assert result['gathered_data_summary']['violations_count'] == 1
        assert result['gathered_data_summary']['rebuild_candidates'] == ['c1']


# ---------------------------------------------------------------------------
# 9. run tests
# ---------------------------------------------------------------------------

@patch('analyzers.config_analyzer.CONFIG_SYSTEM_PROMPT', 'system prompt')
class TestRun:

    def _make_analyzer(self):
        from analyzers.config_analyzer import ConfigAnalyzer
        config = _make_config()
        analyzer = ConfigAnalyzer(config, db=MagicMock(), llm=MagicMock(), langfuse=MagicMock())
        return analyzer

    def test_delegates_to_analyze(self):
        analyzer = self._make_analyzer()
        expected = {'analyzed': True, 'analysis': {}}
        analyzer.analyze = MagicMock(return_value=expected)

        result = analyzer.run(application='test-app')

        assert result is expected
        analyzer.analyze.assert_called_once_with(application='test-app')

    def test_with_application_parameter(self):
        analyzer = self._make_analyzer()
        analyzer.analyze = MagicMock(return_value={'analyzed': True})

        analyzer.run(application='custom-app')

        analyzer.analyze.assert_called_once_with(application='custom-app')

    def test_without_application_parameter(self):
        analyzer = self._make_analyzer()
        analyzer.analyze = MagicMock(return_value={'analyzed': True})

        analyzer.run()

        analyzer.analyze.assert_called_once_with(application=None)


# ---------------------------------------------------------------------------
# 10. Module-level constants
# ---------------------------------------------------------------------------

@patch('analyzers.config_analyzer.CONFIG_SYSTEM_PROMPT', 'system prompt')
class TestModuleConstants:

    def test_transient_rules_set(self):
        from analyzers.config_analyzer import TRANSIENT_RULES

        assert isinstance(TRANSIENT_RULES, set)
        assert 'builtin.attestation.signature_check' in TRANSIENT_RULES
        assert 'slsa_source_correlated.source_code_reference_provided' in TRANSIENT_RULES
        assert 'tasks.successful_pipeline_tasks' in TRANSIENT_RULES
        assert 'test.no_erred_tests' in TRANSIENT_RULES
        assert 'rpm_packages.unique_version' in TRANSIENT_RULES
        assert len(TRANSIENT_RULES) == 5

    def test_config_analysis_tool_schema(self):
        from analyzers.config_analyzer import CONFIG_ANALYSIS_TOOL

        assert CONFIG_ANALYSIS_TOOL['name'] == 'record_config_analysis'
        schema = CONFIG_ANALYSIS_TOOL['input_schema']
        assert 'findings' in schema['properties']
        assert 'overall_severity' in schema['properties']
        assert 'confidence_score' in schema['properties']
        assert 'summary' in schema['properties']
        assert 'auto_rebuild_candidates' in schema['properties']
