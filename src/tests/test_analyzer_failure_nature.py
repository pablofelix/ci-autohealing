"""Tests that analyze_failure persists failure_nature to DB."""

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def analyzer():
    from analyzers.build_failure_analyzer import BuildFailureAnalyzer

    a = BuildFailureAnalyzer.__new__(BuildFailureAnalyzer)
    a.build_repo = MagicMock()
    a.ai_repo = MagicMock()
    a.langfuse = MagicMock()
    a.langfuse.create_trace.return_value = MagicMock()
    a.llm = MagicMock()
    a.pattern_service = MagicMock()
    a.config = MagicMock()
    a._github_client = None
    a.enrichment = MagicMock()

    a._ensure_context = MagicMock()
    a._ensure_enrichment = MagicMock()
    a._ensure_logs = MagicMock()
    a.build_analysis_prompt = MagicMock(return_value=('sys', 'usr'))
    a.parse_analysis_response = MagicMock(return_value={
        'failure_category': 'build_config',
        'confidence_score': 0.9,
        'root_cause': 'test',
        'recommended_fix': 'fix',
        'can_auto_fix': False,
        'requires_human_review': True,
        'recommended_files': [],
    })
    a._save_analysis = MagicMock()
    a._match_patterns = MagicMock()
    a.pattern_repo = MagicMock()

    resp = MagicMock()
    resp.input_tokens = 100
    resp.output_tokens = 50
    a.llm.create_message.return_value = resp
    return a


def _failure(fid, has_ever_succeeded):
    return {
        'id': fid,
        'component_name': 'test-comp',
        'pipelinerun_name': 'pr-1',
        'error_type': None,
        'enrichment_context': {
            'component_health': {'has_ever_succeeded': has_ever_succeeded},
        },
    }


def test_saves_structural_nature(analyzer):
    analyzer.analyze_failure(_failure(42, False))
    analyzer.build_repo.update_failure_nature.assert_called_once_with(42, 'structural')


def test_saves_unknown_nature(analyzer):
    analyzer.analyze_failure(_failure(99, True))
    analyzer.build_repo.update_failure_nature.assert_called_once_with(99, 'unknown')


def test_saves_unknown_when_no_health(analyzer):
    failure = {
        'id': 50,
        'component_name': 'comp',
        'pipelinerun_name': 'pr-2',
        'error_type': None,
        'enrichment_context': {},
    }
    analyzer.analyze_failure(failure)
    analyzer.build_repo.update_failure_nature.assert_called_once_with(50, 'unknown')
