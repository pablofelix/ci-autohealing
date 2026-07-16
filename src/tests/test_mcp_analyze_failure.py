"""Tests for the analyze_failure MCP tool."""

import asyncio
import os
import sys
from datetime import datetime
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _stub_mcp():
    m = MagicMock()
    m.tool.return_value = lambda fn: fn
    return m


def _import_tools():
    import importlib

    import mcp_server.tools as mod
    importlib.reload(mod)
    return mod


@pytest.fixture(autouse=True)
def _patch_mcp(monkeypatch):
    stub = _stub_mcp()
    monkeypatch.setattr('mcp_server.mcp', stub)


def _run(coro_or_func, *args, **kwargs):
    result = coro_or_func(*args, **kwargs)
    if asyncio.iscoroutine(result):
        return asyncio.get_event_loop().run_until_complete(result)
    return result


def _mock_analysis_row():
    return {
        'analysis_type': 'build',
        'component_name': 'my-comp',
        'model_used': 'claude',
        'root_cause': 'infra issue',
        'failure_category': 'infrastructure',
        'confidence_score': 0.85,
        'recommended_fix': 'retry',
        'recommended_files': '[]',
        'fix_action_type': None,
        'can_auto_fix': False,
        'requires_human_review': True,
        'analyzed_at': datetime(2026, 7, 13, 10, 0),
        'langfuse_trace_url': None,
        'tokens_used': 1500,
        'cost_usd': 0.05,
        'analysis_json': None,
    }


class TestAnalyzeFailureTool:

    def test_returns_analysis_on_success(self, monkeypatch):
        tools = _import_tools()

        mock_analyzer_cls = MagicMock()
        mock_analyzer = MagicMock()
        mock_analyzer.run.return_value = {'analyzed': 1}
        mock_analyzer_cls.return_value = mock_analyzer

        mock_config = MagicMock()
        mock_config.llm = MagicMock()

        monkeypatch.setattr('config.CollectorConfig', MagicMock(from_env=lambda: mock_config))
        monkeypatch.setattr('analyzers.build_failure_analyzer.BuildFailureAnalyzer', mock_analyzer_cls)
        monkeypatch.setattr('clients.llm_provider.create_llm_provider', lambda x: MagicMock())

        ai_repo = MagicMock()
        ai_repo.get_analysis_by_component.return_value = _mock_analysis_row()
        monkeypatch.setattr('mcp_server.tools._ai_repo', lambda: ai_repo)
        monkeypatch.setattr('mcp_server.tools._db_connection', lambda: MagicMock())

        result = _run(tools.analyze_failure, 'my-comp', application='rhoai-v3-5')

        mock_analyzer.run.assert_called_once_with(
            component_filter='my-comp',
            force=False,
            application='rhoai-v3-5',
            limit=1,
        )
        assert hasattr(result, 'root_cause')
        assert result.root_cause == 'infra issue'

    def test_force_passes_through(self, monkeypatch):
        tools = _import_tools()

        mock_analyzer_cls = MagicMock()
        mock_analyzer = MagicMock()
        mock_analyzer.run.return_value = {'analyzed': 1}
        mock_analyzer_cls.return_value = mock_analyzer

        mock_config = MagicMock()
        mock_config.llm = MagicMock()

        monkeypatch.setattr('config.CollectorConfig', MagicMock(from_env=lambda: mock_config))
        monkeypatch.setattr('analyzers.build_failure_analyzer.BuildFailureAnalyzer', mock_analyzer_cls)
        monkeypatch.setattr('clients.llm_provider.create_llm_provider', lambda x: MagicMock())

        ai_repo = MagicMock()
        ai_repo.get_analysis_by_component.return_value = _mock_analysis_row()
        monkeypatch.setattr('mcp_server.tools._ai_repo', lambda: ai_repo)
        monkeypatch.setattr('mcp_server.tools._db_connection', lambda: MagicMock())

        _run(tools.analyze_failure, 'my-comp', application='rhoai-v3-5', force=True)

        mock_analyzer.run.assert_called_once_with(
            component_filter='my-comp',
            force=True,
            application='rhoai-v3-5',
            limit=1,
        )

    def test_returns_error_when_llm_not_configured(self, monkeypatch):
        tools = _import_tools()

        mock_config = MagicMock()
        mock_config.llm = None
        monkeypatch.setattr('config.CollectorConfig', MagicMock(from_env=lambda: mock_config))

        result = _run(tools.analyze_failure, 'my-comp')
        assert isinstance(result, dict)
        assert result['error'] == 'llm_not_configured'

    def test_returns_error_when_no_analysis_produced(self, monkeypatch):
        tools = _import_tools()

        mock_analyzer_cls = MagicMock()
        mock_analyzer = MagicMock()
        mock_analyzer.run.return_value = {'analyzed': 0}
        mock_analyzer_cls.return_value = mock_analyzer

        mock_config = MagicMock()
        mock_config.llm = MagicMock()

        monkeypatch.setattr('config.CollectorConfig', MagicMock(from_env=lambda: mock_config))
        monkeypatch.setattr('analyzers.build_failure_analyzer.BuildFailureAnalyzer', mock_analyzer_cls)
        monkeypatch.setattr('clients.llm_provider.create_llm_provider', lambda x: MagicMock())

        ai_repo = MagicMock()
        ai_repo.get_analysis_by_component.return_value = None
        monkeypatch.setattr('mcp_server.tools._ai_repo', lambda: ai_repo)
        monkeypatch.setattr('mcp_server.tools._db_connection', lambda: MagicMock())

        result = _run(tools.analyze_failure, 'missing-comp')
        assert isinstance(result, dict)
        assert result['error'] == 'no_analysis_produced'

    def test_returns_error_on_analyzer_exception(self, monkeypatch):
        tools = _import_tools()

        mock_analyzer_cls = MagicMock()
        mock_analyzer = MagicMock()
        mock_analyzer.run.side_effect = Exception('LLM timeout')
        mock_analyzer_cls.return_value = mock_analyzer

        mock_config = MagicMock()
        mock_config.llm = MagicMock()

        monkeypatch.setattr('config.CollectorConfig', MagicMock(from_env=lambda: mock_config))
        monkeypatch.setattr('analyzers.build_failure_analyzer.BuildFailureAnalyzer', mock_analyzer_cls)
        monkeypatch.setattr('clients.llm_provider.create_llm_provider', lambda x: MagicMock())
        monkeypatch.setattr('mcp_server.tools._db_connection', lambda: MagicMock())

        result = _run(tools.analyze_failure, 'my-comp')
        assert isinstance(result, dict)
        assert result['error'] == 'analysis_failed'
        assert 'LLM timeout' in result['message']


class TestAutoForceReAnalysis:

    def test_auto_forces_when_failure_newer_than_analysis(self, monkeypatch):
        tools = _import_tools()

        mock_analyzer_cls = MagicMock()
        mock_analyzer = MagicMock()
        mock_analyzer.run.return_value = {'analyzed': 1}
        mock_analyzer_cls.return_value = mock_analyzer

        mock_config = MagicMock()
        mock_config.llm = MagicMock()

        monkeypatch.setattr('config.CollectorConfig', MagicMock(from_env=lambda: mock_config))
        monkeypatch.setattr('analyzers.build_failure_analyzer.BuildFailureAnalyzer', mock_analyzer_cls)
        monkeypatch.setattr('clients.llm_provider.create_llm_provider', lambda x: MagicMock())
        monkeypatch.setattr('mcp_server.tools._db_connection', lambda: MagicMock())

        ai_repo = MagicMock()
        ai_repo.get_analysis_by_component.return_value = _mock_analysis_row()
        monkeypatch.setattr('mcp_server.tools._ai_repo', lambda: ai_repo)

        build_repo = MagicMock()
        build_repo.get_failure_details.return_value = {
            'last_updated_at': datetime(2026, 7, 15, 12, 0),
        }
        monkeypatch.setattr('mcp_server.tools._build_repo', lambda: build_repo)

        _run(tools.analyze_failure, 'my-comp', application='rhoai-v3-5')

        mock_analyzer.run.assert_called_once_with(
            component_filter='my-comp',
            force=True,
            application='rhoai-v3-5',
            limit=1,
        )

    def test_no_auto_force_when_analysis_is_fresh(self, monkeypatch):
        tools = _import_tools()

        mock_analyzer_cls = MagicMock()
        mock_analyzer = MagicMock()
        mock_analyzer.run.return_value = {'analyzed': 1}
        mock_analyzer_cls.return_value = mock_analyzer

        mock_config = MagicMock()
        mock_config.llm = MagicMock()

        monkeypatch.setattr('config.CollectorConfig', MagicMock(from_env=lambda: mock_config))
        monkeypatch.setattr('analyzers.build_failure_analyzer.BuildFailureAnalyzer', mock_analyzer_cls)
        monkeypatch.setattr('clients.llm_provider.create_llm_provider', lambda x: MagicMock())
        monkeypatch.setattr('mcp_server.tools._db_connection', lambda: MagicMock())

        ai_repo = MagicMock()
        ai_repo.get_analysis_by_component.return_value = _mock_analysis_row()
        monkeypatch.setattr('mcp_server.tools._ai_repo', lambda: ai_repo)

        build_repo = MagicMock()
        build_repo.get_failure_details.return_value = {
            'last_updated_at': datetime(2026, 7, 13, 8, 0),
        }
        monkeypatch.setattr('mcp_server.tools._build_repo', lambda: build_repo)

        _run(tools.analyze_failure, 'my-comp', application='rhoai-v3-5')

        mock_analyzer.run.assert_called_once_with(
            component_filter='my-comp',
            force=False,
            application='rhoai-v3-5',
            limit=1,
        )

    def test_no_auto_force_when_no_existing_analysis(self, monkeypatch):
        tools = _import_tools()

        mock_analyzer_cls = MagicMock()
        mock_analyzer = MagicMock()
        mock_analyzer.run.return_value = {'analyzed': 1}
        mock_analyzer_cls.return_value = mock_analyzer

        mock_config = MagicMock()
        mock_config.llm = MagicMock()

        monkeypatch.setattr('config.CollectorConfig', MagicMock(from_env=lambda: mock_config))
        monkeypatch.setattr('analyzers.build_failure_analyzer.BuildFailureAnalyzer', mock_analyzer_cls)
        monkeypatch.setattr('clients.llm_provider.create_llm_provider', lambda x: MagicMock())
        monkeypatch.setattr('mcp_server.tools._db_connection', lambda: MagicMock())

        ai_repo = MagicMock()
        ai_repo.get_analysis_by_component.return_value = None
        monkeypatch.setattr('mcp_server.tools._ai_repo', lambda: ai_repo)

        _run(tools.analyze_failure, 'my-comp', application='rhoai-v3-5')

        mock_analyzer.run.assert_called_once_with(
            component_filter='my-comp',
            force=False,
            application='rhoai-v3-5',
            limit=1,
        )

    def test_explicit_force_true_skips_auto_check(self, monkeypatch):
        tools = _import_tools()

        mock_analyzer_cls = MagicMock()
        mock_analyzer = MagicMock()
        mock_analyzer.run.return_value = {'analyzed': 1}
        mock_analyzer_cls.return_value = mock_analyzer

        mock_config = MagicMock()
        mock_config.llm = MagicMock()

        monkeypatch.setattr('config.CollectorConfig', MagicMock(from_env=lambda: mock_config))
        monkeypatch.setattr('analyzers.build_failure_analyzer.BuildFailureAnalyzer', mock_analyzer_cls)
        monkeypatch.setattr('clients.llm_provider.create_llm_provider', lambda x: MagicMock())
        monkeypatch.setattr('mcp_server.tools._db_connection', lambda: MagicMock())

        ai_repo = MagicMock()
        ai_repo.get_analysis_by_component.return_value = _mock_analysis_row()
        monkeypatch.setattr('mcp_server.tools._ai_repo', lambda: ai_repo)

        _run(tools.analyze_failure, 'my-comp', application='rhoai-v3-5', force=True)

        mock_analyzer.run.assert_called_once_with(
            component_filter='my-comp',
            force=True,
            application='rhoai-v3-5',
            limit=1,
        )
        # Only the post-analysis fetch call, no staleness-check call
        ai_repo.get_analysis_by_component.assert_called_once_with('my-comp', 'rhoai-v3-5', 'build')


class TestReviewAnalysisTool:

    def test_returns_review_result(self, monkeypatch):
        tools = _import_tools()

        ai_repo = MagicMock()
        ai_repo.get_analysis_by_component.return_value = _mock_analysis_row()
        monkeypatch.setattr('mcp_server.tools._ai_repo', lambda: ai_repo)

        build_repo = MagicMock()
        build_repo.get_failure_details.return_value = {
            'error_message': 'infra issue detected',
            'build_logs': 'Step build-container failed: infra issue in cluster node',
            'failed_step_name': 'build-container',
            'last_updated_at': datetime(2026, 7, 13, 8, 0),
        }
        monkeypatch.setattr('mcp_server.tools._build_repo', lambda: build_repo)

        result = _run(tools.review_analysis, 'my-comp', application='rhoai-v3-5')
        assert isinstance(result, dict)
        assert 'verdict' in result
        assert 'flags' in result
        assert 'summary' in result
        assert result['component'] == 'my-comp'

    def test_returns_error_when_no_analysis(self, monkeypatch):
        tools = _import_tools()

        ai_repo = MagicMock()
        ai_repo.get_analysis_by_component.return_value = None
        monkeypatch.setattr('mcp_server.tools._ai_repo', lambda: ai_repo)

        result = _run(tools.review_analysis, 'missing-comp')
        assert isinstance(result, dict)
        assert result['error'] == 'no_analysis'

    def test_returns_error_when_no_failure(self, monkeypatch):
        tools = _import_tools()

        ai_repo = MagicMock()
        ai_repo.get_analysis_by_component.return_value = _mock_analysis_row()
        monkeypatch.setattr('mcp_server.tools._ai_repo', lambda: ai_repo)

        build_repo = MagicMock()
        build_repo.get_failure_details.return_value = None
        monkeypatch.setattr('mcp_server.tools._build_repo', lambda: build_repo)

        result = _run(tools.review_analysis, 'my-comp')
        assert isinstance(result, dict)
        assert result['error'] == 'no_failure'

    def test_conforma_type_queries_conforma(self, monkeypatch):
        tools = _import_tools()

        ai_repo = MagicMock()
        ai_repo.get_analysis_by_component.return_value = None
        monkeypatch.setattr('mcp_server.tools._ai_repo', lambda: ai_repo)

        _run(tools.review_analysis, 'my-comp', application='rhoai-v3-5', type='conforma')
        ai_repo.get_analysis_by_component.assert_called_with('my-comp', 'rhoai-v3-5', 'conforma')
