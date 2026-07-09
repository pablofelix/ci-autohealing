"""Comprehensive tests for ReleaseConfigAnalyzer: constructor, data gathering,
prompt building, response parsing, analyze flow, and run entrypoint."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _make_config(llm=True):
    """Create a mock config object."""
    config = MagicMock()
    config.db = MagicMock()
    if llm:
        config.llm = MagicMock()
    else:
        config.llm = None
    return config


def _make_analyzer(config=None, db=None, llm=None, langfuse=None):
    """Instantiate ReleaseConfigAnalyzer with full mocks by default."""
    from analyzers.release_config_analyzer import ReleaseConfigAnalyzer

    config = config or _make_config()
    return ReleaseConfigAnalyzer(
        config,
        db=db if db is not None else MagicMock(),
        llm=llm if llm is not None else MagicMock(),
        langfuse=langfuse if langfuse is not None else MagicMock(),
    )


def _make_llm_response(tool_input, tool_name='record_release_config_analysis',
                        extra_calls=None, input_tokens=1000, output_tokens=500):
    """Create a mock LLM response with tool calls."""
    resp = MagicMock()
    calls = list(extra_calls or [])
    calls.append({'name': tool_name, 'input': tool_input})
    resp.tool_calls = calls
    resp.input_tokens = input_tokens
    resp.output_tokens = output_tokens
    return resp


def _valid_tool_input(**overrides):
    """Return a valid tool_input dict for ReleaseConfigAnalysisResult."""
    base = {
        'findings': [],
        'overall_severity': 'info',
        'confidence_score': 0.85,
        'summary': 'No critical issues found in release configuration',
        'release_blockers': [],
    }
    base.update(overrides)
    return base


def _valid_finding(**overrides):
    """Return a valid finding dict for ReleaseConfigFinding."""
    base = {
        'title': 'Test finding title',
        'severity': 'warning',
        'category': 'conforma_blocker',
        'description': 'A detailed description of the issue found',
        'recommendation': 'Rebuild the affected component to fix this',
        'affected_components': ['comp-a'],
        'blocks_release': False,
        'fix_action': 'rebuild',
    }
    base.update(overrides)
    return base


# ═══════════════════════════════════════════════════════════════════════
# Constructor tests
# ═══════════════════════════════════════════════════════════════════════

class TestReleaseConfigAnalyzerConstructor:
    def test_constructor_with_all_deps(self):
        config = _make_config()
        db = MagicMock()
        llm = MagicMock()
        langfuse = MagicMock()
        analyzer = _make_analyzer(config=config, db=db, llm=llm, langfuse=langfuse)

        assert analyzer.config is config
        assert analyzer.db is db
        assert analyzer.llm is llm
        assert analyzer.langfuse is langfuse

    @patch('analyzers.release_config_analyzer.DatabaseConnection')
    def test_constructor_creates_db_when_none(self, mock_db_class):
        from analyzers.release_config_analyzer import ReleaseConfigAnalyzer

        config = _make_config()
        mock_db_class.return_value = MagicMock()

        analyzer = ReleaseConfigAnalyzer(
            config, db=None, llm=MagicMock(), langfuse=MagicMock(),
        )

        mock_db_class.assert_called_once_with(config.db)
        assert analyzer.db is mock_db_class.return_value

    @patch('analyzers.release_config_analyzer.create_llm_provider')
    def test_constructor_creates_llm_when_none(self, mock_create_llm):
        from analyzers.release_config_analyzer import ReleaseConfigAnalyzer

        config = _make_config()
        mock_create_llm.return_value = MagicMock()

        analyzer = ReleaseConfigAnalyzer(
            config, db=MagicMock(), llm=None, langfuse=MagicMock(),
        )

        mock_create_llm.assert_called_once_with(config.llm)
        assert analyzer.llm is mock_create_llm.return_value

    def test_constructor_raises_without_llm_config(self):
        from analyzers.release_config_analyzer import ReleaseConfigAnalyzer

        config = _make_config(llm=False)

        with pytest.raises(ValueError, match="LLM not configured"):
            ReleaseConfigAnalyzer(
                config, db=MagicMock(), llm=None, langfuse=MagicMock(),
            )

    @patch('analyzers.release_config_analyzer.LangfuseTracker')
    def test_constructor_creates_langfuse_when_none(self, mock_lf_class):
        from analyzers.release_config_analyzer import ReleaseConfigAnalyzer

        config = _make_config()
        mock_lf_class.return_value = MagicMock()

        analyzer = ReleaseConfigAnalyzer(
            config, db=MagicMock(), llm=MagicMock(), langfuse=None,
        )

        mock_lf_class.assert_called_once()
        assert analyzer.langfuse is mock_lf_class.return_value


# ═══════════════════════════════════════════════════════════════════════
# Data gathering tests
# ═══════════════════════════════════════════════════════════════════════

class TestGatherConformaBlockerData:
    @patch('analyzers.release_config_analyzer.ConformaRepository')
    def test_success_returns_list(self, mock_repo_class):
        violations = [{'component': 'comp-a', 'rule': 'r1', 'count': 3}]
        mock_repo_class.return_value.get_violation_summaries.return_value = violations

        analyzer = _make_analyzer()
        result = analyzer._gather_conforma_blocker_data('rhoai-v3-5')

        mock_repo_class.assert_called_once_with(analyzer.db)
        mock_repo_class.return_value.get_violation_summaries.assert_called_once_with('rhoai-v3-5')
        assert result == violations

    @patch('analyzers.release_config_analyzer.ConformaRepository')
    def test_exception_returns_empty_list(self, mock_repo_class):
        mock_repo_class.return_value.get_violation_summaries.side_effect = RuntimeError("db down")

        analyzer = _make_analyzer()
        result = analyzer._gather_conforma_blocker_data('rhoai-v3-5')

        assert result == []


class TestGatherPccFreshnessData:
    @patch('analyzers.release_config_analyzer.HealthMonitor')
    def test_success_returns_dict(self, mock_hm_class):
        pcc_data = {'status': 'stale', 'missing_versions': ['3.4']}
        mock_hm_class.return_value._check_pcc_freshness.return_value = pcc_data

        analyzer = _make_analyzer()
        result = analyzer._gather_pcc_freshness_data()

        mock_hm_class.assert_called_once_with(analyzer.db)
        assert result == pcc_data

    @patch('analyzers.release_config_analyzer.HealthMonitor')
    def test_exception_returns_none(self, mock_hm_class):
        mock_hm_class.return_value._check_pcc_freshness.side_effect = Exception("fail")

        analyzer = _make_analyzer()
        result = analyzer._gather_pcc_freshness_data()

        assert result is None


class TestGatherExceptionCoverageData:
    @patch('conforma.policy_tools.fetch_exceptions_by_policy')
    def test_success_returns_dict(self, mock_fetch):
        exc_data = {'stage': [{'rule': 'r1'}], 'prod': []}
        mock_fetch.return_value = exc_data

        analyzer = _make_analyzer()
        result = analyzer._gather_exception_coverage_data('rhoai-v3-5')

        mock_fetch.assert_called_once_with('rhoai-v3-5')
        assert result == exc_data

    @patch('conforma.policy_tools.fetch_exceptions_by_policy', side_effect=ImportError("no module"))
    def test_exception_returns_empty_dict(self, mock_fetch):
        analyzer = _make_analyzer()
        result = analyzer._gather_exception_coverage_data('rhoai-v3-5')

        assert result == {}


class TestGatherSnapshotFreshnessData:
    @patch('analyzers.release_config_analyzer.HealthMonitor')
    def test_success_returns_dict(self, mock_hm_class):
        snap_data = {'stale': [{'component': 'c1', 'reason': 'old'}]}
        mock_hm_class.return_value.check_snapshot_freshness.return_value = snap_data

        analyzer = _make_analyzer()
        result = analyzer._gather_snapshot_freshness_data('rhoai-v3-5')

        mock_hm_class.assert_called_once_with(analyzer.db)
        mock_hm_class.return_value.check_snapshot_freshness.assert_called_once_with('rhoai-v3-5')
        assert result == snap_data

    @patch('analyzers.release_config_analyzer.HealthMonitor')
    def test_exception_returns_empty_dict(self, mock_hm_class):
        mock_hm_class.return_value.check_snapshot_freshness.side_effect = Exception("fail")

        analyzer = _make_analyzer()
        result = analyzer._gather_snapshot_freshness_data('rhoai-v3-5')

        assert result == {}


class TestGatherNightlyStatusData:
    @patch('analyzers.release_config_analyzer.HealthMonitor')
    def test_success_returns_dict(self, mock_hm_class):
        nightly_data = {'fbc_health': {'current_status': 'green'}, 'blockers': []}
        mock_hm_class.return_value.get_nightly_status.return_value = nightly_data

        analyzer = _make_analyzer()
        result = analyzer._gather_nightly_status_data('rhoai-v3-5')

        mock_hm_class.assert_called_once_with(analyzer.db)
        mock_hm_class.return_value.get_nightly_status.assert_called_once_with('rhoai-v3-5')
        assert result == nightly_data

    @patch('analyzers.release_config_analyzer.HealthMonitor')
    def test_exception_returns_empty_dict(self, mock_hm_class):
        mock_hm_class.return_value.get_nightly_status.side_effect = Exception("fail")

        analyzer = _make_analyzer()
        result = analyzer._gather_nightly_status_data('rhoai-v3-5')

        assert result == {}


# ═══════════════════════════════════════════════════════════════════════
# Prompt building tests
# ═══════════════════════════════════════════════════════════════════════

class TestBuildAnalysisPromptCoverage:
    def test_all_sections_present(self):
        analyzer = _make_analyzer()
        gathered = {
            'application': 'rhoai-v3-5',
            'conforma_violations': [
                {'component': 'comp-a', 'rule': 'r1', 'count': 3},
            ],
            'pcc_freshness': {
                'status': 'stale',
                'missing_versions': ['3.4'],
            },
            'exception_coverage': {
                'stage-policy': [{'rule': 'r1'}, {'rule': 'r2'}],
            },
            'snapshot_freshness': {
                'stale': [{'component': 'comp-b', 'reason': 'older than 7d'}],
            },
            'nightly_status': {
                'fbc_health': {'current_status': 'red'},
                'blockers': [{'component': 'fbc-fragment'}],
            },
        }
        sys_prompt, user_prompt = analyzer.build_analysis_prompt(gathered)

        assert 'rhoai-v3-5' in user_prompt
        assert 'Conforma Violations' in user_prompt
        assert 'comp-a' in user_prompt
        assert 'PCC Cache Status' in user_prompt
        assert 'stale' in user_prompt
        assert 'Exception Coverage' in user_prompt
        assert 'stage-policy' in user_prompt
        assert 'Snapshot Freshness' in user_prompt
        assert 'comp-b' in user_prompt
        assert 'Nightly Build Status' in user_prompt
        assert 'fbc-fragment' in user_prompt
        assert sys_prompt is not None

    def test_only_conforma_section(self):
        analyzer = _make_analyzer()
        gathered = {
            'application': 'rhoai-v3-5',
            'conforma_violations': [
                {'component': 'comp-x', 'rule': 'hermetic', 'count': 2},
            ],
            'pcc_freshness': None,
            'exception_coverage': {},
            'snapshot_freshness': {},
            'nightly_status': {},
        }
        _, user_prompt = analyzer.build_analysis_prompt(gathered)

        assert 'Conforma Violations' in user_prompt
        assert 'PCC Cache Status' not in user_prompt
        assert 'Exception Coverage' not in user_prompt
        assert 'Snapshot Freshness' not in user_prompt
        assert 'Nightly Build Status' not in user_prompt

    def test_only_pcc_section(self):
        analyzer = _make_analyzer()
        gathered = {
            'application': 'rhoai-v3-5',
            'conforma_violations': [],
            'pcc_freshness': {'status': 'fresh'},
            'exception_coverage': {},
            'snapshot_freshness': {},
            'nightly_status': {},
        }
        _, user_prompt = analyzer.build_analysis_prompt(gathered)

        assert 'PCC Cache Status' in user_prompt
        assert 'Conforma Violations' not in user_prompt

    def test_only_snapshot_section(self):
        analyzer = _make_analyzer()
        gathered = {
            'application': 'rhoai-v3-5',
            'conforma_violations': [],
            'pcc_freshness': None,
            'exception_coverage': {},
            'snapshot_freshness': {
                'stale': [{'component': 'snap-comp', 'reason': 'stale'}],
            },
            'nightly_status': {},
        }
        _, user_prompt = analyzer.build_analysis_prompt(gathered)

        assert 'Snapshot Freshness' in user_prompt
        assert 'snap-comp' in user_prompt
        assert 'Conforma Violations' not in user_prompt

    def test_only_nightly_section(self):
        analyzer = _make_analyzer()
        gathered = {
            'application': 'rhoai-v3-5',
            'conforma_violations': [],
            'pcc_freshness': None,
            'exception_coverage': {},
            'snapshot_freshness': {},
            'nightly_status': {
                'fbc_health': {'current_status': 'green'},
                'blockers': [{'component': 'blocker-comp'}],
            },
        }
        _, user_prompt = analyzer.build_analysis_prompt(gathered)

        assert 'Nightly Build Status' in user_prompt
        assert 'blocker-comp' in user_prompt

    def test_only_exception_section(self):
        analyzer = _make_analyzer()
        gathered = {
            'application': 'rhoai-v3-5',
            'conforma_violations': [],
            'pcc_freshness': None,
            'exception_coverage': {
                'prod-policy': [{'rule': 'r1'}],
            },
            'snapshot_freshness': {},
            'nightly_status': {},
        }
        _, user_prompt = analyzer.build_analysis_prompt(gathered)

        assert 'Exception Coverage' in user_prompt
        assert 'prod-policy' in user_prompt
        assert '1 exceptions' in user_prompt

    def test_no_data(self):
        analyzer = _make_analyzer()
        gathered = {
            'application': 'rhoai-v3-5',
            'conforma_violations': [],
            'pcc_freshness': None,
            'exception_coverage': {},
            'snapshot_freshness': {},
            'nightly_status': {},
        }
        _, user_prompt = analyzer.build_analysis_prompt(gathered)

        assert 'No data collected.' in user_prompt

    def test_pcc_with_last_regen(self):
        analyzer = _make_analyzer()
        gathered = {
            'application': 'rhoai-v3-5',
            'conforma_violations': [],
            'pcc_freshness': {
                'status': 'stale',
                'last_regen': {'date': '2026-06-30', 'conclusion': 'success'},
            },
            'exception_coverage': {},
            'snapshot_freshness': {},
            'nightly_status': {},
        }
        _, user_prompt = analyzer.build_analysis_prompt(gathered)

        assert 'Last regen' in user_prompt
        assert '2026-06-30' in user_prompt
        assert 'success' in user_prompt

    def test_conforma_with_component_name_key(self):
        """Violations using component_name key instead of component."""
        analyzer = _make_analyzer()
        gathered = {
            'application': 'rhoai-v3-5',
            'conforma_violations': [
                {'component_name': 'alt-comp', 'violation_summary': 'bad-rule', 'violations_count': 5},
            ],
            'pcc_freshness': None,
            'exception_coverage': {},
            'snapshot_freshness': {},
            'nightly_status': {},
        }
        _, user_prompt = analyzer.build_analysis_prompt(gathered)

        assert 'alt-comp' in user_prompt
        assert 'bad-rule' in user_prompt

    def test_nightly_with_string_blockers(self):
        """Blockers that are plain strings instead of dicts."""
        analyzer = _make_analyzer()
        gathered = {
            'application': 'rhoai-v3-5',
            'conforma_violations': [],
            'pcc_freshness': None,
            'exception_coverage': {},
            'snapshot_freshness': {},
            'nightly_status': {
                'fbc_health': {},
                'blockers': ['string-blocker-1', 'string-blocker-2'],
            },
        }
        _, user_prompt = analyzer.build_analysis_prompt(gathered)

        assert 'Nightly Build Status' in user_prompt
        assert 'string-blocker-1' in user_prompt
        assert 'string-blocker-2' in user_prompt


# ═══════════════════════════════════════════════════════════════════════
# Response parsing tests
# ═══════════════════════════════════════════════════════════════════════

class TestParseAnalysisResponseCoverage:
    def test_valid_tool_call(self):
        analyzer = _make_analyzer()
        tool_input = _valid_tool_input()
        resp = _make_llm_response(tool_input)

        result = analyzer.parse_analysis_response(resp)

        assert result['overall_severity'] == 'info'
        assert result['confidence_score'] == 0.85
        assert result['findings'] == []
        assert 'summary' in result

    def test_no_tool_calls_raises(self):
        analyzer = _make_analyzer()
        resp = MagicMock()
        resp.tool_calls = []

        with pytest.raises(ValueError, match="did not return tool_use"):
            analyzer.parse_analysis_response(resp)

    def test_no_tool_calls_none_raises(self):
        analyzer = _make_analyzer()
        resp = MagicMock()
        resp.tool_calls = None

        with pytest.raises(ValueError, match="did not return tool_use"):
            analyzer.parse_analysis_response(resp)

    def test_wrong_tool_name_raises(self):
        analyzer = _make_analyzer()
        resp = MagicMock()
        resp.tool_calls = [{'name': 'wrong_tool', 'input': {}}]

        with pytest.raises(ValueError, match="did not call record_release_config_analysis"):
            analyzer.parse_analysis_response(resp)

    def test_validation_error_returns_fallback(self):
        analyzer = _make_analyzer()
        # invalid: confidence_score out of range, severity not valid
        bad_input = {
            'findings': [],
            'overall_severity': 'INVALID',
            'confidence_score': 99.0,
            'summary': 'Some summary text for validation',
        }
        resp = _make_llm_response(bad_input)

        result = analyzer.parse_analysis_response(resp)

        assert result['overall_severity'] == 'info'
        assert result['confidence_score'] == 0.0
        assert result['findings'] == []
        assert result['summary'] == 'Some summary text for validation'

    def test_validation_error_fallback_missing_summary(self):
        analyzer = _make_analyzer()
        bad_input = {
            'findings': [],
            'overall_severity': 'NOT_VALID',
            'confidence_score': 99.0,
        }
        resp = _make_llm_response(bad_input)

        result = analyzer.parse_analysis_response(resp)

        assert result['summary'] == 'Validation failed'

    def test_multiple_tool_calls_picks_correct(self):
        analyzer = _make_analyzer()
        tool_input = _valid_tool_input(summary='Correct tool was picked for analysis')
        extra = [{'name': 'some_other_tool', 'input': {'foo': 'bar'}}]
        resp = _make_llm_response(tool_input, extra_calls=extra)

        result = analyzer.parse_analysis_response(resp)

        assert result['summary'] == 'Correct tool was picked for analysis'

    def test_valid_with_release_blockers(self):
        analyzer = _make_analyzer()
        tool_input = _valid_tool_input(
            release_blockers=['comp-a RPM signature missing', 'comp-b hermetic violation'],
        )
        resp = _make_llm_response(tool_input)

        result = analyzer.parse_analysis_response(resp)

        assert len(result['release_blockers']) == 2
        assert 'comp-a RPM signature missing' in result['release_blockers']

    def test_valid_with_multiple_findings(self):
        analyzer = _make_analyzer()
        findings = [
            _valid_finding(title='Finding one is critical', severity='critical',
                           category='conforma_blocker', blocks_release=True),
            _valid_finding(title='Finding two is a warning', severity='warning',
                           category='pcc_cache_stale'),
        ]
        tool_input = _valid_tool_input(
            findings=findings,
            overall_severity='critical',
        )
        resp = _make_llm_response(tool_input)

        result = analyzer.parse_analysis_response(resp)

        assert len(result['findings']) == 2
        assert result['findings'][0]['severity'] == 'critical'
        assert result['findings'][0]['blocks_release'] is True
        assert result['findings'][1]['category'] == 'pcc_cache_stale'
        assert result['overall_severity'] == 'critical'


# ═══════════════════════════════════════════════════════════════════════
# analyze() flow tests
# ═══════════════════════════════════════════════════════════════════════

class TestAnalyze:
    def _setup_analyzer_for_analyze(self, application=None):
        """Create an analyzer with mocked gather methods and LLM."""
        analyzer = _make_analyzer()

        analyzer._gather_conforma_blocker_data = MagicMock(return_value=[])
        analyzer._gather_pcc_freshness_data = MagicMock(return_value=None)
        analyzer._gather_exception_coverage_data = MagicMock(return_value={})
        analyzer._gather_snapshot_freshness_data = MagicMock(return_value={})
        analyzer._gather_nightly_status_data = MagicMock(return_value={})

        tool_input = _valid_tool_input()
        resp = _make_llm_response(tool_input)
        analyzer.llm.create_message.return_value = resp
        analyzer.llm.model_name.return_value = 'test-model'

        analyzer.langfuse.create_trace.return_value = MagicMock()

        return analyzer

    def test_full_flow(self):
        analyzer = self._setup_analyzer_for_analyze()

        result = analyzer.analyze(application='test-app')

        assert result['analyzed'] is True
        assert 'analysis' in result
        assert 'duration' in result
        assert 'cost_usd' in result
        assert 'model' in result
        assert result['model'] == 'test-model'
        assert result['analysis']['overall_severity'] == 'info'

        analyzer._gather_conforma_blocker_data.assert_called_once_with('test-app')
        analyzer._gather_pcc_freshness_data.assert_called_once()
        analyzer._gather_exception_coverage_data.assert_called_once_with('test-app')
        analyzer._gather_snapshot_freshness_data.assert_called_once_with('test-app')
        analyzer._gather_nightly_status_data.assert_called_once_with('test-app')

        analyzer.llm.create_message.assert_called_once()
        analyzer.langfuse.create_trace.assert_called_once()
        analyzer.langfuse.record_generation.assert_called_once()
        analyzer.langfuse.end_trace.assert_called_once()
        analyzer.langfuse.flush.assert_called_once()

    @patch.dict(os.environ, {'APPLICATION_NAME': 'env-app'}, clear=False)
    def test_uses_default_application(self):
        analyzer = self._setup_analyzer_for_analyze()

        result = analyzer.analyze()

        analyzer._gather_conforma_blocker_data.assert_called_once_with('env-app')

    def test_uses_provided_application(self):
        analyzer = self._setup_analyzer_for_analyze()

        result = analyzer.analyze(application='explicit-app')

        analyzer._gather_conforma_blocker_data.assert_called_once_with('explicit-app')

    def test_cost_calculation(self):
        analyzer = self._setup_analyzer_for_analyze()

        tool_input = _valid_tool_input()
        resp = _make_llm_response(tool_input, input_tokens=2000, output_tokens=1000)
        analyzer.llm.create_message.return_value = resp

        result = analyzer.analyze(application='test-app')

        expected_cost = (2000 * 0.000003) + (1000 * 0.000015)
        assert abs(result['cost_usd'] - expected_cost) < 1e-10


# ═══════════════════════════════════════════════════════════════════════
# run() tests
# ═══════════════════════════════════════════════════════════════════════

class TestRun:
    def test_delegates_to_analyze(self):
        analyzer = _make_analyzer()
        expected = {'analyzed': True, 'analysis': {}, 'duration': 1.0}
        analyzer.analyze = MagicMock(return_value=expected)

        result = analyzer.run()

        analyzer.analyze.assert_called_once_with(application=None)
        assert result is expected

    def test_passes_application_to_analyze(self):
        analyzer = _make_analyzer()
        analyzer.analyze = MagicMock(return_value={'analyzed': True})

        analyzer.run(application='custom-app')

        analyzer.analyze.assert_called_once_with(application='custom-app')

    def test_returns_analyze_result(self):
        analyzer = _make_analyzer()
        expected = {'analyzed': True, 'analysis': {'severity': 'info'}}
        analyzer.analyze = MagicMock(return_value=expected)

        result = analyzer.run(application='app')

        assert result == expected
