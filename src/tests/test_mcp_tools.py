"""Tests for MCP server tools.

Tests the synchronous logic inside each tool by mocking repository helpers
and lazy imports.  The ``@async_tool`` decorator is bypassed by calling the
inner sync function via ``__wrapped__`` when available, or by running the
async version with ``asyncio.run()``.

We do NOT import ``mcp_server.tools`` at the top level because that would
pull in ``mcp_server.mcp`` which tries to initialise an MCP server.
Instead we patch ``mcp_server.mcp`` with a stub before importing.
"""

import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stub_mcp():
    """Return a MagicMock that acts like a FastMCP instance."""
    m = MagicMock()
    # ``@mcp.tool()`` should be a passthrough decorator.
    m.tool.return_value = lambda fn: fn
    return m


def _import_tools():
    """Import ``mcp_server.tools`` with a stubbed MCP server.

    Must be called AFTER patching ``mcp_server.mcp``.
    """
    import importlib

    import mcp_server.tools as mod
    importlib.reload(mod)
    return mod


@pytest.fixture(autouse=True)
def _patch_mcp(monkeypatch):
    """Patch the ``mcp`` object inside mcp_server so tools can be imported."""
    stub = _stub_mcp()
    monkeypatch.setattr('mcp_server.mcp', stub)


def _run(coro_or_func, *args, **kwargs):
    """Run an async_tool-decorated function synchronously.

    Works for both the raw sync function and the async wrapper.
    """
    result = coro_or_func(*args, **kwargs)
    if asyncio.iscoroutine(result):
        return asyncio.get_event_loop().run_until_complete(result)
    return result


def _now():
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# _extract_from_analysis_json
# ---------------------------------------------------------------------------

class TestExtractFromAnalysisJson:
    """Unit tests for the ``_extract_from_analysis_json`` helper."""

    def _fn(self):
        from mcp_server.tools import _extract_from_analysis_json
        return _extract_from_analysis_json

    def test_none_input(self):
        evidence, transparency, fix_type = self._fn()(None)
        assert evidence == []
        assert transparency is None
        assert fix_type is None

    def test_empty_dict(self):
        evidence, transparency, fix_type = self._fn()({})
        assert evidence == []
        assert transparency is None
        assert fix_type is None

    def test_empty_list(self):
        evidence, transparency, fix_type = self._fn()([])
        assert evidence == []

    def test_dict_with_tool_calls(self):
        data = {
            'tool_calls': [
                {
                    'input': {
                        'evidence_references': [
                            {'type': 'log', 'url': 'http://x', 'description': 'build log'}
                        ],
                        'source_transparency': {
                            'sources_consulted': ['logs'],
                            'sources_unavailable': [],
                            'limitations': [],
                        },
                        'fix_action_type': 'rebuild',
                    }
                }
            ]
        }
        evidence, transparency, fix_type = self._fn()(data)
        assert len(evidence) == 1
        assert evidence[0]['type'] == 'log'
        assert transparency is not None
        assert transparency['sources_consulted'] == ['logs']
        assert fix_type == 'rebuild'

    def test_list_of_items(self):
        data = [
            {
                'input': {
                    'evidence_references': [
                        {'type': 'commit', 'url': '', 'description': 'sha abc123'}
                    ],
                    'fix_action_type': 'file_change',
                }
            }
        ]
        evidence, transparency, fix_type = self._fn()(data)
        assert len(evidence) == 1
        assert fix_type == 'file_change'
        assert transparency is None

    def test_json_string_input(self):
        data = json.dumps({'tool_calls': [{'input': {'evidence_references': []}}]})
        evidence, transparency, fix_type = self._fn()(data)
        assert evidence == []

    def test_invalid_json_string(self):
        evidence, transparency, fix_type = self._fn()("not json")
        assert evidence == []
        assert transparency is None

    def test_non_dict_items_skipped(self):
        data = [42, "text", None, {'input': {'fix_action_type': 'rebuild'}}]
        evidence, transparency, fix_type = self._fn()(data)
        assert fix_type == 'rebuild'


# ---------------------------------------------------------------------------
# _row_to_analysis
# ---------------------------------------------------------------------------

class TestRowToAnalysis:
    """Unit tests for the ``_row_to_analysis`` helper."""

    def _fn(self):
        from mcp_server.tools import _row_to_analysis
        return _row_to_analysis

    def _minimal_row(self):
        return {
            'analysis_type': 'build',
            'component_name': 'my-comp',
            'model_used': 'claude-opus',
            'root_cause': 'missing dep',
            'failure_category': 'dependency_issue',
            'confidence_score': 0.85,
            'recommended_fix': 'add dep',
            'recommended_files': None,
            'can_auto_fix': False,
            'requires_human_review': True,
            'analyzed_at': _now(),
            'langfuse_trace_url': None,
            'tokens_used': 100,
            'cost_usd': 0.01,
            'analysis_json': None,
        }

    def test_minimal_row(self):
        row = self._minimal_row()
        result = self._fn()(row)
        assert result.component == 'my-comp'
        assert result.type == 'build'
        assert result.confidence_score == 0.85
        assert result.recommended_files == []
        assert result.evidence_references == []
        assert result.source_transparency is None

    def test_recommended_files_as_csv_string(self):
        row = self._minimal_row()
        row['recommended_files'] = 'file_a.py, file_b.go, '
        result = self._fn()(row)
        assert result.recommended_files == ['file_a.py', 'file_b.go']

    def test_recommended_files_as_list(self):
        row = self._minimal_row()
        row['recommended_files'] = ['a.py', 'b.py']
        result = self._fn()(row)
        assert result.recommended_files == ['a.py', 'b.py']

    def test_full_row_with_analysis_json(self):
        row = self._minimal_row()
        row['analysis_json'] = {
            'tool_calls': [{
                'input': {
                    'evidence_references': [
                        {'type': 'log', 'url': 'http://x', 'description': 'd'}
                    ],
                    'source_transparency': {
                        'sources_consulted': ['logs'],
                        'sources_unavailable': [],
                        'limitations': [],
                    },
                    'fix_action_type': 'rebuild',
                }
            }]
        }
        result = self._fn()(row)
        assert len(result.evidence_references) == 1
        assert result.source_transparency is not None
        assert result.fix_action_type == 'rebuild'

    def test_zero_confidence(self):
        row = self._minimal_row()
        row['confidence_score'] = None
        result = self._fn()(row)
        assert result.confidence_score == 0.0

    def test_zero_cost(self):
        row = self._minimal_row()
        row['cost_usd'] = None
        result = self._fn()(row)
        assert result.cost_usd == 0.0


# ---------------------------------------------------------------------------
# list_applications
# ---------------------------------------------------------------------------

class TestListApplications:

    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_happy_path(self, mock_build, mock_conforma):
        mock_build.return_value.get_applications.return_value = [
            {'application': 'rhoai-v3-5'},
            {'application': 'rhoai-v3-6'},
        ]
        mock_build.return_value.get_overview_stats.side_effect = [
            {'components': 10, 'total': 3},
            {'components': 20, 'total': 0},
        ]
        mock_conforma.return_value.find_unresolved_component_names.side_effect = [
            ['comp-a', 'comp-b'],
            [],
        ]
        from mcp_server.tools import list_applications
        result = _run(list_applications)

        assert len(result) == 2
        assert result[0].name == 'rhoai-v3-5'
        assert result[0].failure_count == 3
        assert result[0].conforma_count == 2
        assert result[1].conforma_count == 0

    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_empty(self, mock_build, mock_conforma):
        mock_build.return_value.get_applications.return_value = []
        from mcp_server.tools import list_applications
        result = _run(list_applications)
        assert result == []


# ---------------------------------------------------------------------------
# get_failure
# ---------------------------------------------------------------------------

class TestGetFailure:

    @patch('mcp_server.tools._build_repo')
    def test_not_found(self, mock_build):
        mock_build.return_value.get_failure_details.return_value = None
        from mcp_server.tools import get_failure
        result = _run(get_failure, 'no-such-comp')
        assert result is None

    @patch('mcp_server.tools._build_repo')
    def test_happy_path(self, mock_build):
        row = {
            'component_name': 'my-comp',
            'pipelinerun_name': 'pr-abc',
            'error_message': 'exit code 1',
            'error_type': 'build_error',
            'failed_task_name': 'build',
            'failed_step_name': 'compile',
            'build_logs': 'ERROR: something failed\nINFO: ok\n',
            'commit_sha': 'abc123',
            'commit_message': 'fix stuff',
            'commit_author': 'dev',
            'commit_url': 'http://github.com/c/abc123',
            'repository_url': 'http://github.com/org/repo',
            'branch': 'rhoai-v3.5',
            'commit_context': {'dockerfile': 'FROM ubi'},
            'konflux_url': 'http://konflux/pr-abc',
            'first_detected_at': _now(),
        }
        mock_build.return_value.get_failure_details.return_value = row

        with patch('utils.log_filter.filter_error_lines', return_value='ERROR: something failed'):
            from mcp_server.tools import get_failure
            result = _run(get_failure, 'my-comp', include_logs=True, include_commit_context=True)

        assert result is not None
        assert result.component == 'my-comp'
        assert result.error_message == 'exit code 1'
        assert result.build_logs == 'ERROR: something failed'
        assert result.commit_context == {'dockerfile': 'FROM ubi'}

    @patch('mcp_server.tools._build_repo')
    def test_exclude_logs_and_context(self, mock_build):
        row = {
            'component_name': 'my-comp',
            'pipelinerun_name': 'pr-abc',
            'error_message': 'fail',
            'error_type': None,
            'failed_task_name': None,
            'failed_step_name': None,
            'build_logs': 'big log data',
            'commit_sha': None,
            'commit_message': None,
            'commit_author': None,
            'commit_url': None,
            'repository_url': None,
            'branch': None,
            'commit_context': {'some': 'data'},
            'konflux_url': None,
            'first_detected_at': _now(),
        }
        mock_build.return_value.get_failure_details.return_value = row

        from mcp_server.tools import get_failure
        result = _run(get_failure, 'my-comp', include_logs=False, include_commit_context=False)

        assert result.build_logs is None
        assert result.commit_context is None


# ---------------------------------------------------------------------------
# get_violation
# ---------------------------------------------------------------------------

class TestGetViolation:

    @patch('mcp_server.tools._conforma_repo')
    def test_not_found(self, mock_conforma):
        mock_conforma.return_value.get_violation_details.return_value = None
        from mcp_server.tools import get_violation
        result = _run(get_violation, 'no-such')
        assert result is None

    @patch('mcp_server.tools._conforma_repo')
    def test_happy_path(self, mock_conforma):
        row = {
            'component_name': 'comp-a',
            'scenario': 'rhoai-v3-5-verify-conforma-stage',
            'violations_count': 5,
            'warnings_count': 2,
            'successes_count': 100,
            'violation_summary': 'FAIL: hermetic_task.hermetic\nFAIL: trusted_task.trusted',
            'violation_details': {'k': 'v'},
            'repository_url': 'http://github.com/org/repo',
            'commit_sha': 'abc',
            'snapshot_name': 'snap-1',
            'pipelinerun_name': 'pr-xyz',
            'first_detected_at': _now(),
        }
        mock_conforma.return_value.get_violation_details.return_value = row

        with patch('utils.conforma_utils.fetch_exceptions_by_policy', return_value={}), \
             patch('utils.conforma_utils.extract_policy_from_scenario', return_value='stage'), \
             patch('utils.conforma_utils.policy_url', return_value='http://policy'), \
             patch('utils.conforma_utils.count_unique_violations', return_value=(2, [
                 {'rule': 'hermetic_task.hermetic', 'detail': ''},
                 {'rule': 'trusted_task.trusted', 'detail': ''},
             ])), \
             patch('utils.conforma_utils.extract_violation_rules', return_value=[]), \
             patch('utils.conforma_utils.compute_violation_coverage', return_value=None), \
             patch('utils.conforma_utils.lookup_exceptions', return_value=[]):
            from mcp_server.tools import get_violation
            result = _run(get_violation, 'comp-a')

        assert result is not None
        assert result.component == 'comp-a'
        assert result.violations_count == 5
        assert result.unique_violations == 2
        assert len(result.violation_rules) == 2


# ---------------------------------------------------------------------------
# get_analysis
# ---------------------------------------------------------------------------

class TestGetAnalysis:

    @patch('mcp_server.tools._ai_repo')
    def test_not_found(self, mock_ai):
        mock_ai.return_value.get_analysis_by_component.return_value = None
        from mcp_server.tools import get_analysis
        result = _run(get_analysis, 'no-such')
        assert result is None

    @patch('mcp_server.tools._ai_repo')
    def test_found(self, mock_ai):
        mock_ai.return_value.get_analysis_by_component.return_value = {
            'analysis_type': 'build',
            'component_name': 'comp-x',
            'model_used': 'claude',
            'root_cause': 'deps',
            'failure_category': 'dependency_issue',
            'confidence_score': 0.9,
            'recommended_fix': 'update deps',
            'recommended_files': ['go.mod'],
            'can_auto_fix': True,
            'requires_human_review': False,
            'analyzed_at': _now(),
            'langfuse_trace_url': None,
            'tokens_used': 500,
            'cost_usd': 0.05,
            'analysis_json': None,
        }
        from mcp_server.tools import get_analysis
        result = _run(get_analysis, 'comp-x', type='build')
        assert result.component == 'comp-x'
        assert result.can_auto_fix is True


# ---------------------------------------------------------------------------
# submit_analysis
# ---------------------------------------------------------------------------

class TestSubmitAnalysis:

    @patch('mcp_server.tools._ai_repo')
    @patch('mcp_server.tools._build_repo')
    def test_component_not_found(self, mock_build, mock_ai):
        mock_build.return_value.get_failure_details.return_value = None
        from mcp_server.tools import submit_analysis
        result = submit_analysis(
            component='ghost', root_cause='x', failure_category='y',
            confidence_score=0.5, recommended_fix='z',
        )
        assert 'error' in result
        assert 'ghost' in result['error']

    @patch('mcp_server.tools._ai_repo')
    @patch('mcp_server.tools._build_repo')
    def test_no_failure_id(self, mock_build, mock_ai):
        mock_build.return_value.get_failure_details.return_value = {'id': None}
        from mcp_server.tools import submit_analysis
        result = submit_analysis(
            component='c', root_cause='x', failure_category='y',
            confidence_score=0.5, recommended_fix='z',
        )
        assert result == {'error': 'Failure has no ID'}

    @patch('mcp_server.tools._ai_repo')
    @patch('mcp_server.tools._build_repo')
    def test_success_build(self, mock_build, mock_ai):
        mock_build.return_value.get_failure_details.return_value = {'id': 42}
        mock_ai.return_value.insert_analysis.return_value = 99
        from mcp_server.tools import submit_analysis
        result = submit_analysis(
            component='comp', root_cause='dep missing', failure_category='dependency',
            confidence_score=0.8, recommended_fix='add dep',
            analysis_type='build',
        )
        assert result['action'] == 'stored'
        assert result['analysis_id'] == 99
        mock_ai.return_value.insert_analysis.assert_called_once()
        call_kwargs = mock_ai.return_value.insert_analysis.call_args[1]
        assert call_kwargs['build_failure_id'] == 42
        assert call_kwargs['conforma_result_id'] is None

    @patch('mcp_server.tools._ai_repo')
    @patch('mcp_server.tools._build_repo')
    def test_success_conforma(self, mock_build, mock_ai):
        mock_build.return_value.get_failure_details.return_value = {'id': 10}
        mock_ai.return_value.insert_analysis.return_value = 55
        from mcp_server.tools import submit_analysis
        submit_analysis(
            component='c', root_cause='r', failure_category='policy',
            confidence_score=0.7, recommended_fix='f',
            analysis_type='conforma',
        )
        call_kwargs = mock_ai.return_value.insert_analysis.call_args[1]
        assert call_kwargs['build_failure_id'] is None
        assert call_kwargs['conforma_result_id'] == 10


# ---------------------------------------------------------------------------
# search_failures
# ---------------------------------------------------------------------------

class TestSearchFailures:

    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_mixed_results(self, mock_build, mock_conforma):
        now = _now()
        mock_build.return_value.get_triage_summary.return_value = {
            'failing_components': [
                {
                    'component': 'build-comp',
                    'status': 'Failed',
                    'error_type': 'build_error',
                    'first_detected_at': now - timedelta(hours=2),
                    'last_updated_at': now,
                    'failure_count': 3,
                    'has_logs': True,
                    'ai_analyzed': False,
                },
            ],
        }
        mock_conforma.return_value.find_unresolved_component_names.return_value = ['conforma-comp']
        mock_conforma.return_value.get_violation_details.return_value = {
            'scenario': 'verify-conforma',
            'first_detected_at': now - timedelta(hours=1),
            'last_updated_at': now,
            'violation_summary': 'FAIL: rule.x',
            'ai_analyzed': False,
        }
        from mcp_server.tools import search_failures
        result = _run(search_failures, limit=10)
        assert len(result) == 2
        statuses = {r.component: r.status for r in result}
        assert statuses['build-comp'] == 'Failed'
        assert statuses['conforma-comp'] == 'Conforma Violation'

    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_filter_has_analysis_true(self, mock_build, mock_conforma):
        now = _now()
        mock_build.return_value.get_triage_summary.return_value = {
            'failing_components': [
                {
                    'component': 'analyzed', 'status': 'Failed',
                    'first_detected_at': now, 'last_updated_at': now,
                    'failure_count': 1, 'has_logs': True, 'ai_analyzed': True,
                },
                {
                    'component': 'not-analyzed', 'status': 'Failed',
                    'first_detected_at': now, 'last_updated_at': now,
                    'failure_count': 1, 'has_logs': True, 'ai_analyzed': False,
                },
            ],
        }
        mock_conforma.return_value.find_unresolved_component_names.return_value = []
        from mcp_server.tools import search_failures
        result = _run(search_failures, has_analysis=True)
        assert len(result) == 1
        assert result[0].component == 'analyzed'

    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_limit_applied(self, mock_build, mock_conforma):
        now = _now()
        comps = [
            {
                'component': f'comp-{i}', 'status': 'Failed',
                'first_detected_at': now - timedelta(hours=i),
                'last_updated_at': now - timedelta(hours=i),
                'failure_count': 1, 'has_logs': False, 'ai_analyzed': False,
            }
            for i in range(5)
        ]
        mock_build.return_value.get_triage_summary.return_value = {'failing_components': comps}
        mock_conforma.return_value.find_unresolved_component_names.return_value = []
        from mcp_server.tools import search_failures
        result = _run(search_failures, limit=3)
        assert len(result) == 3


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------

class TestGetStats:

    @patch('mcp_server.tools._ai_repo')
    def test_happy_path(self, mock_ai):
        mock_ai.return_value.get_extended_status.return_value = {
            'build': {'pending': 5, 'analyzed': 10, 'auto_fixable': 2},
            'conforma': {'pending': 3, 'analyzed': 7, 'auto_fixable': 1},
            'total_cost': 1.23,
        }
        mock_ai.return_value.get_recent_analyses.return_value = [
            {'component': 'x', 'category': 'build_error'}
        ]
        from mcp_server.tools import get_stats
        result = _run(get_stats)
        assert result.build_failures['pending'] == 5
        assert result.conforma_violations['analyzed'] == 7
        assert result.total_cost_30d == 1.23
        assert len(result.recent_analyses) == 1


# ---------------------------------------------------------------------------
# get_triage
# ---------------------------------------------------------------------------

class TestGetTriage:

    @patch('mcp_server.tools._build_repo')
    def test_happy_path(self, mock_build):
        mock_build.return_value.get_triage_summary.return_value = {
            'total': 50, 'failing': 5, 'working': 45,
            'failing_components': [{'component': 'a'}, {'component': 'b'}],
        }
        from mcp_server.tools import get_triage
        result = _run(get_triage)
        assert result.total == 50
        assert result.failing == 5
        assert result.working == 45
        assert len(result.failing_components) == 2


# ---------------------------------------------------------------------------
# get_triage_report
# ---------------------------------------------------------------------------

class TestGetTriageReport:

    @patch('mcp_server.tools._triage_repo')
    def test_happy_path(self, mock_triage):
        mock_triage.return_value.get_report.return_value = [
            {'id': 1, 'component': 'c', 'status': 'active'}
        ]
        mock_triage.return_value.get_summary.return_value = {
            'active': 1, 'resolved': 0
        }
        from mcp_server.tools import get_triage_report
        result = _run(get_triage_report, application='rhoai-v3-5', date='2026-07-01')
        assert result['application'] == 'rhoai-v3-5'
        assert len(result['items']) == 1
        assert result['summary']['active'] == 1


# ---------------------------------------------------------------------------
# track_triage_item
# ---------------------------------------------------------------------------

class TestTrackTriageItem:

    @patch('mcp_server.tools._triage_repo')
    def test_create_new(self, mock_triage):
        mock_triage.return_value.find_by_component.return_value = None
        mock_triage.return_value.create_item.return_value = 77
        from mcp_server.tools import track_triage_item
        result = _run(track_triage_item, 'comp-x', group_label='Go issue')
        assert result['action'] == 'created'
        assert result['item_id'] == 77

    @patch('mcp_server.tools._triage_repo')
    def test_already_exists(self, mock_triage):
        mock_triage.return_value.find_by_component.return_value = {
            'id': 5, 'component': 'comp-x', 'status': 'active'
        }
        from mcp_server.tools import track_triage_item
        result = _run(track_triage_item, 'comp-x')
        assert result['action'] == 'exists'

    @patch('mcp_server.tools._triage_repo')
    def test_add_to_existing(self, mock_triage):
        mock_triage.return_value.get_by_id.return_value = {
            'id': 10, 'components': ['comp-a'], 'status': 'active'
        }
        from mcp_server.tools import track_triage_item
        result = _run(track_triage_item, 'comp-b', add_to_id=10,
                      jira_key='RHOAI-123', slack_thread_url='http://slack/t')
        assert result['action'] == 'added'
        assert result['item_id'] == 10
        mock_triage.return_value.update_item.assert_called_once()
        mock_triage.return_value.add_slack_url.assert_called_once_with(10, 'http://slack/t')

    @patch('mcp_server.tools._triage_repo')
    def test_add_to_nonexistent(self, mock_triage):
        mock_triage.return_value.get_by_id.return_value = None
        from mcp_server.tools import track_triage_item
        result = _run(track_triage_item, 'comp-x', add_to_id=999)
        assert 'error' in result


# ---------------------------------------------------------------------------
# update_triage_item
# ---------------------------------------------------------------------------

class TestUpdateTriageItem:

    @patch('mcp_server.tools._triage_repo')
    def test_not_found(self, mock_triage):
        mock_triage.return_value.get_by_id.return_value = None
        from mcp_server.tools import update_triage_item
        result = _run(update_triage_item, item_id=999)
        assert 'error' in result

    @patch('mcp_server.tools._triage_repo')
    def test_resolve(self, mock_triage):
        mock_triage.return_value.get_by_id.return_value = {'id': 1}
        from mcp_server.tools import update_triage_item
        result = _run(update_triage_item, item_id=1, status='resolved',
                      resolution='fixed', resolution_pr_url='http://pr')
        assert result['action'] == 'resolved'
        mock_triage.return_value.resolve_item.assert_called_once_with(
            1, resolution='fixed', pr_url='http://pr',
        )

    @patch('mcp_server.tools._triage_repo')
    def test_update_fields(self, mock_triage):
        mock_triage.return_value.get_by_id.return_value = {'id': 2}
        from mcp_server.tools import update_triage_item
        result = _run(update_triage_item, item_id=2, jira_key='J-1', notes='note')
        assert result['action'] == 'updated'
        assert 'jira_key' in result['updates']
        assert 'notes' in result['updates']

    @patch('mcp_server.tools._triage_repo')
    def test_add_slack_url(self, mock_triage):
        mock_triage.return_value.get_by_id.return_value = {'id': 3}
        from mcp_server.tools import update_triage_item
        result = _run(update_triage_item, item_id=3, slack_thread_url='http://s')
        assert result['action'] == 'updated'
        mock_triage.return_value.add_slack_url.assert_called_once_with(3, 'http://s')

    @patch('mcp_server.tools._triage_repo')
    def test_no_updates(self, mock_triage):
        mock_triage.return_value.get_by_id.return_value = {'id': 4}
        from mcp_server.tools import update_triage_item
        result = _run(update_triage_item, item_id=4)
        assert 'error' in result
        assert 'No updates' in result['error']


# ---------------------------------------------------------------------------
# resolve_triage_item
# ---------------------------------------------------------------------------

class TestResolveTriageItem:

    @patch('mcp_server.tools._triage_repo')
    def test_basic_resolve(self, mock_triage):
        from mcp_server.tools import resolve_triage_item
        result = resolve_triage_item(item_id=1, resolution='done')
        assert result['action'] == 'resolved'
        mock_triage.return_value.resolve_item.assert_called_once_with(
            1, resolution='done', pr_url='',
        )

    @patch('mcp_server.tools._db_connection')
    @patch('mcp_server.tools._triage_repo')
    def test_with_verdict(self, mock_triage, mock_db):
        mock_triage.return_value.get_by_id.return_value = {
            'id': 2, 'components': ['comp-a'],
        }
        with patch('repositories.ai_analysis_repository.AIAnalysisRepository') as mock_ai_cls:
            from mcp_server.tools import resolve_triage_item
            result = resolve_triage_item(item_id=2, verdict='correct',
                                         application='rhoai-v3-5')
        assert result['action'] == 'resolved'
        mock_ai_cls.return_value.record_verdict.assert_called_once_with(
            'comp-a', 'rhoai-v3-5', 'correct', verdict_by='mcp',
        )


# ---------------------------------------------------------------------------
# check_jira_health
# ---------------------------------------------------------------------------

class TestCheckJiraHealth:

    def test_delegates(self):
        mock_result = MagicMock()
        with patch('api.routes.failures.check_jira_health', return_value=mock_result):
            from mcp_server.tools import check_jira_health
            result = _run(check_jira_health)
        assert result is mock_result


# ---------------------------------------------------------------------------
# get_exception_lifecycle
# ---------------------------------------------------------------------------

class TestGetExceptionLifecycle:

    def test_delegates(self):
        mock_result = MagicMock()
        with patch('api.routes.violations.get_exception_lifecycle', return_value=mock_result):
            from mcp_server.tools import get_exception_lifecycle
            result = _run(get_exception_lifecycle)
        assert result is mock_result


# ---------------------------------------------------------------------------
# check_fix_propagation
# ---------------------------------------------------------------------------

class TestCheckFixPropagation:

    def test_delegates(self):
        mock_result = MagicMock()
        with patch('api.routes.failures.check_fix_propagation', return_value=mock_result):
            from mcp_server.tools import check_fix_propagation
            result = _run(check_fix_propagation, days=7, target_branch='release')
        assert result is mock_result


# ---------------------------------------------------------------------------
# list_blockers
# ---------------------------------------------------------------------------

class TestListBlockers:

    def test_delegates(self):
        mock_result = MagicMock()
        with patch('api.routes.failures.list_blockers', return_value=mock_result):
            from mcp_server.tools import list_blockers
            result = _run(list_blockers)
        assert result is mock_result


# ---------------------------------------------------------------------------
# list_bouncing_issues
# ---------------------------------------------------------------------------

class TestListBouncingIssues:

    def test_delegates(self):
        mock_result = MagicMock()
        with patch('api.routes.failures.list_bouncing_issues', return_value=mock_result):
            from mcp_server.tools import list_bouncing_issues
            result = _run(list_bouncing_issues, min_bounces=3)
        assert result is mock_result


# ---------------------------------------------------------------------------
# get_fbc_history
# ---------------------------------------------------------------------------

class TestGetFbcHistory:

    def test_delegates(self):
        mock_result = MagicMock()
        with patch('api.routes.releases.get_fbc_history', return_value=mock_result):
            from mcp_server.tools import get_fbc_history
            result = _run(get_fbc_history, limit=5)
        assert result is mock_result


# ---------------------------------------------------------------------------
# get_working / get_resolved / get_daily_stats / get_component_history
# ---------------------------------------------------------------------------

class TestSimpleBuildRepoTools:

    @patch('mcp_server.tools._build_repo')
    def test_get_working(self, mock_build):
        mock_build.return_value.get_working_components.return_value = [{'c': 'ok'}]
        from mcp_server.tools import get_working
        result = _run(get_working)
        assert result == [{'c': 'ok'}]

    @patch('mcp_server.tools._build_repo')
    def test_get_resolved(self, mock_build):
        mock_build.return_value.get_resolved_components.return_value = [{'c': 'fixed'}]
        from mcp_server.tools import get_resolved
        result = _run(get_resolved, days=7)
        assert result == [{'c': 'fixed'}]

    @patch('mcp_server.tools._build_repo')
    def test_get_daily_stats(self, mock_build):
        mock_build.return_value.get_daily_stats.return_value = [
            {'date': '2026-07-01', 'count': 5}
        ]
        from mcp_server.tools import get_daily_stats
        result = _run(get_daily_stats, days=3)
        assert len(result) == 1

    @patch('mcp_server.tools._build_repo')
    def test_get_component_history(self, mock_build):
        mock_build.return_value.get_component_history.return_value = {
            'summary': {'total': 10, 'success': 8},
            'builds': [{'status': 'ok'}],
        }
        from mcp_server.tools import get_component_history
        result = _run(get_component_history, 'comp-a', limit=5)
        assert result.component == 'comp-a'
        assert result.summary['total'] == 10


# ---------------------------------------------------------------------------
# get_dashboard
# ---------------------------------------------------------------------------

class TestGetDashboard:

    @patch('mcp_server.tools._resolution_repo')
    @patch('mcp_server.tools._pattern_repo')
    @patch('mcp_server.tools._ai_repo')
    @patch('mcp_server.tools._build_repo')
    def test_happy_path(self, mock_build, mock_ai, mock_pattern, mock_resolution):
        mock_build.return_value.get_overview_stats.return_value = {
            'components': 50, 'total': 5
        }
        mock_build.return_value.get_enrichment_coverage.return_value = {
            'logs': 40, 'context': 30
        }
        mock_ai.return_value.get_extended_status.return_value = {
            'build': {}, 'conforma': {},
        }
        mock_ai.return_value.get_cost_summary.return_value = {'total': 5.0}
        mock_pattern.return_value.get_library_summary.return_value = {'count': 10}
        mock_resolution.return_value.get_outcome_summary.return_value = {'success': 3}

        from mcp_server.tools import get_dashboard
        result = _run(get_dashboard, application='rhoai-v3-5')
        assert result.application == 'rhoai-v3-5'
        assert result.overview['components'] == 50
        assert result.patterns == {'count': 10}


# ---------------------------------------------------------------------------
# list_patterns / get_pattern / get_fix_history
# ---------------------------------------------------------------------------

class TestPatternAndResolutionTools:

    @patch('mcp_server.tools._pattern_repo')
    def test_list_patterns(self, mock_pattern):
        mock_pattern.return_value.get_all.return_value = [
            {'name': 'go-mismatch', 'type': 'build'}
        ]
        from mcp_server.tools import list_patterns
        result = _run(list_patterns, failure_type='build')
        assert len(result) == 1

    @patch('mcp_server.tools._pattern_repo')
    def test_get_pattern_found(self, mock_pattern):
        mock_pattern.return_value.get_by_name_or_category.return_value = {
            'name': 'go-mismatch',
        }
        from mcp_server.tools import get_pattern
        result = _run(get_pattern, 'go-mismatch')
        assert result['name'] == 'go-mismatch'

    @patch('mcp_server.tools._pattern_repo')
    def test_get_pattern_not_found(self, mock_pattern):
        mock_pattern.return_value.get_by_name_or_category.return_value = None
        from mcp_server.tools import get_pattern
        result = _run(get_pattern, 'nonexistent')
        assert result is None

    @patch('mcp_server.tools._resolution_repo')
    def test_get_fix_history(self, mock_resolution):
        mock_resolution.return_value.get_all.return_value = [
            {'id': 1, 'outcome': 'success'}
        ]
        mock_resolution.return_value.get_outcome_summary.return_value = {'success': 1}
        from mcp_server.tools import get_fix_history
        result = _run(get_fix_history, days=14)
        assert result['summary']['success'] == 1
        assert len(result['attempts']) == 1


# ---------------------------------------------------------------------------
# _konflux_url helper
# ---------------------------------------------------------------------------

class TestKonfluxUrl:

    def test_url_format(self):
        from mcp_server.tools import _konflux_url
        url = _konflux_url('pr-abc-123')
        assert 'pr-abc-123' in url
        assert '/logs' in url


# ---------------------------------------------------------------------------
# async_tool decorator
# ---------------------------------------------------------------------------

class TestAsyncToolDecorator:

    def test_wraps_sync_function(self):
        from mcp_server.tools import async_tool

        def my_sync(x):
            return x * 2

        wrapped = async_tool(my_sync)
        assert asyncio.iscoroutinefunction(wrapped)
        result = asyncio.get_event_loop().run_until_complete(wrapped(5))
        assert result == 10

    def test_preserves_name(self):
        from mcp_server.tools import async_tool

        def my_func():
            """My docstring."""
            pass

        wrapped = async_tool(my_func)
        assert wrapped.__name__ == 'my_func'
        assert wrapped.__doc__ == 'My docstring.'
