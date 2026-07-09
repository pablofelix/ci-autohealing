"""Extended coverage tests for MCP server tools.

Complements ``test_mcp_tools.py`` with broader coverage of edge cases, error
paths, and tools not yet tested.  Follows the same import-avoidance pattern:
``mcp_server.mcp`` is patched with a stub before importing ``mcp_server.tools``.
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
# Helpers (same as test_mcp_tools.py)
# ---------------------------------------------------------------------------

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


def _now():
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# list_applications — edge cases
# ---------------------------------------------------------------------------

class TestListApplicationsExtended:

    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_single_app_zero_failures(self, mock_build, mock_conforma):
        mock_build.return_value.get_applications.return_value = [
            {'application': 'app-1'},
        ]
        mock_build.return_value.get_overview_stats.return_value = {
            'components': 5, 'total': 0,
        }
        mock_conforma.return_value.find_unresolved_component_names.return_value = []
        from mcp_server.tools import list_applications
        result = _run(list_applications)
        assert len(result) == 1
        assert result[0].failure_count == 0
        assert result[0].conforma_count == 0
        assert result[0].component_count == 5

    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_multiple_apps_with_mixed_counts(self, mock_build, mock_conforma):
        mock_build.return_value.get_applications.return_value = [
            {'application': 'a'}, {'application': 'b'}, {'application': 'c'},
        ]
        mock_build.return_value.get_overview_stats.side_effect = [
            {'components': 10, 'total': 2},
            {'components': 20, 'total': 0},
            {'components': 30, 'total': 15},
        ]
        mock_conforma.return_value.find_unresolved_component_names.side_effect = [
            ['x'], [], ['y', 'z', 'w'],
        ]
        from mcp_server.tools import list_applications
        result = _run(list_applications)
        assert len(result) == 3
        assert result[0].name == 'a'
        assert result[0].conforma_count == 1
        assert result[2].conforma_count == 3
        assert result[2].failure_count == 15


# ---------------------------------------------------------------------------
# list_alerts
# ---------------------------------------------------------------------------

class TestListAlerts:

    @patch('mcp_server.tools._triage_repo')
    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_empty_alerts(self, mock_build, mock_conforma, mock_triage):
        mock_build.return_value.get_triage_summary.return_value = {
            'failing_components': [],
        }
        mock_conforma.return_value.get_violation_summaries.return_value = []
        mock_triage.return_value.build_jira_map.return_value = {}

        with patch('clients.konflux_client.KonfluxClient') as mock_kc_cls, \
             patch('api.routes.failures._compute_age_signals', return_value=(0, True, False)), \
             patch('api.routes.failures._detect_systemic_patterns'), \
             patch('conforma.policy_tools.fetch_exceptions_by_policy', return_value={}), \
             patch('api.routes.failures._compute_freeze_countdown', return_value=None), \
             patch('api.routes.releases.get_schedule', return_value=None):
            mock_kc_cls.return_value.get_dependency_updates.return_value = []
            from mcp_server.tools import list_alerts
            result = _run(list_alerts, application='test-app')

        assert result.application == 'test-app'
        assert result.total_count == 0
        assert result.build_failures == []
        assert result.conforma_violations == []

    @patch('mcp_server.tools._triage_repo')
    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_build_failures_with_dep_update(self, mock_build, mock_conforma, mock_triage):
        now = _now()
        mock_build.return_value.get_triage_summary.return_value = {
            'failing_components': [
                {
                    'component': 'dep-comp',
                    'status': 'Failed',
                    'error_type': 'build_error',
                    'first_detected_at': now - timedelta(hours=1),
                    'last_updated_at': now,
                    'failure_count': 1,
                    'has_logs': True,
                    'ai_analyzed': False,
                },
            ],
        }
        mock_conforma.return_value.get_violation_summaries.return_value = []
        mock_triage.return_value.build_jira_map.return_value = {}

        with patch('clients.konflux_client.KonfluxClient') as mock_kc_cls, \
             patch('api.routes.failures._compute_age_signals', return_value=(1.0, True, False)), \
             patch('api.routes.failures._detect_systemic_patterns'), \
             patch('conforma.policy_tools.fetch_exceptions_by_policy', return_value={}), \
             patch('api.routes.failures._compute_freeze_countdown', return_value=None), \
             patch('api.routes.releases.get_schedule', return_value=None):
            mock_kc_cls.return_value.get_dependency_updates.return_value = [
                {'component': 'dep-comp', 'package': 'go', 'from_version': '1.21', 'to_version': '1.22'},
            ]
            from mcp_server.tools import list_alerts
            result = _run(list_alerts, application='app')

        assert result.total_count == 1
        assert len(result.build_failures) == 1
        assert result.build_failures[0].possible_cause is not None
        assert 'dependency_update' in result.build_failures[0].possible_cause

    @patch('mcp_server.tools._triage_repo')
    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_conforma_violations_in_alerts(self, mock_build, mock_conforma, mock_triage):
        now = _now()
        mock_build.return_value.get_triage_summary.return_value = {
            'failing_components': [],
        }
        mock_conforma.return_value.get_violation_summaries.return_value = [
            {
                'component_name': 'viol-comp',
                'scenario': 'verify-stage',
                'violations_count': 3,
                'warnings_count': 1,
                'first_detected_at': now,
                'last_updated_at': now,
                'violation_summary': 'FAIL: hermetic_task.hermetic',
                'ai_analyzed': False,
            },
        ]
        mock_triage.return_value.build_jira_map.return_value = {
            'viol-comp': 'RHOAI-999',
        }

        with patch('clients.konflux_client.KonfluxClient') as mock_kc_cls, \
             patch('api.routes.failures._compute_age_signals', return_value=(0, True, False)), \
             patch('api.routes.failures._detect_systemic_patterns'), \
             patch('conforma.policy_tools.fetch_exceptions_by_policy', return_value={}), \
             patch('conforma.policy_tools.categorize_policy', return_value='Components'), \
             patch('conforma.policy_tools.compute_exception_coverage_details', return_value={
                 'coverage': None, 'stage': None, 'prod': None, 'env_tag': None,
                 'policy_url_stage': None, 'policy_url_prod': None,
                 'uncovered_rules_stage': None, 'uncovered_rules_prod': None,
             }), \
             patch('conforma.policy_tools.count_unique_violations', return_value=(1, [])), \
             patch('conforma.policy_tools.extract_violation_rules', return_value=[]), \
             patch('conforma.policy_tools.policy_url', return_value='http://policy'), \
             patch('api.routes.failures._compute_freeze_countdown', return_value=None), \
             patch('api.routes.releases.get_schedule', return_value=None):
            mock_kc_cls.return_value.get_dependency_updates.return_value = []
            from mcp_server.tools import list_alerts
            result = _run(list_alerts, application='app')

        assert result.total_count == 1
        assert len(result.conforma_violations) == 1
        assert result.conforma_violations[0].jira_key == 'RHOAI-999'


# ---------------------------------------------------------------------------
# check_jira_health — extended
# ---------------------------------------------------------------------------

class TestCheckJiraHealthExtended:

    def test_error_path(self):
        from mcp_server.models import JiraTokenHealth
        mock_result = JiraTokenHealth(status='expired', message='Token expired')
        with patch('api.routes.failures.check_jira_health', return_value=mock_result):
            from mcp_server.tools import check_jira_health
            result = _run(check_jira_health)
        assert result.status == 'expired'
        assert 'expired' in result.message.lower()


# ---------------------------------------------------------------------------
# get_exception_lifecycle — extended
# ---------------------------------------------------------------------------

class TestGetExceptionLifecycleExtended:

    def test_returns_summary(self):
        from mcp_server.models import ExceptionLifecycleSummary
        mock_result = ExceptionLifecycleSummary(
            total_exceptions=10, permanent=3, active_temporary=5,
            expiring_soon=2, expired=0, exceptions=[],
        )
        with patch('api.routes.violations.get_exception_lifecycle', return_value=mock_result):
            from mcp_server.tools import get_exception_lifecycle
            result = _run(get_exception_lifecycle)
        assert result.total_exceptions == 10
        assert result.permanent == 3


# ---------------------------------------------------------------------------
# get_fbc_history — extended
# ---------------------------------------------------------------------------

class TestGetFbcHistoryExtended:

    def test_with_default_limit(self):
        mock_result = MagicMock()
        with patch('api.routes.releases.get_fbc_history', return_value=mock_result) as mock_fn:
            from mcp_server.tools import get_fbc_history
            result = _run(get_fbc_history, application='rhoai-v3-5')
        mock_fn.assert_called_once_with('rhoai-v3-5', limit=20)
        assert result is mock_result


# ---------------------------------------------------------------------------
# check_fix_propagation — extended
# ---------------------------------------------------------------------------

class TestCheckFixPropagationExtended:

    def test_with_custom_params(self):
        mock_result = MagicMock()
        with patch('api.routes.failures.check_fix_propagation', return_value=mock_result) as mock_fn:
            from mcp_server.tools import check_fix_propagation
            result = _run(check_fix_propagation, application='app-1', days=30,
                          target_branch='release-v2')
        mock_fn.assert_called_once_with('app-1', days=30, target_branch='release-v2')
        assert result is mock_result


# ---------------------------------------------------------------------------
# list_blockers — extended
# ---------------------------------------------------------------------------

class TestListBlockersExtended:

    def test_passes_application(self):
        mock_result = MagicMock()
        with patch('api.routes.failures.list_blockers', return_value=mock_result) as mock_fn:
            from mcp_server.tools import list_blockers
            result = _run(list_blockers, application='my-app')
        mock_fn.assert_called_once_with('my-app')
        assert result is mock_result


# ---------------------------------------------------------------------------
# list_bouncing_issues — extended
# ---------------------------------------------------------------------------

class TestListBouncingIssuesExtended:

    def test_default_min_bounces(self):
        mock_result = MagicMock()
        with patch('api.routes.failures.list_bouncing_issues', return_value=mock_result) as mock_fn:
            from mcp_server.tools import list_bouncing_issues
            result = _run(list_bouncing_issues, application='app')
        mock_fn.assert_called_once_with('app', min_bounces=2)


# ---------------------------------------------------------------------------
# get_failure — extended edge cases
# ---------------------------------------------------------------------------

class TestGetFailureExtended:

    @patch('mcp_server.tools._build_repo')
    def test_none_logs_when_raw_logs_empty(self, mock_build):
        row = {
            'component_name': 'comp',
            'pipelinerun_name': 'pr-1',
            'error_message': 'fail',
            'error_type': None,
            'failed_task_name': None,
            'failed_step_name': None,
            'build_logs': None,
            'commit_sha': None,
            'commit_message': None,
            'commit_author': None,
            'commit_url': None,
            'repository_url': None,
            'branch': None,
            'commit_context': None,
            'konflux_url': None,
            'first_detected_at': _now(),
        }
        mock_build.return_value.get_failure_details.return_value = row

        with patch('log_filter.filter_error_lines') as mock_filter:
            from mcp_server.tools import get_failure
            result = _run(get_failure, 'comp', include_logs=True)

        # filter should not be called when build_logs is None
        mock_filter.assert_not_called()
        assert result.build_logs is None

    @patch('mcp_server.tools._build_repo')
    def test_konflux_url_fallback(self, mock_build):
        """When konflux_url is absent, it should be generated from pipelinerun_name."""
        row = {
            'component_name': 'comp',
            'pipelinerun_name': 'pr-fallback-123',
            'error_message': 'err',
            'error_type': None,
            'failed_task_name': None,
            'failed_step_name': None,
            'build_logs': None,
            'commit_sha': None,
            'commit_message': None,
            'commit_author': None,
            'commit_url': None,
            'repository_url': None,
            'branch': None,
            'commit_context': None,
            'konflux_url': None,
            'first_detected_at': _now(),
        }
        mock_build.return_value.get_failure_details.return_value = row
        from mcp_server.tools import get_failure
        result = _run(get_failure, 'comp', include_logs=False)
        assert 'pr-fallback-123' in result.konflux_url


# ---------------------------------------------------------------------------
# get_violation — extended
# ---------------------------------------------------------------------------

class TestGetViolationExtended:

    @patch('mcp_server.tools._conforma_repo')
    def test_include_details_false(self, mock_conforma):
        row = {
            'component_name': 'comp-a',
            'scenario': 'verify-conforma',
            'violations_count': 2,
            'warnings_count': 0,
            'successes_count': 50,
            'violation_summary': 'FAIL: rule.x',
            'violation_details': {'big': 'blob'},
            'repository_url': None,
            'commit_sha': None,
            'snapshot_name': None,
            'pipelinerun_name': 'pr-1',
            'first_detected_at': _now(),
        }
        mock_conforma.return_value.get_violation_details.return_value = row

        with patch('conforma.policy_tools.fetch_exceptions_by_policy', return_value={}), \
             patch('conforma.policy_tools.extract_policy_from_scenario', return_value='stage'), \
             patch('conforma.policy_tools.policy_url', return_value='http://p'), \
             patch('conforma.policy_tools.count_unique_violations', return_value=(1, [
                 {'rule': 'rule.x', 'detail': ''},
             ])), \
             patch('conforma.policy_tools.extract_violation_rules', return_value=[]), \
             patch('conforma.policy_tools.compute_violation_coverage', return_value=None), \
             patch('conforma.policy_tools.lookup_exceptions', return_value=[]):
            from mcp_server.tools import get_violation
            result = _run(get_violation, 'comp-a', include_details=False)

        assert result is not None
        assert result.violation_details is None


# ---------------------------------------------------------------------------
# get_analysis — extended
# ---------------------------------------------------------------------------

class TestGetAnalysisExtended:

    @patch('mcp_server.tools._ai_repo')
    def test_auto_type(self, mock_ai):
        mock_ai.return_value.get_analysis_by_component.return_value = {
            'analysis_type': 'conforma',
            'component_name': 'comp-y',
            'model_used': 'claude',
            'root_cause': 'missing exception',
            'failure_category': 'policy_violation',
            'confidence_score': 0.7,
            'recommended_fix': 'add exception',
            'recommended_files': None,
            'can_auto_fix': False,
            'requires_human_review': True,
            'analyzed_at': _now(),
            'langfuse_trace_url': 'http://trace/1',
            'tokens_used': 200,
            'cost_usd': 0.02,
            'analysis_json': None,
        }
        from mcp_server.tools import get_analysis
        result = _run(get_analysis, 'comp-y', type='auto')
        assert result.type == 'conforma'
        assert result.langfuse_trace_url == 'http://trace/1'


# ---------------------------------------------------------------------------
# submit_analysis — extended
# ---------------------------------------------------------------------------

class TestSubmitAnalysisExtended:

    @patch('mcp_server.tools._ai_repo')
    @patch('mcp_server.tools._build_repo')
    def test_with_optional_params(self, mock_build, mock_ai):
        mock_build.return_value.get_failure_details.return_value = {'id': 7}
        mock_ai.return_value.insert_analysis.return_value = 44
        from mcp_server.tools import submit_analysis
        result = submit_analysis(
            component='c', root_cause='r', failure_category='f',
            confidence_score=0.6, recommended_fix='fix',
            model_used='gpt-4', recommended_files=['a.py', 'b.py'],
            can_auto_fix=True, tokens_used=1000, cost_usd=0.10,
        )
        assert result['action'] == 'stored'
        call_kwargs = mock_ai.return_value.insert_analysis.call_args[1]
        assert call_kwargs['model_used'] == 'gpt-4'
        assert call_kwargs['recommended_files'] == ['a.py', 'b.py']
        assert call_kwargs['can_auto_fix'] is True
        assert call_kwargs['requires_human_review'] is False


# ---------------------------------------------------------------------------
# search_failures — extended
# ---------------------------------------------------------------------------

class TestSearchFailuresExtended:

    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_empty_results(self, mock_build, mock_conforma):
        mock_build.return_value.get_triage_summary.return_value = {
            'failing_components': [],
        }
        mock_conforma.return_value.find_unresolved_component_names.return_value = []
        from mcp_server.tools import search_failures
        result = _run(search_failures)
        assert result == []

    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_filter_has_analysis_false(self, mock_build, mock_conforma):
        now = _now()
        mock_build.return_value.get_triage_summary.return_value = {
            'failing_components': [
                {
                    'component': 'analyzed', 'status': 'Failed',
                    'first_detected_at': now, 'last_updated_at': now,
                    'failure_count': 1, 'has_logs': True, 'ai_analyzed': True,
                },
                {
                    'component': 'pending', 'status': 'Failed',
                    'first_detected_at': now, 'last_updated_at': now,
                    'failure_count': 1, 'has_logs': True, 'ai_analyzed': False,
                },
            ],
        }
        mock_conforma.return_value.find_unresolved_component_names.return_value = []
        from mcp_server.tools import search_failures
        result = _run(search_failures, has_analysis=False)
        assert len(result) == 1
        assert result[0].component == 'pending'

    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_conforma_filter_has_analysis(self, mock_build, mock_conforma):
        now = _now()
        mock_build.return_value.get_triage_summary.return_value = {
            'failing_components': [],
        }
        mock_conforma.return_value.find_unresolved_component_names.return_value = [
            'comp-analyzed', 'comp-pending',
        ]
        mock_conforma.return_value.get_violation_details.side_effect = [
            {
                'scenario': 'verify-conforma',
                'first_detected_at': now, 'last_updated_at': now,
                'violation_summary': 'FAIL: rule', 'ai_analyzed': True,
            },
            {
                'scenario': 'verify-conforma',
                'first_detected_at': now, 'last_updated_at': now,
                'violation_summary': 'FAIL: rule', 'ai_analyzed': False,
            },
        ]
        from mcp_server.tools import search_failures
        result = _run(search_failures, has_analysis=True)
        assert len(result) == 1
        assert result[0].component == 'comp-analyzed'


# ---------------------------------------------------------------------------
# get_stats — extended
# ---------------------------------------------------------------------------

class TestGetStatsExtended:

    @patch('mcp_server.tools._ai_repo')
    def test_empty_analyses(self, mock_ai):
        mock_ai.return_value.get_extended_status.return_value = {
            'build': {'pending': 0, 'analyzed': 0, 'auto_fixable': 0},
            'conforma': {'pending': 0, 'analyzed': 0, 'auto_fixable': 0},
            'total_cost': 0,
        }
        mock_ai.return_value.get_recent_analyses.return_value = []
        from mcp_server.tools import get_stats
        result = _run(get_stats, application='app')
        assert result.application == 'app'
        assert result.total_cost_30d == 0
        assert result.recent_analyses == []


# ---------------------------------------------------------------------------
# get_triage — extended
# ---------------------------------------------------------------------------

class TestGetTriageExtended:

    @patch('mcp_server.tools._build_repo')
    def test_empty_triage(self, mock_build):
        mock_build.return_value.get_triage_summary.return_value = {
            'total': 0, 'failing': 0, 'working': 0,
            'failing_components': [],
        }
        from mcp_server.tools import get_triage
        result = _run(get_triage, application='empty-app')
        assert result.application == 'empty-app'
        assert result.total == 0
        assert result.failing_components == []


# ---------------------------------------------------------------------------
# get_triage_report — extended
# ---------------------------------------------------------------------------

class TestGetTriageReportExtended:

    @patch('mcp_server.tools._triage_repo')
    def test_empty_report(self, mock_triage):
        mock_triage.return_value.get_report.return_value = []
        mock_triage.return_value.get_summary.return_value = {
            'active': 0, 'resolved': 0,
        }
        from mcp_server.tools import get_triage_report
        result = _run(get_triage_report, application='app', date=None)
        assert result['items'] == []
        assert result['summary']['active'] == 0

    @patch('mcp_server.tools._triage_repo')
    def test_with_date_filter(self, mock_triage):
        mock_triage.return_value.get_report.return_value = [
            {'id': 1, 'component': 'c1', 'status': 'active'},
        ]
        mock_triage.return_value.get_summary.return_value = {'active': 1}
        from mcp_server.tools import get_triage_report
        result = _run(get_triage_report, application='app', date='2026-07-01')
        mock_triage.return_value.get_report.assert_called_once_with('app', date='2026-07-01')
        assert len(result['items']) == 1


# ---------------------------------------------------------------------------
# track_triage_item — extended
# ---------------------------------------------------------------------------

class TestTrackTriageItemExtended:

    @patch('mcp_server.tools._triage_repo')
    def test_create_with_all_fields(self, mock_triage):
        mock_triage.return_value.find_by_component.return_value = None
        mock_triage.return_value.create_item.return_value = 100
        from mcp_server.tools import track_triage_item
        result = _run(track_triage_item, 'comp-x',
                      application='app', group_label='Go issue',
                      root_cause='dep mismatch', failed_step='build',
                      slack_thread_url='http://slack', jira_key='RHOAI-1',
                      notes='test note')
        assert result['action'] == 'created'
        assert result['item_id'] == 100
        call_kwargs = mock_triage.return_value.create_item.call_args[1]
        assert call_kwargs['group_label'] == 'Go issue'
        assert call_kwargs['root_cause'] == 'dep mismatch'
        assert call_kwargs['jira_key'] == 'RHOAI-1'
        assert call_kwargs['slack_thread_urls'] == ['http://slack']

    @patch('mcp_server.tools._triage_repo')
    def test_add_to_existing_component_already_in_list(self, mock_triage):
        """Adding a component that's already in the list should not duplicate."""
        mock_triage.return_value.get_by_id.return_value = {
            'id': 10, 'components': ['comp-a'], 'status': 'active',
        }
        from mcp_server.tools import track_triage_item
        result = _run(track_triage_item, 'comp-a', add_to_id=10)
        assert result['action'] == 'added'
        call_kwargs = mock_triage.return_value.update_item.call_args[1]
        # comp-a should appear exactly once
        assert call_kwargs['components'].count('comp-a') == 1


# ---------------------------------------------------------------------------
# update_triage_item — extended
# ---------------------------------------------------------------------------

class TestUpdateTriageItemExtended:

    @patch('mcp_server.tools._triage_repo')
    def test_update_root_cause(self, mock_triage):
        mock_triage.return_value.get_by_id.return_value = {'id': 5}
        from mcp_server.tools import update_triage_item
        result = _run(update_triage_item, item_id=5, root_cause='new cause')
        assert result['action'] == 'updated'
        mock_triage.return_value.update_item.assert_called_once_with(
            5, root_cause='new cause',
        )

    @patch('mcp_server.tools._triage_repo')
    def test_update_status_to_monitoring(self, mock_triage):
        mock_triage.return_value.get_by_id.return_value = {'id': 6}
        from mcp_server.tools import update_triage_item
        result = _run(update_triage_item, item_id=6, status='monitoring')
        assert result['action'] == 'updated'
        mock_triage.return_value.update_item.assert_called_once_with(
            6, status='monitoring',
        )

    @patch('mcp_server.tools._triage_repo')
    def test_resolve_with_pr(self, mock_triage):
        mock_triage.return_value.get_by_id.return_value = {'id': 7}
        from mcp_server.tools import update_triage_item
        result = _run(update_triage_item, item_id=7, status='resolved',
                      resolution='fixed', resolution_pr_url='http://pr/1')
        assert result['action'] == 'resolved'
        mock_triage.return_value.resolve_item.assert_called_once_with(
            7, resolution='fixed', pr_url='http://pr/1',
        )


# ---------------------------------------------------------------------------
# resolve_triage_item — extended
# ---------------------------------------------------------------------------

class TestResolveTriageItemExtended:

    @patch('mcp_server.tools._triage_repo')
    def test_resolve_with_pr_url(self, mock_triage):
        from mcp_server.tools import resolve_triage_item
        result = resolve_triage_item(item_id=3, resolution='PR merged',
                                      pr_url='http://pr/2')
        assert result['action'] == 'resolved'
        mock_triage.return_value.resolve_item.assert_called_once_with(
            3, resolution='PR merged', pr_url='http://pr/2',
        )

    @patch('mcp_server.tools._db_connection')
    @patch('mcp_server.tools._triage_repo')
    def test_invalid_verdict_skipped(self, mock_triage, mock_db):
        """Invalid verdict values should not call record_verdict."""
        mock_triage.return_value.get_by_id.return_value = None
        from mcp_server.tools import resolve_triage_item
        result = resolve_triage_item(item_id=5, verdict='invalid_value')
        assert result['action'] == 'resolved'
        # record_verdict should NOT have been called since 'invalid_value'
        # is not in the allowed set


# ---------------------------------------------------------------------------
# get_conforma_categories
# ---------------------------------------------------------------------------

class TestGetConformaCategories:

    @patch('mcp_server.tools._db_connection')
    def test_db_path(self, mock_db):
        """Test the non-reporter path using _db_connection."""
        mock_conn = MagicMock()
        mock_db.return_value = mock_conn

        with patch('repositories.conforma_repository.ConformaRepository') as mock_repo_cls, \
             patch('conforma.policy_tools.fetch_exceptions_by_policy', return_value={}), \
             patch('conforma.policy_tools.categorize_policy', return_value='Components'), \
             patch('conforma.policy_tools.extract_policy_from_scenario', return_value='stage'), \
             patch('conforma.policy_tools.count_unique_violations', return_value=(2, [])), \
             patch('conforma.policy_tools.compute_violation_coverage', return_value=None), \
             patch('conforma.policy_tools.extract_violation_rules', return_value=[]), \
             patch('conforma.policy_tools.lookup_exceptions', return_value=[]):
            mock_repo_cls.return_value.find_unresolved_component_names.return_value = ['comp-a']
            mock_repo_cls.return_value.get_violation_details.return_value = {
                'scenario': 'verify-conforma-stage',
                'violation_summary': 'FAIL: rule.x',
                'violations_count': 3,
            }
            from mcp_server.tools import get_conforma_categories
            result = get_conforma_categories(application='app')

        assert result['application'] == 'app'
        assert 'categories' in result
        assert 'groups' in result
        # Default groups should be present
        assert 'FBC' in result['groups']
        assert 'Components' in result['groups']
        assert 'Charts' in result['groups']

    def test_reporter_path(self):
        """Test the reporter-env path."""
        with patch('cli.config.app_to_reporter_branch', return_value='rhoai-v3.5'), \
             patch('clients.conforma_reporter_client.fetch_reporter_violations', return_value=[
                 {'component': 'c1', 'unique_violations': 3},
                 {'component': 'c2', 'unique_violations': 1},
             ]):
            from mcp_server.tools import get_conforma_categories
            result = get_conforma_categories(
                application='app', reporter_env='stage',
                reporter_build_type='latest',
            )

        assert result['application'] == 'app'
        assert result['source'] == 'stage/latest'
        assert 'FBC' in result['groups']
        assert 'Components' in result['groups']
        assert 'Charts' in result['groups']


# ---------------------------------------------------------------------------
# get_conforma_rules
# ---------------------------------------------------------------------------

class TestGetConformaRules:

    @patch('mcp_server.tools._db_connection')
    def test_db_path(self, mock_db):
        mock_conn = MagicMock()
        mock_db.return_value = mock_conn

        with patch('repositories.conforma_repository.ConformaRepository') as mock_repo_cls, \
             patch('conforma.policy_tools.count_unique_violations') as mock_cuv:
            mock_repo_cls.return_value.get_violation_summaries.return_value = [
                {
                    'component_name': 'comp-a',
                    'violation_summary': 'FAIL: hermetic_task.hermetic',
                },
                {
                    'component_name': 'comp-b',
                    'violation_summary': 'FAIL: hermetic_task.hermetic',
                },
            ]
            mock_cuv.side_effect = [
                (1, [{'rule': 'hermetic_task.hermetic', 'detail': ''}]),
                (1, [{'rule': 'hermetic_task.hermetic', 'detail': ''}]),
            ]
            from mcp_server.tools import get_conforma_rules
            result = _run(get_conforma_rules, application='app')

        assert result['total_rules'] == 1
        rule = result['rules'][0]
        assert rule['rule'] == 'hermetic_task.hermetic'
        assert rule['count'] == 2
        assert set(rule['components']) == {'comp-a', 'comp-b'}

    def test_reporter_path(self):
        with patch('cli.config.app_to_reporter_branch', return_value='rhoai-v3.5'), \
             patch('clients.conforma_reporter_client.fetch_reporter_rules', return_value=[
                 {'rule': 'hermetic', 'title': 'Hermetic', 'components': ['c1']},
             ]):
            from mcp_server.tools import get_conforma_rules
            result = _run(get_conforma_rules, application='app',
                          reporter_env='prod', reporter_build_type='nightly')

        assert result['source'] == 'prod/nightly'
        assert result['total_rules'] == 1


# ---------------------------------------------------------------------------
# get_working / get_resolved / get_daily_stats / get_component_history
# ---------------------------------------------------------------------------

class TestSimpleBuildRepoToolsExtended:

    @patch('mcp_server.tools._build_repo')
    def test_get_working_empty(self, mock_build):
        mock_build.return_value.get_working_components.return_value = []
        from mcp_server.tools import get_working
        result = _run(get_working, application='app')
        assert result == []

    @patch('mcp_server.tools._build_repo')
    def test_get_resolved_with_since_and_to(self, mock_build):
        mock_build.return_value.get_resolved_components.return_value = [
            {'component': 'c1', 'resolved_at': '2026-07-01'},
        ]
        from mcp_server.tools import get_resolved
        result = _run(get_resolved, application='app', since='2026-06-01',
                      to='2026-07-01')
        mock_build.return_value.get_resolved_components.assert_called_once_with(
            'app', days=None, since='2026-06-01', to='2026-07-01',
        )
        assert len(result) == 1

    @patch('mcp_server.tools._build_repo')
    def test_get_daily_stats_default_days(self, mock_build):
        mock_build.return_value.get_daily_stats.return_value = []
        from mcp_server.tools import get_daily_stats
        result = _run(get_daily_stats)
        mock_build.return_value.get_daily_stats.assert_called_once()
        assert result == []

    @patch('mcp_server.tools._build_repo')
    def test_get_component_history_empty(self, mock_build):
        mock_build.return_value.get_component_history.return_value = {
            'summary': {}, 'builds': [],
        }
        from mcp_server.tools import get_component_history
        result = _run(get_component_history, 'comp-x', application='app')
        assert result.component == 'comp-x'
        assert result.application == 'app'
        assert result.builds == []


# ---------------------------------------------------------------------------
# get_dashboard — extended
# ---------------------------------------------------------------------------

class TestGetDashboardExtended:

    @patch('mcp_server.tools._resolution_repo')
    @patch('mcp_server.tools._pattern_repo')
    @patch('mcp_server.tools._ai_repo')
    @patch('mcp_server.tools._build_repo')
    def test_default_application(self, mock_build, mock_ai, mock_pattern, mock_resolution):
        mock_build.return_value.get_overview_stats.return_value = {
            'components': 0, 'total': 0,
        }
        mock_build.return_value.get_enrichment_coverage.return_value = {}
        mock_ai.return_value.get_extended_status.return_value = {
            'build': {}, 'conforma': {},
        }
        mock_ai.return_value.get_cost_summary.return_value = {'total': 0}
        mock_pattern.return_value.get_library_summary.return_value = {}
        mock_resolution.return_value.get_outcome_summary.return_value = {}

        from mcp_server.tools import get_dashboard
        result = _run(get_dashboard)
        assert result.overview['components'] == 0
        assert result.fixes == {}


# ---------------------------------------------------------------------------
# list_patterns / get_pattern / get_fix_history — extended
# ---------------------------------------------------------------------------

class TestPatternAndResolutionToolsExtended:

    @patch('mcp_server.tools._pattern_repo')
    def test_list_patterns_no_filter(self, mock_pattern):
        mock_pattern.return_value.get_all.return_value = [
            {'name': 'p1', 'type': 'build'},
            {'name': 'p2', 'type': 'conforma'},
        ]
        from mcp_server.tools import list_patterns
        result = _run(list_patterns)
        assert len(result) == 2
        mock_pattern.return_value.get_all.assert_called_once_with(None)

    @patch('mcp_server.tools._resolution_repo')
    def test_get_fix_history_empty(self, mock_resolution):
        mock_resolution.return_value.get_all.return_value = []
        mock_resolution.return_value.get_outcome_summary.return_value = {}
        from mcp_server.tools import get_fix_history
        result = _run(get_fix_history)
        assert result['summary'] == {}
        assert result['attempts'] == []


# ---------------------------------------------------------------------------
# export_jira
# ---------------------------------------------------------------------------

class TestExportJira:

    @patch('mcp_server.tools._ai_repo')
    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_build_failure_path(self, mock_build, mock_conforma, mock_ai):
        mock_build.return_value.get_failure_details.return_value = {
            'component_name': 'comp-a',
            'pipelinerun_name': 'pr-1',
            'error_message': 'exit 1',
            'error_type': 'build_error',
            'failed_step_name': 'compile',
            'repository_url': 'http://github.com/org/repo',
            'branch': 'main',
            'commit_sha': 'abc123',
            'commit_url': 'http://github.com/commit/abc123',
            'commit_message': 'fix stuff',
            'first_detected_at': _now(),
            'last_updated_at': _now(),
            'konflux_url': 'http://konflux/pr-1',
        }
        mock_ai.return_value.get_analysis_by_component.return_value = None
        from mcp_server.tools import export_jira
        result = _run(export_jira, 'comp-a')
        assert 'Build Failure: comp-a' in result
        assert 'pr-1' in result

    @patch('mcp_server.tools._ai_repo')
    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_conforma_violation_path(self, mock_build, mock_conforma, mock_ai):
        mock_build.return_value.get_failure_details.return_value = None
        mock_conforma.return_value.get_violation_details.return_value = {
            'component_name': 'comp-v',
            'scenario': 'verify-conforma-stage',
            'violations_count': 3,
            'warnings_count': 1,
            'violation_summary': 'FAIL: rule.x',
            'pipelinerun_name': 'pr-2',
            'snapshot_name': 'snap-1',
            'first_detected_at': _now(),
            'last_updated_at': _now(),
        }
        mock_ai.return_value.get_analysis_by_component.return_value = None
        with patch('conforma.policy_tools.count_unique_violations', return_value=(1, [])):
            from mcp_server.tools import export_jira
            result = _run(export_jira, 'comp-v')
        assert 'Conforma Policy Violation: comp-v' in result
        assert 'verify-conforma-stage' in result

    @patch('mcp_server.tools._ai_repo')
    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_not_found(self, mock_build, mock_conforma, mock_ai):
        mock_build.return_value.get_failure_details.return_value = None
        mock_conforma.return_value.get_violation_details.return_value = None
        mock_ai.return_value.get_analysis_for_release.return_value = None
        from mcp_server.tools import export_jira
        result = _run(export_jira, 'ghost')
        assert 'No unresolved failures' in result

    @patch('mcp_server.tools._ai_repo')
    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_release_analysis_path(self, mock_build, mock_conforma, mock_ai):
        mock_build.return_value.get_failure_details.return_value = None
        mock_conforma.return_value.get_violation_details.return_value = None
        mock_ai.return_value.get_analysis_for_release.return_value = {
            'root_cause': 'stale image',
            'recommended_fix': 'rebuild',
            'failure_category': 'release_failure',
            'confidence_score': 0.8,
            'can_auto_fix': True,
            'requires_human_review': False,
            'recommended_files': None,
            'analysis_json': None,
            'langfuse_trace_id': None,
        }
        from mcp_server.tools import export_jira
        result = _run(export_jira, 'release-v1')
        assert 'Release Failure: release-v1' in result
        assert 'stale image' in result


# ---------------------------------------------------------------------------
# export_markdown
# ---------------------------------------------------------------------------

class TestExportMarkdown:

    @patch('mcp_server.tools._ai_repo')
    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_build_failure_with_analysis(self, mock_build, mock_conforma, mock_ai):
        mock_build.return_value.get_failure_details.return_value = {
            'component_name': 'comp-md',
            'pipelinerun_name': 'pr-md',
            'error_message': 'compile error',
            'error_type': 'build_error',
            'failed_step_name': 'build',
            'repository_url': 'http://github.com/org/repo',
            'branch': 'main',
            'commit_sha': 'def456',
            'commit_url': 'http://github.com/commit/def456',
            'first_detected_at': _now(),
            'konflux_url': 'http://konflux/pr-md',
        }
        mock_ai.return_value.get_analysis_by_component.return_value = {
            'analysis_type': 'build',
            'component_name': 'comp-md',
            'model_used': 'claude',
            'root_cause': 'missing dep',
            'failure_category': 'dependency_issue',
            'confidence_score': 0.9,
            'recommended_fix': 'add dep',
            'recommended_files': ['go.mod'],
            'can_auto_fix': True,
            'requires_human_review': False,
            'analyzed_at': _now(),
            'langfuse_trace_url': None,
            'tokens_used': 100,
            'cost_usd': 0.01,
            'analysis_json': None,
        }
        from mcp_server.tools import export_markdown
        result = _run(export_markdown, 'comp-md')
        assert '## Build Failure: comp-md' in result
        assert '### AI Analysis' in result
        assert '`go.mod`' in result

    @patch('mcp_server.tools._ai_repo')
    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_conforma_violation(self, mock_build, mock_conforma, mock_ai):
        mock_build.return_value.get_failure_details.return_value = None
        mock_conforma.return_value.get_violation_details.return_value = {
            'component_name': 'comp-cv',
            'scenario': 'verify-stage',
            'violations_count': 5,
            'warnings_count': 2,
            'violation_summary': 'FAIL: trusted_task.trusted',
            'first_detected_at': _now(),
        }
        mock_ai.return_value.get_analysis_by_component.return_value = None
        with patch('conforma.policy_tools.count_unique_violations', return_value=(1, [])):
            from mcp_server.tools import export_markdown
            result = _run(export_markdown, 'comp-cv')
        assert '## Conforma Violation: comp-cv' in result

    @patch('mcp_server.tools._ai_repo')
    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_not_found(self, mock_build, mock_conforma, mock_ai):
        mock_build.return_value.get_failure_details.return_value = None
        mock_conforma.return_value.get_violation_details.return_value = None
        mock_ai.return_value.get_analysis_by_component.return_value = None
        from mcp_server.tools import export_markdown
        result = _run(export_markdown, 'ghost')
        assert 'No unresolved failures' in result


# ---------------------------------------------------------------------------
# export_json
# ---------------------------------------------------------------------------

class TestExportJson:

    @patch('mcp_server.tools._ai_repo')
    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_build_failure(self, mock_build, mock_conforma, mock_ai):
        mock_build.return_value.get_failure_details.return_value = {
            'component_name': 'comp-j',
            'build_logs': 'big log',
            'commit_context': {'k': 'v'},
            'error_message': 'err',
        }
        mock_ai.return_value.get_analysis_by_component.return_value = None
        from mcp_server.tools import export_json
        result = _run(export_json, 'comp-j', include_logs=False)
        parsed = json.loads(result)
        assert parsed['type'] == 'build_failure'
        # build_logs should be stripped when include_logs=False
        assert 'build_logs' not in parsed['failure']

    @patch('mcp_server.tools._ai_repo')
    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_conforma_violation(self, mock_build, mock_conforma, mock_ai):
        mock_build.return_value.get_failure_details.return_value = None
        mock_conforma.return_value.get_violation_details.return_value = {
            'component_name': 'comp-v',
            'scenario': 'verify',
            'violations_count': 2,
        }
        mock_ai.return_value.get_analysis_by_component.return_value = None
        from mcp_server.tools import export_json
        result = _run(export_json, 'comp-v')
        parsed = json.loads(result)
        assert parsed['type'] == 'conforma_violation'

    @patch('mcp_server.tools._ai_repo')
    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_not_found(self, mock_build, mock_conforma, mock_ai):
        mock_build.return_value.get_failure_details.return_value = None
        mock_conforma.return_value.get_violation_details.return_value = None
        from mcp_server.tools import export_json
        result = _run(export_json, 'ghost')
        parsed = json.loads(result)
        assert parsed['type'] == 'not_found'

    @patch('mcp_server.tools._ai_repo')
    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_with_analysis(self, mock_build, mock_conforma, mock_ai):
        mock_build.return_value.get_failure_details.return_value = {
            'component_name': 'comp',
            'error_message': 'err',
        }
        mock_ai.return_value.get_analysis_by_component.return_value = {
            'root_cause': 'dep issue',
        }
        from mcp_server.tools import export_json
        result = _run(export_json, 'comp', include_logs=True)
        parsed = json.loads(result)
        assert 'analysis' in parsed
        assert parsed['analysis']['root_cause'] == 'dep issue'


# ---------------------------------------------------------------------------
# export_slack
# ---------------------------------------------------------------------------

class TestExportSlack:

    @patch('mcp_server.tools._ai_repo')
    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_build_failure(self, mock_build, mock_conforma, mock_ai):
        mock_build.return_value.get_failure_details.return_value = {
            'component_name': 'comp-s',
            'pipelinerun_name': 'pr-s',
            'error_message': 'compile failed',
            'error_type': 'build_error',
            'failed_step_name': 'build',
            'repository_url': 'http://github.com/org/repo',
            'branch': 'main',
            'commit_sha': 'abc123',
            'commit_url': 'http://github.com/commit/abc',
            'commit_author': 'dev',
            'first_detected_at': _now() - timedelta(days=2),
            'konflux_url': 'http://konflux/pr-s',
        }
        mock_ai.return_value.get_analysis_by_component.return_value = None
        from mcp_server.tools import export_slack
        result = _run(export_slack, 'comp-s')
        assert ':red_circle:' in result
        assert 'comp-s' in result
        assert 'View in Konflux' in result

    @patch('mcp_server.tools._ai_repo')
    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_conforma_violation(self, mock_build, mock_conforma, mock_ai):
        mock_build.return_value.get_failure_details.return_value = None
        mock_conforma.return_value.get_violation_details.return_value = {
            'component_name': 'comp-sv',
            'scenario': 'verify-stage',
            'violations_count': 4,
            'warnings_count': 1,
            'violation_summary': 'FAIL: rule',
            'pipelinerun_name': 'pr-sv',
        }
        mock_ai.return_value.get_analysis_by_component.return_value = None
        with patch('conforma.policy_tools.count_unique_violations', return_value=(2, [])):
            from mcp_server.tools import export_slack
            result = _run(export_slack, 'comp-sv')
        assert ':warning:' in result
        assert 'comp-sv' in result

    @patch('mcp_server.tools._ai_repo')
    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_not_found(self, mock_build, mock_conforma, mock_ai):
        mock_build.return_value.get_failure_details.return_value = None
        mock_conforma.return_value.get_violation_details.return_value = None
        mock_ai.return_value.get_analysis_by_component.return_value = None
        from mcp_server.tools import export_slack
        result = _run(export_slack, 'ghost')
        assert 'No unresolved failures' in result

    @patch('mcp_server.tools._ai_repo')
    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_build_failure_with_analysis(self, mock_build, mock_conforma, mock_ai):
        mock_build.return_value.get_failure_details.return_value = {
            'component_name': 'comp-sa',
            'pipelinerun_name': 'pr-sa',
            'error_message': 'error',
            'error_type': None,
            'failed_step_name': None,
            'repository_url': None,
            'branch': None,
            'commit_sha': None,
            'commit_url': None,
            'commit_author': None,
            'first_detected_at': None,
            'konflux_url': 'http://k',
        }
        mock_ai.return_value.get_analysis_by_component.return_value = {
            'analysis_type': 'build',
            'component_name': 'comp-sa',
            'model_used': 'claude',
            'root_cause': 'dep issue',
            'failure_category': 'dependency',
            'confidence_score': 0.85,
            'recommended_fix': 'fix deps',
            'recommended_files': None,
            'can_auto_fix': True,
            'requires_human_review': False,
            'analyzed_at': _now(),
            'langfuse_trace_url': None,
            'tokens_used': 50,
            'cost_usd': 0.005,
            'analysis_json': None,
        }
        from mcp_server.tools import export_slack
        result = _run(export_slack, 'comp-sa')
        assert ':robot_face:' in result
        assert '85%' in result
        assert 'dep issue' in result


# ---------------------------------------------------------------------------
# _extract_from_analysis_json — extended
# ---------------------------------------------------------------------------

class TestExtractFromAnalysisJsonExtended:

    def _fn(self):
        from mcp_server.tools import _extract_from_analysis_json
        return _extract_from_analysis_json

    def test_dict_without_tool_calls(self):
        """A dict without 'tool_calls' should be treated as a single item."""
        data = {
            'input': {
                'evidence_references': [
                    {'type': 'commit', 'url': 'http://c', 'description': 'd'}
                ],
                'fix_action_type': 'file_change',
            }
        }
        evidence, transparency, fix_type = self._fn()(data)
        assert len(evidence) == 1
        assert fix_type == 'file_change'

    def test_nested_json_string(self):
        inner = {
            'tool_calls': [{
                'input': {
                    'evidence_references': [
                        {'type': 'log', 'url': '', 'description': 'test'}
                    ],
                }
            }]
        }
        evidence, _, _ = self._fn()(json.dumps(inner))
        assert len(evidence) == 1

    def test_empty_string(self):
        evidence, transparency, fix_type = self._fn()('')
        assert evidence == []


# ---------------------------------------------------------------------------
# _row_to_analysis — extended
# ---------------------------------------------------------------------------

class TestRowToAnalysisExtended:

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

    def test_missing_model_used(self):
        row = self._minimal_row()
        del row['model_used']
        result = self._fn()(row)
        assert result.model_used == ''

    def test_missing_root_cause(self):
        row = self._minimal_row()
        del row['root_cause']
        result = self._fn()(row)
        assert result.root_cause == ''

    def test_tokens_none(self):
        row = self._minimal_row()
        row['tokens_used'] = None
        result = self._fn()(row)
        assert result.tokens_used == 0


# ---------------------------------------------------------------------------
# get_watched_applications
# ---------------------------------------------------------------------------

class TestGetWatchedApplications:

    def test_returns_list(self):
        with patch('config.CollectorConfig') as mock_cfg_cls, \
             patch('repositories.config_repository.ConfigRepository') as mock_repo_cls, \
             patch('repositories.connection.DatabaseConnection') as mock_db_cls:
            mock_cfg_cls.from_env.return_value.db = MagicMock()
            mock_repo_cls.return_value.get_watched_applications.return_value = [
                'rhoai-v3-5', 'rhoai-v3-6',
            ]
            from mcp_server.tools import get_watched_applications
            result = get_watched_applications()
        assert result['applications'] == ['rhoai-v3-5', 'rhoai-v3-6']


# ---------------------------------------------------------------------------
# list_skills
# ---------------------------------------------------------------------------

class TestListSkills:

    @patch('mcp_server.tools._skill_registry')
    def test_list_all(self, mock_registry):
        mock_entry = MagicMock()
        mock_entry.qualified_name = 'src/fix-hermetic'
        mock_entry.name = 'fix-hermetic'
        mock_entry.source = 'src'
        mock_entry.metadata.description = 'Fix hermetic task'
        mock_entry.status = 'active'
        mock_entry.tags = ['conforma']
        mock_entry.metadata.category = 'fix'
        mock_entry.metadata.allowed_tools = 'bash'
        mock_entry.metadata.user_invocable = True

        mock_registry.return_value.list_skills.return_value = [mock_entry]
        from mcp_server.tools import list_skills
        result = _run(list_skills)
        assert len(result) == 1
        assert result[0].name == 'fix-hermetic'
        assert result[0].tags == ['conforma']
        assert result[0].user_invocable is True

    @patch('mcp_server.tools._skill_registry')
    def test_filter_by_tag(self, mock_registry):
        mock_registry.return_value.list_skills.return_value = []
        from mcp_server.tools import list_skills
        result = _run(list_skills, tag='onboarding')
        mock_registry.return_value.list_skills.assert_called_once_with(
            tag='onboarding', source=None,
        )
        assert result == []

    @patch('mcp_server.tools._skill_registry')
    def test_filter_by_source(self, mock_registry):
        mock_registry.return_value.list_skills.return_value = []
        from mcp_server.tools import list_skills
        result = _run(list_skills, source='aiops-infra')
        mock_registry.return_value.list_skills.assert_called_once_with(
            tag=None, source='aiops-infra',
        )


# ---------------------------------------------------------------------------
# get_ai_quality_metrics
# ---------------------------------------------------------------------------

class TestGetAiQualityMetrics:

    @patch('mcp_server.tools._db_connection')
    def test_delegates(self, mock_db):
        mock_conn = MagicMock()
        mock_db.return_value = mock_conn
        with patch('repositories.ai_analysis_repository.AIAnalysisRepository') as mock_repo_cls:
            mock_repo_cls.return_value.get_quality_metrics.return_value = {
                'total_verdicts': 10, 'correct': 8, 'accuracy': 0.8,
            }
            from mcp_server.tools import get_ai_quality_metrics
            result = get_ai_quality_metrics(application='app', days=14)
        assert result['accuracy'] == 0.8
        mock_repo_cls.return_value.get_quality_metrics.assert_called_once_with(
            application='app', days=14,
        )

    @patch('mcp_server.tools._db_connection')
    def test_default_days(self, mock_db):
        mock_conn = MagicMock()
        mock_db.return_value = mock_conn
        with patch('repositories.ai_analysis_repository.AIAnalysisRepository') as mock_repo_cls:
            mock_repo_cls.return_value.get_quality_metrics.return_value = {}
            from mcp_server.tools import DEFAULT_APPLICATION, get_ai_quality_metrics
            get_ai_quality_metrics()
        mock_repo_cls.return_value.get_quality_metrics.assert_called_once_with(
            application=DEFAULT_APPLICATION, days=30,
        )


# ---------------------------------------------------------------------------
# list_freezes
# ---------------------------------------------------------------------------

class TestListFreezes:

    @patch('mcp_server.tools._db_connection')
    def test_empty(self, mock_db):
        mock_conn_ctx = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn_ctx.__enter__ = MagicMock(return_value=MagicMock(cursor=MagicMock(return_value=mock_cursor)))
        mock_conn_ctx.__exit__ = MagicMock(return_value=False)
        mock_db.return_value.connection.return_value = mock_conn_ctx
        from mcp_server.tools import list_freezes
        result = _run(list_freezes)
        assert result == []

    @patch('mcp_server.tools._db_connection')
    def test_with_freezes(self, mock_db):
        from datetime import date
        mock_cursor = MagicMock()
        today = date.today()
        mock_cursor.fetchall.return_value = [
            (1, today - timedelta(days=2), today + timedelta(days=2), 'RC freeze'),
            (2, today + timedelta(days=10), today + timedelta(days=15), 'GA freeze'),
            (3, today - timedelta(days=30), today - timedelta(days=20), 'Old freeze'),
        ]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn_ctx = MagicMock()
        mock_conn_ctx.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn_ctx.__exit__ = MagicMock(return_value=False)
        mock_db.return_value.connection.return_value = mock_conn_ctx

        from mcp_server.tools import list_freezes
        result = _run(list_freezes)
        assert len(result) == 3
        assert result[0]['status'] == 'active'
        assert result[1]['status'] == 'upcoming'
        assert result[2]['status'] == 'past'


# ---------------------------------------------------------------------------
# get_active_freeze
# ---------------------------------------------------------------------------

class TestGetActiveFreeze:

    @patch('mcp_server.tools._db_connection')
    def test_no_freeze(self, mock_db):
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn_ctx = MagicMock()
        mock_conn_ctx.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn_ctx.__exit__ = MagicMock(return_value=False)
        mock_db.return_value.connection.return_value = mock_conn_ctx

        from mcp_server.tools import get_active_freeze
        result = _run(get_active_freeze)
        assert result is None

    @patch('mcp_server.tools._db_connection')
    def test_active_freeze(self, mock_db):
        from datetime import date
        today = date.today()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (
            1, today - timedelta(days=1), today + timedelta(days=3), 'Code freeze',
        )
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn_ctx = MagicMock()
        mock_conn_ctx.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn_ctx.__exit__ = MagicMock(return_value=False)
        mock_db.return_value.connection.return_value = mock_conn_ctx

        from mcp_server.tools import get_active_freeze
        result = _run(get_active_freeze)
        assert result is not None
        assert result['status'] == 'active'
        assert result['reason'] == 'Code freeze'


# ---------------------------------------------------------------------------
# get_release_schedule
# ---------------------------------------------------------------------------

class TestGetReleaseSchedule:

    @patch('mcp_server.tools._db_connection')
    def test_not_found(self, mock_db):
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn_ctx = MagicMock()
        mock_conn_ctx.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn_ctx.__exit__ = MagicMock(return_value=False)
        mock_db.return_value.connection.return_value = mock_conn_ctx

        from mcp_server.tools import get_release_schedule
        result = _run(get_release_schedule, application='app')
        assert result is None

    @patch('mcp_server.tools._db_connection')
    def test_found(self, mock_db):
        from datetime import date
        today = date.today()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (
            today + timedelta(days=5),   # planning_freeze
            today + timedelta(days=10),  # feature_freeze
            today + timedelta(days=15),  # code_freeze
            today + timedelta(days=20),  # initial_rc
            today + timedelta(days=25),  # release_window_start
            today + timedelta(days=30),  # release_date
            'rhoai-v3-6',               # next_release
            _now(),                       # updated_at
        )
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn_ctx = MagicMock()
        mock_conn_ctx.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn_ctx.__exit__ = MagicMock(return_value=False)
        mock_db.return_value.connection.return_value = mock_conn_ctx

        from mcp_server.tools import get_release_schedule
        result = _run(get_release_schedule, application='rhoai-v3-5')
        assert result is not None
        assert result['application'] == 'rhoai-v3-5'
        assert result['next_release'] == 'rhoai-v3-6'
        assert result['code_freeze_days'] == 15


# ---------------------------------------------------------------------------
# _format_build_jira helper
# ---------------------------------------------------------------------------

class TestFormatBuildJira:

    def test_with_analysis(self):
        from mcp_server.tools import _format_build_jira
        failure = {
            'component_name': 'comp-a',
            'pipelinerun_name': 'pr-1',
            'error_message': 'error msg',
            'error_type': 'build_error',
            'failed_step_name': 'compile',
            'repository_url': 'http://github.com/org/repo',
            'branch': 'main',
            'commit_sha': 'abc12345',
            'commit_url': 'http://github.com/c/abc12345',
            'commit_message': 'fix stuff',
            'first_detected_at': _now(),
            'last_updated_at': _now(),
            'konflux_url': 'http://konflux/pr-1',
        }
        analysis = {
            'analysis_type': 'build',
            'component_name': 'comp-a',
            'model_used': 'claude',
            'root_cause': 'missing dep',
            'failure_category': 'dependency_issue',
            'confidence_score': 0.9,
            'recommended_fix': 'add dep',
            'recommended_files': ['go.mod'],
            'can_auto_fix': True,
            'requires_human_review': False,
            'analyzed_at': _now(),
            'langfuse_trace_url': 'http://trace/1',
            'tokens_used': 100,
            'cost_usd': 0.01,
            'analysis_json': None,
        }
        result = _format_build_jira(failure, analysis)
        assert 'h2. Build Failure: comp-a' in result
        assert 'h3. AI Analysis' in result
        assert '90%' in result
        assert 'go.mod' in result
        assert 'Analysis Trace' in result

    def test_without_analysis(self):
        from mcp_server.tools import _format_build_jira
        failure = {
            'component_name': 'comp-b',
            'pipelinerun_name': 'pr-2',
            'error_message': None,
            'error_type': None,
            'failed_step_name': None,
            'repository_url': None,
            'branch': None,
            'commit_sha': None,
            'commit_url': None,
            'commit_message': None,
            'first_detected_at': _now(),
            'last_updated_at': _now(),
            'konflux_url': None,
        }
        result = _format_build_jira(failure, None)
        assert 'h2. Build Failure: comp-b' in result
        assert 'AI Analysis' not in result


# ---------------------------------------------------------------------------
# _format_conforma_jira helper
# ---------------------------------------------------------------------------

class TestFormatConformaJira:

    def test_basic(self):
        with patch('conforma.policy_tools.count_unique_violations', return_value=(3, [])):
            from mcp_server.tools import _format_conforma_jira
            violation = {
                'component_name': 'comp-v',
                'scenario': 'verify-conforma-stage',
                'violations_count': 5,
                'warnings_count': 2,
                'violation_summary': 'FAIL: rule.x',
                'pipelinerun_name': 'pr-v',
                'snapshot_name': 'snap-1',
                'first_detected_at': _now(),
                'last_updated_at': _now(),
            }
            result = _format_conforma_jira(violation, None)
        assert 'Conforma Policy Violation: comp-v' in result
        assert 'verify-conforma-stage' in result
        assert '3 unique violations' in result


# ---------------------------------------------------------------------------
# _format_release_jira helper
# ---------------------------------------------------------------------------

class TestFormatReleaseJira:

    def test_with_all_fields(self):
        from mcp_server.tools import _format_release_jira
        analysis = {
            'root_cause': 'stale image SHA',
            'recommended_fix': 'trigger rebuild',
            'failure_category': 'release_failure',
            'confidence_score': 0.85,
            'can_auto_fix': True,
            'requires_human_review': False,
            'recommended_files': 'a.py, b.py',
            'analysis_json': json.dumps([{
                'input': {
                    'evidence_references': [
                        {'type': 'log', 'url': 'http://log', 'description': 'build log'},
                    ],
                    'source_transparency': {
                        'sources_consulted': ['logs', 'registry'],
                        'sources_unavailable': [],
                        'limitations': ['no access to prod'],
                    },
                    'fix_action_type': 'rebuild',
                    'affected_images': ['quay.io/org/img:sha'],
                    'owner_team': 'build-team',
                }
            }]),
            'langfuse_trace_id': 'trace-123',
        }
        result = _format_release_jira('release-v1', analysis)
        assert 'Release Failure: release-v1' in result
        assert 'stale image SHA' in result
        assert 'rebuild' in result
        assert 'build-team' in result
        assert 'Affected Images' in result
        assert 'Analysis Transparency' in result
        assert 'Acceptance Criteria' in result
        assert 'trace-123' in result

    def test_rebuild_acceptance_criteria(self):
        from mcp_server.tools import _format_release_jira
        analysis = {
            'root_cause': 'stale',
            'recommended_fix': 'rebuild',
            'failure_category': 'release',
            'confidence_score': 0.5,
            'can_auto_fix': False,
            'requires_human_review': True,
            'recommended_files': None,
            'analysis_json': json.dumps([{
                'input': {'fix_action_type': 'rebuild'}
            }]),
            'langfuse_trace_id': None,
        }
        result = _format_release_jira('rel', analysis)
        assert 'Component is rebuilt successfully' in result

    def test_file_change_acceptance_criteria(self):
        from mcp_server.tools import _format_release_jira
        analysis = {
            'root_cause': 'config error',
            'recommended_fix': 'fix config',
            'failure_category': 'config',
            'confidence_score': 0.7,
            'can_auto_fix': False,
            'requires_human_review': True,
            'recommended_files': None,
            'analysis_json': json.dumps([{
                'input': {'fix_action_type': 'file_change'}
            }]),
            'langfuse_trace_id': None,
        }
        result = _format_release_jira('rel', analysis)
        assert 'Fix PR is merged' in result


# ---------------------------------------------------------------------------
# _konflux_url helper
# ---------------------------------------------------------------------------

class TestKonfluxUrlExtended:

    def test_empty_name(self):
        from mcp_server.tools import _konflux_url
        url = _konflux_url('')
        assert '/logs' in url

    def test_special_characters(self):
        from mcp_server.tools import _konflux_url
        url = _konflux_url('pr-abc-123-xyz')
        assert 'pr-abc-123-xyz' in url


# ---------------------------------------------------------------------------
# async_tool decorator — extended
# ---------------------------------------------------------------------------

class TestAsyncToolDecoratorExtended:

    def test_preserves_wrapped(self):
        from mcp_server.tools import async_tool

        def original():
            return 42

        wrapped = async_tool(original)
        # __wrapped__ is set by functools.wraps
        assert wrapped.__wrapped__ is original

    def test_passes_kwargs(self):
        from mcp_server.tools import async_tool

        def fn_with_kwargs(a, b=10):
            return a + b

        wrapped = async_tool(fn_with_kwargs)
        result = asyncio.get_event_loop().run_until_complete(wrapped(5, b=20))
        assert result == 25
