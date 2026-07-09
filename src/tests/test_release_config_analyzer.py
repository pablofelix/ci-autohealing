"""Tests for ReleaseConfigAnalyzer: prompt building, response parsing, data gathering."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ═══════════════════════════════════════════════════════════════════════
# Prompt building tests (pure function behavior)
# ═══════════════════════════════════════════════════════════════════════

class TestReleaseAnalysisPrompt:
    def _make_analyzer(self):
        from analyzers.release_config_analyzer import ReleaseConfigAnalyzer
        config = MagicMock()
        config.db = MagicMock()
        config.llm = MagicMock()
        return ReleaseConfigAnalyzer(
            config, db=MagicMock(), llm=MagicMock(), langfuse=MagicMock(),
        )

    def test_prompt_includes_conforma_section(self):
        analyzer = self._make_analyzer()
        gathered = {
            'application': 'rhoai-v3-5',
            'conforma_violations': [
                {'component': 'comp-a', 'rule': 'fips_check', 'count': 2},
            ],
            'pcc_freshness': None,
            'exception_coverage': {},
            'snapshot_freshness': {},
            'nightly_status': {},
        }
        _, user_prompt = analyzer.build_analysis_prompt(gathered)
        assert 'Conforma' in user_prompt or 'conforma' in user_prompt
        assert 'comp-a' in user_prompt

    def test_prompt_includes_pcc_section(self):
        analyzer = self._make_analyzer()
        gathered = {
            'application': 'rhoai-v3-5',
            'conforma_violations': [],
            'pcc_freshness': {
                'status': 'stale', 'missing_versions': ['v2.15.0'],
            },
            'exception_coverage': {},
            'snapshot_freshness': {},
            'nightly_status': {},
        }
        _, user_prompt = analyzer.build_analysis_prompt(gathered)
        assert 'PCC' in user_prompt or 'pcc' in user_prompt
        assert 'stale' in user_prompt

    def test_prompt_includes_nightly_section(self):
        analyzer = self._make_analyzer()
        gathered = {
            'application': 'rhoai-v3-5',
            'conforma_violations': [],
            'pcc_freshness': None,
            'exception_coverage': {},
            'snapshot_freshness': {},
            'nightly_status': {
                'fbc_health': {'current_status': 'Failed'},
                'blockers': [{'component': 'comp-x'}],
            },
        }
        _, user_prompt = analyzer.build_analysis_prompt(gathered)
        assert 'Nightly' in user_prompt or 'nightly' in user_prompt

    def test_prompt_empty_data_still_valid(self):
        analyzer = self._make_analyzer()
        gathered = {
            'application': 'rhoai-v3-5',
            'conforma_violations': [],
            'pcc_freshness': None,
            'exception_coverage': {},
            'snapshot_freshness': {},
            'nightly_status': {},
        }
        system, user = analyzer.build_analysis_prompt(gathered)
        assert len(system) > 0
        assert 'rhoai-v3-5' in user
        assert 'record_release_config_analysis' in user

    def test_prompt_includes_snapshot_freshness(self):
        analyzer = self._make_analyzer()
        gathered = {
            'application': 'rhoai-v3-5',
            'conforma_violations': [],
            'pcc_freshness': None,
            'exception_coverage': {},
            'snapshot_freshness': {
                'stale': [{'component': 'comp-a', 'reason': 'newer build exists'}],
            },
            'nightly_status': {},
        }
        _, user_prompt = analyzer.build_analysis_prompt(gathered)
        assert 'Snapshot' in user_prompt or 'snapshot' in user_prompt


# ═══════════════════════════════════════════════════════════════════════
# Response parsing tests
# ═══════════════════════════════════════════════════════════════════════

class TestParseReleaseAnalysisResponse:
    def _make_analyzer(self):
        from analyzers.release_config_analyzer import ReleaseConfigAnalyzer
        config = MagicMock()
        config.db = MagicMock()
        config.llm = MagicMock()
        return ReleaseConfigAnalyzer(
            config, db=MagicMock(), llm=MagicMock(), langfuse=MagicMock(),
        )

    def test_valid_tool_call(self):
        analyzer = self._make_analyzer()
        response = MagicMock()
        response.tool_calls = [{
            'name': 'record_release_config_analysis',
            'input': {
                'findings': [{
                    'title': 'PCC cache is stale after release',
                    'severity': 'warning',
                    'category': 'pcc_cache_stale',
                    'description': 'PCC not regenerated after last release cycle',
                    'recommendation': 'Run regen-pcc-cache workflow',
                }],
                'overall_severity': 'warning',
                'confidence_score': 0.8,
                'summary': 'Release config has 1 warning about PCC freshness',
                'release_blockers': [],
            },
        }]
        result = analyzer.parse_analysis_response(response)
        assert len(result['findings']) == 1
        assert result['release_blockers'] == []

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
            'name': 'record_release_config_analysis',
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
        assert result['release_blockers'] == []


# ═══════════════════════════════════════════════════════════════════════
# Data gathering tests (verify correct delegation)
# ═══════════════════════════════════════════════════════════════════════

class TestReleaseDataGathering:
    def _make_analyzer(self):
        from analyzers.release_config_analyzer import ReleaseConfigAnalyzer
        config = MagicMock()
        config.db = MagicMock()
        config.llm = MagicMock()
        db = MagicMock()
        return ReleaseConfigAnalyzer(
            config, db=db, llm=MagicMock(), langfuse=MagicMock(),
        ), db

    @patch('analyzers.release_config_analyzer.ConformaRepository')
    def test_gather_conforma_calls_repository(self, mock_repo_cls):
        analyzer, db = self._make_analyzer()
        mock_repo = MagicMock()
        mock_repo.get_violation_summaries.return_value = []
        mock_repo_cls.return_value = mock_repo

        result = analyzer._gather_conforma_blocker_data('test-app')
        mock_repo.get_violation_summaries.assert_called_once_with('test-app')
        assert isinstance(result, list)

    @patch('analyzers.release_config_analyzer.HealthMonitor')
    def test_gather_pcc_calls_health_monitor(self, mock_hm_cls):
        analyzer, db = self._make_analyzer()
        mock_hm = MagicMock()
        mock_hm._check_pcc_freshness.return_value = {
            'status': 'fresh', 'missing_versions': [],
        }
        mock_hm_cls.return_value = mock_hm

        result = analyzer._gather_pcc_freshness_data()
        mock_hm._check_pcc_freshness.assert_called_once()

    @patch('analyzers.release_config_analyzer.HealthMonitor')
    def test_gather_snapshot_calls_health_monitor(self, mock_hm_cls):
        analyzer, db = self._make_analyzer()
        mock_hm = MagicMock()
        mock_hm.check_snapshot_freshness.return_value = {
            'stale': [], 'fresh': [],
        }
        mock_hm_cls.return_value = mock_hm

        result = analyzer._gather_snapshot_freshness_data('test-app')
        mock_hm.check_snapshot_freshness.assert_called_once_with('test-app')

    @patch('analyzers.release_config_analyzer.HealthMonitor')
    def test_gather_nightly_calls_health_monitor(self, mock_hm_cls):
        analyzer, db = self._make_analyzer()
        mock_hm = MagicMock()
        mock_hm.get_nightly_status.return_value = {}
        mock_hm_cls.return_value = mock_hm

        result = analyzer._gather_nightly_status_data('test-app')
        mock_hm.get_nightly_status.assert_called_once_with('test-app')
