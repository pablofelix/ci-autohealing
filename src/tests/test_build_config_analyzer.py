"""Tests for BuildConfigAnalyzer: prompt building, response parsing, data gathering."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ═══════════════════════════════════════════════════════════════════════
# Prompt building tests (pure function behavior)
# ═══════════════════════════════════════════════════════════════════════

class TestBuildAnalysisPrompt:
    def _make_analyzer(self):
        from analyzers.build_config_analyzer import BuildConfigAnalyzer
        config = MagicMock()
        config.db = MagicMock()
        config.llm = MagicMock()
        analyzer = BuildConfigAnalyzer(
            config, db=MagicMock(), llm=MagicMock(), langfuse=MagicMock(),
        )
        return analyzer

    def test_prompt_includes_stale_section(self):
        analyzer = self._make_analyzer()
        gathered = {
            'application': 'rhoai-v3-5',
            'stale_components': {
                'stale': [{'component': 'comp-a', 'days_behind': 5}],
                'stale_count': 1,
            },
            'recurring_transient': [],
            'build_durations': {},
            'webhook_issues': [],
            'build_chain': [],
            'repeat_failures': [],
        }
        _, user_prompt = analyzer.build_analysis_prompt(gathered)
        assert 'Stale Components' in user_prompt
        assert 'comp-a' in user_prompt

    def test_prompt_includes_recurring_section(self):
        analyzer = self._make_analyzer()
        gathered = {
            'application': 'rhoai-v3-5',
            'stale_components': {'stale': [], 'stale_count': 0},
            'recurring_transient': [
                {'component': 'comp-b', 'failure_count': 5,
                 'error_type': 'signing_error'},
            ],
            'build_durations': {},
            'webhook_issues': [],
            'build_chain': [],
            'repeat_failures': [],
        }
        _, user_prompt = analyzer.build_analysis_prompt(gathered)
        assert 'Recurring Transient' in user_prompt
        assert 'comp-b' in user_prompt

    def test_prompt_empty_data_still_valid(self):
        analyzer = self._make_analyzer()
        gathered = {
            'application': 'rhoai-v3-5',
            'stale_components': {'stale': [], 'stale_count': 0},
            'recurring_transient': [],
            'build_durations': {},
            'webhook_issues': [],
            'build_chain': [],
            'repeat_failures': [],
        }
        system, user = analyzer.build_analysis_prompt(gathered)
        assert len(system) > 0
        assert 'rhoai-v3-5' in user
        assert 'record_build_config_analysis' in user

    def test_prompt_includes_webhook_section(self):
        analyzer = self._make_analyzer()
        gathered = {
            'application': 'rhoai-v3-5',
            'stale_components': {'stale': [], 'stale_count': 0},
            'recurring_transient': [],
            'build_durations': {},
            'webhook_issues': [
                {'component': 'comp-c', 'cause': 'missing_pac_repository'},
            ],
            'build_chain': [],
            'repeat_failures': [],
        }
        _, user_prompt = analyzer.build_analysis_prompt(gathered)
        assert 'Webhook' in user_prompt or 'webhook' in user_prompt
        assert 'comp-c' in user_prompt

    def test_prompt_includes_build_chain_section(self):
        analyzer = self._make_analyzer()
        gathered = {
            'application': 'rhoai-v3-5',
            'stale_components': {'stale': [], 'stale_count': 0},
            'recurring_transient': [],
            'build_durations': {},
            'webhook_issues': [],
            'build_chain': [
                {'upstream': 'comp-a', 'downstream': ['comp-b', 'comp-c']},
            ],
            'repeat_failures': [],
        }
        _, user_prompt = analyzer.build_analysis_prompt(gathered)
        assert 'Build Chain' in user_prompt or 'chain' in user_prompt


# ═══════════════════════════════════════════════════════════════════════
# Response parsing tests
# ═══════════════════════════════════════════════════════════════════════

class TestParseAnalysisResponse:
    def _make_analyzer(self):
        from analyzers.build_config_analyzer import BuildConfigAnalyzer
        config = MagicMock()
        config.db = MagicMock()
        config.llm = MagicMock()
        return BuildConfigAnalyzer(
            config, db=MagicMock(), llm=MagicMock(), langfuse=MagicMock(),
        )

    def test_valid_tool_call(self):
        analyzer = self._make_analyzer()
        response = MagicMock()
        response.tool_calls = [{
            'name': 'record_build_config_analysis',
            'input': {
                'findings': [{
                    'title': 'Stale nudge for odh-dashboard component',
                    'severity': 'warning',
                    'category': 'stale_component',
                    'description': 'Component building from commits 5 days old',
                    'recommendation': 'Fix nudge propagation for component',
                }],
                'overall_severity': 'warning',
                'confidence_score': 0.85,
                'summary': 'Build config audit found 1 stale component',
                'auto_rebuild_candidates': [],
            },
        }]
        result = analyzer.parse_analysis_response(response)
        assert len(result['findings']) == 1
        assert result['overall_severity'] == 'warning'
        assert result['confidence_score'] == 0.85

    def test_no_tool_calls_raises(self):
        analyzer = self._make_analyzer()
        response = MagicMock()
        response.tool_calls = []
        with pytest.raises(ValueError, match="did not return"):
            analyzer.parse_analysis_response(response)

    def test_wrong_tool_name_raises(self):
        analyzer = self._make_analyzer()
        response = MagicMock()
        response.tool_calls = [{'name': 'wrong_tool', 'input': {}}]
        with pytest.raises(ValueError, match="did not call"):
            analyzer.parse_analysis_response(response)

    def test_validation_error_returns_fallback(self):
        analyzer = self._make_analyzer()
        response = MagicMock()
        response.tool_calls = [{
            'name': 'record_build_config_analysis',
            'input': {
                'findings': [],
                'overall_severity': 'INVALID',
                'confidence_score': 999,
                'summary': 'bad',
            },
        }]
        result = analyzer.parse_analysis_response(response)
        assert result['confidence_score'] == 0.0
        assert result['findings'] == []


# ═══════════════════════════════════════════════════════════════════════
# Data gathering tests (verify correct delegation)
# ═══════════════════════════════════════════════════════════════════════

class TestDataGathering:
    def _make_analyzer(self):
        from analyzers.build_config_analyzer import BuildConfigAnalyzer
        config = MagicMock()
        config.db = MagicMock()
        config.llm = MagicMock()
        db = MagicMock()
        return BuildConfigAnalyzer(
            config, db=db, llm=MagicMock(), langfuse=MagicMock(),
        ), db

    @patch('analyzers.build_config_analyzer.HealthMonitor')
    def test_gather_stale_calls_health_monitor(self, mock_hm_cls):
        analyzer, db = self._make_analyzer()
        mock_hm = MagicMock()
        mock_hm.get_stale_components.return_value = {
            'stale': [], 'stale_count': 0, 'skipped': 0,
        }
        mock_hm_cls.return_value = mock_hm

        result = analyzer._gather_stale_component_data('test-app')
        mock_hm.get_stale_components.assert_called_once_with(
            'test-app', diagnose=True,
        )
        assert 'stale' in result

    @patch('analyzers.build_config_analyzer.HealthMonitor')
    def test_gather_stale_handles_error(self, mock_hm_cls):
        analyzer, db = self._make_analyzer()
        mock_hm = MagicMock()
        mock_hm.get_stale_components.side_effect = Exception("k8s down")
        mock_hm_cls.return_value = mock_hm

        result = analyzer._gather_stale_component_data('test-app')
        assert 'error' in result

    @patch('analyzers.build_config_analyzer.HealthMonitor')
    def test_gather_repeat_failures_calls_health_monitor(self, mock_hm_cls):
        analyzer, db = self._make_analyzer()
        mock_hm = MagicMock()
        mock_hm.get_repeat_failures.return_value = []
        mock_hm_cls.return_value = mock_hm

        result = analyzer._gather_repeat_failure_data()
        mock_hm.get_repeat_failures.assert_called_once()
        assert isinstance(result, list)
