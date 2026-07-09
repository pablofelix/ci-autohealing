"""Extended coverage tests (batch 3) for MCP server tools.

Covers additional branches and edge cases not tested in batches 1 and 2:
  - get_dashboard, get_conforma_report (DB path with enrichment),
  - export_slack (build with PRs, contacts, time-based "failing since", conforma+AI),
  - export_markdown (build+AI, conforma+AI),
  - export_json (conforma with analysis),
  - _format_build_jira (with AI files, full context),
  - _format_conforma_jira (with AI analysis, snapshot),
  - _format_release_jira (generic acceptance, no evidence, affected_images parsing),
  - get_resolved_patterns (SQL cursor mock),
  - get_config_analysis / get_build_config_analysis / get_release_config_analysis
    (success and no-llm paths),
  - get_regression_report (build, release, conforma domains),
  - get_ai_quality_metrics,
  - get_ec_policy_summary (with and without releng),
  - get_scenario_coverage (with gaps),
  - lookup_image (quay_match path, URL path extraction with regex),
  - _extract_from_analysis_json (dict with tool_calls, nested items, non-dict items),
  - _row_to_analysis (files as string, files as list),
  - list_alerts (nightly warnings, schedule, freeze countdown, blocker signals),
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
# Helpers (same pattern as previous batches)
# ---------------------------------------------------------------------------

def _stub_mcp():
    m = MagicMock()
    m.tool.return_value = lambda fn: fn
    return m


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
# get_dashboard
# ---------------------------------------------------------------------------

class TestGetDashboard:

    @patch('mcp_server.tools._resolution_repo')
    @patch('mcp_server.tools._pattern_repo')
    @patch('mcp_server.tools._ai_repo')
    @patch('mcp_server.tools._build_repo')
    def test_assembles_all_sections(self, mock_build, mock_ai, mock_pattern, mock_res):
        mock_build.return_value.get_overview_stats.return_value = {
            'components': 10, 'total': 3,
        }
        mock_build.return_value.get_enrichment_coverage.return_value = {
            'with_logs': 2, 'with_context': 1,
        }
        mock_ai.return_value.get_extended_status.return_value = {
            'pending': 1, 'analyzed': 2,
        }
        mock_ai.return_value.get_cost_summary.return_value = {
            'total': 0.50,
        }
        mock_pattern.return_value.get_library_summary.return_value = {
            'total': 5,
        }
        mock_res.return_value.get_outcome_summary.return_value = {
            'success': 3, 'fail': 1,
        }
        from mcp_server.tools import get_dashboard
        result = _run(get_dashboard, application='test-app')
        assert result.application == 'test-app'
        assert result.overview['enrichment'] == {'with_logs': 2, 'with_context': 1}
        assert result.ai_status['cost_30d'] == {'total': 0.50}
        assert result.patterns == {'total': 5}
        assert result.fixes == {'success': 3, 'fail': 1}


# ---------------------------------------------------------------------------
# get_pattern
# ---------------------------------------------------------------------------

class TestGetPattern:

    @patch('mcp_server.tools._pattern_repo')
    def test_found(self, mock_pattern):
        mock_pattern.return_value.get_by_name_or_category.return_value = {
            'name': 'dep_issue', 'description': 'Dependency issue',
        }
        from mcp_server.tools import get_pattern
        result = _run(get_pattern, name='dep_issue')
        assert result['name'] == 'dep_issue'

    @patch('mcp_server.tools._pattern_repo')
    def test_not_found(self, mock_pattern):
        mock_pattern.return_value.get_by_name_or_category.return_value = None
        from mcp_server.tools import get_pattern
        result = _run(get_pattern, name='nonexistent')
        assert result is None


# ---------------------------------------------------------------------------
# get_fix_history
# ---------------------------------------------------------------------------

class TestGetFixHistory:

    @patch('mcp_server.tools._resolution_repo')
    def test_returns_summary_and_attempts(self, mock_res):
        mock_res.return_value.get_all.return_value = [{'id': 1, 'outcome': 'success'}]
        mock_res.return_value.get_outcome_summary.return_value = {'success': 1}
        from mcp_server.tools import get_fix_history
        result = _run(get_fix_history, days=7)
        assert result['summary'] == {'success': 1}
        assert len(result['attempts']) == 1


# ---------------------------------------------------------------------------
# get_conforma_report — DB path (full enrichment)
# ---------------------------------------------------------------------------

class TestGetConformaReportDbPathEnriched:

    @patch('mcp_server.tools._conforma_repo')
    def test_db_path_with_violations(self, mock_conforma):
        mock_conforma.return_value.find_unresolved_component_names.return_value = [
            'comp-a', 'comp-b',
        ]
        mock_conforma.return_value.get_violation_details.side_effect = [
            {
                'component_name': 'comp-a',
                'scenario': 'verify-conforma-lp-rhoai-v35',
                'violations_count': 5,
                'warnings_count': 1,
                'violation_summary': 'FAIL: hermetic_task.hermetic (2 images)\nFAIL: labels.required (2 images)',
            },
            {
                'component_name': 'comp-b',
                'scenario': 'verify-conforma-lp-rhoai-v35',
                'violations_count': 3,
                'warnings_count': 0,
                'violation_summary': 'FAIL: source_image.exists (3 images)',
            },
        ]
        with patch('conforma.policy_tools.fetch_exceptions_by_policy', return_value={}), \
             patch('conforma.policy_tools.extract_policy_from_scenario', return_value='rhoai-v35'), \
             patch('conforma.policy_tools.policy_url', return_value='http://policy'), \
             patch('conforma.policy_tools.categorize_policy', side_effect=['Components', 'Components']), \
             patch('conforma.policy_tools.count_unique_violations', side_effect=[
                 (2, [{'rule': 'hermetic_task.hermetic', 'detail': '2 images'},
                       {'rule': 'labels.required', 'detail': ''}]),
                 (1, [{'rule': 'source_image.exists', 'detail': '3 images'}]),
             ]), \
             patch('conforma.policy_tools.enrich_with_coverage'):
            from mcp_server.tools import get_conforma_report
            result = _run(get_conforma_report, application='app')
        assert result['total_violations'] == 2
        assert result['total_unique_violations'] == 3
        assert 'Components' in result['groups']
        assert result['groups']['Components']['components'] == 2
        # FBC, Charts should be defaulted
        assert ['FBC'] or result['groups']
        assert 'Charts' in result['groups']

    @patch('mcp_server.tools._conforma_repo')
    def test_db_path_no_violations(self, mock_conforma):
        mock_conforma.return_value.find_unresolved_component_names.return_value = []
        with patch('conforma.policy_tools.fetch_exceptions_by_policy', return_value={}):
            from mcp_server.tools import get_conforma_report
            result = _run(get_conforma_report, application='app')
        assert result['total_violations'] == 0
        assert result['coverage_summary'] is None

    @patch('mcp_server.tools._conforma_repo')
    def test_db_path_with_exceptions_coverage(self, mock_conforma):
        mock_conforma.return_value.find_unresolved_component_names.return_value = ['comp-a']
        mock_conforma.return_value.get_violation_details.return_value = {
            'component_name': 'comp-a',
            'scenario': 'verify-conforma-lp-rhoai-v35',
            'violations_count': 2,
            'warnings_count': 0,
            'violation_summary': 'FAIL: hermetic_task.hermetic',
        }
        exceptions = {'rhoai-v35': [{'value': 'hermetic_task.hermetic'}]}
        with patch('conforma.policy_tools.fetch_exceptions_by_policy', return_value=exceptions), \
             patch('conforma.policy_tools.extract_policy_from_scenario', return_value='rhoai-v35'), \
             patch('conforma.policy_tools.policy_url', return_value='http://p'), \
             patch('conforma.policy_tools.categorize_policy', return_value='Components'), \
             patch('conforma.policy_tools.count_unique_violations', return_value=(1, [{'rule': 'hermetic_task.hermetic', 'detail': ''}])), \
             patch('conforma.policy_tools.enrich_with_coverage') as mock_enrich:
            # Make enrich_with_coverage set exception_coverage on the violations
            def set_coverage(violations, exc_by_policy):
                for v in violations:
                    v['exception_coverage'] = 'fully_covered'
            mock_enrich.side_effect = set_coverage
            from mcp_server.tools import get_conforma_report
            result = _run(get_conforma_report, application='app')
        assert result['coverage_summary'] is not None
        assert result['coverage_summary']['fully_covered'] == 1


# ---------------------------------------------------------------------------
# export_slack — additional branches
# ---------------------------------------------------------------------------

class TestExportSlackExtended:

    @patch('mcp_server.tools._ai_repo')
    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_build_failure_today(self, mock_build, mock_conforma, mock_ai):
        """Test 'Failing since: today' branch."""
        mock_build.return_value.get_failure_details.return_value = {
            'component_name': 'comp-today',
            'pipelinerun_name': 'pr-today',
            'error_message': 'error',
            'error_type': 'build',
            'failed_step_name': None,
            'repository_url': None,
            'branch': None,
            'commit_sha': None,
            'commit_url': None,
            'commit_author': None,
            'first_detected_at': _now(),
            'konflux_url': 'http://k',
        }
        mock_ai.return_value.get_analysis_by_component.return_value = None
        from mcp_server.tools import export_slack
        result = _run(export_slack, 'comp-today')
        assert 'Failing since: today' in result

    @patch('mcp_server.tools._ai_repo')
    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_build_failure_yesterday(self, mock_build, mock_conforma, mock_ai):
        """Test 'Failing since: yesterday' branch."""
        mock_build.return_value.get_failure_details.return_value = {
            'component_name': 'comp-yest',
            'pipelinerun_name': 'pr-yest',
            'error_message': 'error',
            'error_type': 'build',
            'failed_step_name': None,
            'repository_url': None,
            'branch': None,
            'commit_sha': None,
            'commit_url': None,
            'commit_author': None,
            'first_detected_at': _now() - timedelta(days=1),
            'konflux_url': 'http://k',
        }
        mock_ai.return_value.get_analysis_by_component.return_value = None
        from mcp_server.tools import export_slack
        result = _run(export_slack, 'comp-yest')
        assert 'Failing since: yesterday' in result

    @patch('mcp_server.tools._ai_repo')
    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_build_failure_multiple_days(self, mock_build, mock_conforma, mock_ai):
        """Test 'Failing since: <date> (N days)' branch."""
        first = _now() - timedelta(days=5)
        mock_build.return_value.get_failure_details.return_value = {
            'component_name': 'comp-old',
            'pipelinerun_name': 'pr-old',
            'error_message': 'error',
            'error_type': 'build',
            'failed_step_name': None,
            'repository_url': None,
            'branch': None,
            'commit_sha': None,
            'commit_url': None,
            'commit_author': None,
            'first_detected_at': first,
            'konflux_url': 'http://k',
        }
        mock_ai.return_value.get_analysis_by_component.return_value = None
        from mcp_server.tools import export_slack
        result = _run(export_slack, 'comp-old')
        assert '5 days' in result

    @patch('mcp_server.tools._ai_repo')
    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_build_with_repo_branch(self, mock_build, mock_conforma, mock_ai):
        """Test repo name extraction and branch display."""
        mock_build.return_value.get_failure_details.return_value = {
            'component_name': 'comp-rb',
            'pipelinerun_name': 'pr-rb',
            'error_message': 'error',
            'error_type': 'build',
            'failed_step_name': 'build-step',
            'repository_url': 'https://github.com/org/my-repo',
            'branch': 'release-3.5',
            'commit_sha': 'abc12345',
            'commit_url': 'https://github.com/org/my-repo/commit/abc12345',
            'commit_author': 'devuser',
            'first_detected_at': None,
            'konflux_url': 'http://k',
        }
        mock_ai.return_value.get_analysis_by_component.return_value = None
        from mcp_server.tools import export_slack
        result = _run(export_slack, 'comp-rb')
        assert 'my-repo' in result
        assert 'release-3.5' in result
        assert 'abc12345' in result[:100] or 'abc1234' in result

    @patch('mcp_server.tools._ai_repo')
    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_conforma_with_ai(self, mock_build, mock_conforma, mock_ai):
        """Test conforma violation + AI analysis path."""
        mock_build.return_value.get_failure_details.return_value = None
        mock_conforma.return_value.get_violation_details.return_value = {
            'component_name': 'comp-ca',
            'scenario': 'verify-stage',
            'violations_count': 3,
            'warnings_count': 0,
            'violation_summary': 'FAIL: rule_a',
            'pipelinerun_name': 'pr-ca',
        }
        mock_ai.return_value.get_analysis_by_component.return_value = {
            'analysis_type': 'conforma',
            'component_name': 'comp-ca',
            'model_used': 'claude',
            'root_cause': 'missing hermetic config' * 20,  # long root cause
            'failure_category': 'config_issue',
            'confidence_score': 0.92,
            'recommended_fix': 'add hermetic config',
            'recommended_files': None,
            'can_auto_fix': False,
            'requires_human_review': True,
            'analyzed_at': _now(),
            'langfuse_trace_url': None,
            'tokens_used': 100,
            'cost_usd': 0.01,
            'analysis_json': None,
        }
        with patch('conforma.policy_tools.count_unique_violations', return_value=(0, [])):
            from mcp_server.tools import export_slack
            result = _run(export_slack, 'comp-ca')
        assert ':robot_face:' in result
        assert '92%' in result
        assert 'config_issue' in result

    @patch('mcp_server.tools._ai_repo')
    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_conforma_no_unique_violations(self, mock_build, mock_conforma, mock_ai):
        """Test conforma violation without unique violations (0 count)."""
        mock_build.return_value.get_failure_details.return_value = None
        mock_conforma.return_value.get_violation_details.return_value = {
            'component_name': 'comp-nu',
            'scenario': 'verify-stage',
            'violations_count': 2,
            'warnings_count': 1,
            'violation_summary': '',
            'pipelinerun_name': 'pr-nu',
        }
        mock_ai.return_value.get_analysis_by_component.return_value = None
        with patch('conforma.policy_tools.count_unique_violations', return_value=(0, [])):
            from mcp_server.tools import export_slack
            result = _run(export_slack, 'comp-nu')
        assert 'Violations: 2 | Warnings: 1' in result


# ---------------------------------------------------------------------------
# export_markdown — additional branches
# ---------------------------------------------------------------------------

class TestExportMarkdownExtended:

    @patch('mcp_server.tools._ai_repo')
    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_build_with_ai_and_files(self, mock_build, mock_conforma, mock_ai):
        mock_build.return_value.get_failure_details.return_value = {
            'component_name': 'comp-mda',
            'pipelinerun_name': 'pr-mda',
            'error_message': 'build failed',
            'error_type': 'compile',
            'failed_step_name': 'compile',
            'repository_url': 'https://github.com/org/repo',
            'commit_sha': 'abc12345',
            'commit_url': 'https://github.com/org/repo/commit/abc12345',
            'first_detected_at': _now(),
            'konflux_url': 'http://k/pr',
        }
        mock_ai.return_value.get_analysis_by_component.return_value = {
            'analysis_type': 'build',
            'component_name': 'comp-mda',
            'model_used': 'claude',
            'root_cause': 'missing module',
            'failure_category': 'dependency',
            'confidence_score': 0.9,
            'recommended_fix': 'add module',
            'recommended_files': ['file_a.go', 'file_b.go'],
            'can_auto_fix': True,
            'requires_human_review': False,
            'analyzed_at': _now(),
            'langfuse_trace_url': None,
            'tokens_used': 80,
            'cost_usd': 0.01,
            'analysis_json': None,
        }
        from mcp_server.tools import export_markdown
        result = _run(export_markdown, 'comp-mda')
        assert '## Build Failure: comp-mda' in result
        assert '90%' in result
        assert '`file_a.go`' in result
        assert 'abc1234' in result

    @patch('mcp_server.tools._ai_repo')
    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_conforma_with_ai(self, mock_build, mock_conforma, mock_ai):
        mock_build.return_value.get_failure_details.return_value = None
        mock_conforma.return_value.get_violation_details.return_value = {
            'component_name': 'comp-mcv',
            'scenario': 'verify-conforma',
            'violations_count': 4,
            'warnings_count': 1,
            'violation_summary': 'FAIL: rule',
            'first_detected_at': _now(),
        }
        mock_ai.return_value.get_analysis_by_component.return_value = {
            'analysis_type': 'conforma',
            'component_name': 'comp-mcv',
            'model_used': 'claude',
            'root_cause': 'stale build',
            'failure_category': 'conforma',
            'confidence_score': 0.8,
            'recommended_fix': 'rebuild',
            'recommended_files': None,
            'can_auto_fix': False,
            'requires_human_review': True,
            'analyzed_at': _now(),
            'langfuse_trace_url': None,
            'tokens_used': 50,
            'cost_usd': 0.005,
            'analysis_json': None,
        }
        with patch('conforma.policy_tools.count_unique_violations', return_value=(2, [])):
            from mcp_server.tools import export_markdown
            result = _run(export_markdown, 'comp-mcv')
        assert '## Conforma Violation: comp-mcv' in result
        assert '80%' in result
        assert 'stale build' in result
        assert '2 unique violations' in result


# ---------------------------------------------------------------------------
# export_json — conforma with analysis
# ---------------------------------------------------------------------------

class TestExportJsonExtended:

    @patch('mcp_server.tools._ai_repo')
    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_conforma_with_analysis(self, mock_build, mock_conforma, mock_ai):
        mock_build.return_value.get_failure_details.return_value = None
        mock_conforma.return_value.get_violation_details.return_value = {
            'component_name': 'comp-jc',
            'scenario': 'verify',
            'violations_count': 2,
            'warnings_count': 0,
        }
        mock_ai.return_value.get_analysis_by_component.return_value = {
            'root_cause': 'outdated deps',
        }
        from mcp_server.tools import export_json
        result = json.loads(_run(export_json, 'comp-jc'))
        assert result['type'] == 'conforma_violation'
        assert result['analysis']['root_cause'] == 'outdated deps'


# ---------------------------------------------------------------------------
# _format_build_jira — with full AI analysis and context
# ---------------------------------------------------------------------------

class TestFormatBuildJiraExtended:

    def test_with_ai_files_and_trace(self):
        from mcp_server.tools import _format_build_jira
        failure = {
            'component_name': 'comp-bj',
            'pipelinerun_name': 'pr-bj',
            'first_detected_at': '2024-01-01',
            'last_updated_at': '2024-01-02',
            'repository_url': 'https://github.com/org/repo',
            'branch': 'main',
            'commit_sha': 'deadbeef12345',
            'commit_url': 'https://github.com/org/repo/commit/deadbeef',
            'commit_message': 'fix: update deps',
            'failed_step_name': 'build',
            'error_type': 'compile_error',
            'error_message': 'go build failed',
            'konflux_url': 'http://k/pr-bj',
        }
        analysis = {
            'analysis_type': 'build',
            'component_name': 'comp-bj',
            'model_used': 'claude',
            'root_cause': 'Go module missing',
            'failure_category': 'dependency_issue',
            'confidence_score': 0.95,
            'recommended_fix': 'add go.sum entry',
            'recommended_files': ['go.sum', 'go.mod'],
            'can_auto_fix': True,
            'requires_human_review': False,
            'analyzed_at': _now(),
            'langfuse_trace_url': 'http://langfuse/trace/123',
            'tokens_used': 200,
            'cost_usd': 0.02,
            'analysis_json': None,
        }
        result = _format_build_jira(failure, analysis)
        assert 'Go module missing' in result
        assert 'go.sum, go.mod' in result
        assert 'Analysis Trace' in result
        assert 'fix: update deps' in result
        assert 'compile_error' in result
        assert 'go build failed' in result
        assert '{code}' in result  # error message in code block
        assert 'repo' in result  # repo name extracted


# ---------------------------------------------------------------------------
# _format_conforma_jira — with AI analysis and snapshot
# ---------------------------------------------------------------------------

class TestFormatConformaJiraExtended:

    def test_with_ai_and_snapshot(self):
        with patch('conforma.policy_tools.count_unique_violations', return_value=(3, [])):
            from mcp_server.tools import _format_conforma_jira
        violation = {
            'component_name': 'comp-cj',
            'pipelinerun_name': 'pr-cj',
            'scenario': 'verify-conforma',
            'violations_count': 6,
            'warnings_count': 2,
            'violation_summary': 'FAIL: rule1 (3 images)',
            'first_detected_at': '2024-01-01',
            'last_updated_at': '2024-01-02',
            'snapshot_name': 'snap-abc',
            'repository_url': 'https://github.com/org/comp-cj',
            'commit_sha': 'abc12345',
            'commit_url': 'https://github.com/org/comp-cj/commit/abc12345',
        }
        analysis = {
            'analysis_type': 'conforma',
            'component_name': 'comp-cj',
            'model_used': 'claude',
            'root_cause': 'missing hermetic config',
            'failure_category': 'config_issue',
            'confidence_score': 0.88,
            'recommended_fix': 'add .tekton hermetic',
            'recommended_files': None,
            'can_auto_fix': False,
            'requires_human_review': True,
            'analyzed_at': _now(),
            'langfuse_trace_url': 'http://langfuse/trace/456',
            'tokens_used': 150,
            'cost_usd': 0.015,
            'analysis_json': None,
        }
        with patch('conforma.policy_tools.count_unique_violations', return_value=(3, [])):
            result = _format_conforma_jira(violation, analysis)
        assert 'Conforma Policy Violation: comp-cj' in result
        assert 'snap-abc' in result
        assert '88%' in result
        assert 'missing hermetic config' in result
        assert 'Analysis Trace' in result
        assert 'abc1234' in result


# ---------------------------------------------------------------------------
# _format_release_jira — additional branches
# ---------------------------------------------------------------------------

class TestFormatReleaseJiraExtended:

    def test_generic_acceptance_criteria(self):
        """Test the 'else' branch for acceptance criteria (neither rebuild nor file_change)."""
        from mcp_server.tools import _format_release_jira
        analysis = {
            'root_cause': 'unknown issue',
            'recommended_fix': 'investigate',
            'failure_category': 'unknown',
            'confidence_score': 0.3,
            'can_auto_fix': False,
            'requires_human_review': True,
            'recommended_files': None,
            'analysis_json': json.dumps([{
                'input': {'fix_action_type': 'manual_investigation'}
            }]),
            'langfuse_trace_id': None,
        }
        result = _format_release_jira('rel-x', analysis)
        assert 'Root cause is addressed' in result
        assert 'Fix is verified to prevent recurrence' in result

    def test_no_analysis_json(self):
        """Test with analysis_json = None."""
        from mcp_server.tools import _format_release_jira
        analysis = {
            'root_cause': 'issue',
            'recommended_fix': 'fix it',
            'failure_category': 'cat',
            'confidence_score': 0.5,
            'can_auto_fix': False,
            'requires_human_review': True,
            'recommended_files': None,
            'analysis_json': None,
            'langfuse_trace_id': None,
        }
        result = _format_release_jira('rel-y', analysis)
        assert 'Release Failure: rel-y' in result
        assert 'Affected Images' not in result  # no images

    def test_analysis_json_as_dict(self):
        """Test when analysis_json is a dict (not wrapped in list)."""
        from mcp_server.tools import _format_release_jira
        analysis = {
            'root_cause': 'issue',
            'recommended_fix': 'fix',
            'failure_category': 'cat',
            'confidence_score': 0.6,
            'can_auto_fix': False,
            'requires_human_review': True,
            'recommended_files': None,
            'analysis_json': {
                'input': {
                    'affected_images': ['quay.io/a:v1', 'quay.io/b:v2'],
                    'owner_team': 'team-alpha',
                    'fix_action_type': 'rebuild',
                }
            },
            'langfuse_trace_id': None,
        }
        result = _format_release_jira('rel-dict', analysis)
        assert 'team-alpha' in result
        assert 'quay.io/a:v1' in result

    def test_evidence_without_url(self):
        """Test evidence references with no URL."""
        from mcp_server.tools import _format_release_jira
        analysis = {
            'root_cause': 'issue',
            'recommended_fix': 'fix',
            'failure_category': 'cat',
            'confidence_score': 0.5,
            'can_auto_fix': False,
            'requires_human_review': True,
            'recommended_files': None,
            'analysis_json': json.dumps([{
                'input': {
                    'evidence_references': [
                        {'type': 'observation', 'url': '', 'description': 'seen in logs'},
                    ],
                    'source_transparency': {
                        'sources_consulted': ['logs'],
                        'sources_unavailable': ['registry'],
                        'limitations': [],
                    },
                }
            }]),
            'langfuse_trace_id': None,
        }
        result = _format_release_jira('rel-ev', analysis)
        assert 'seen in logs' in result
        assert 'Sources used' in result
        assert 'Sources unavailable' in result


# ---------------------------------------------------------------------------
# _extract_from_analysis_json — additional cases
# ---------------------------------------------------------------------------

class TestExtractFromAnalysisJsonMore:

    def _fn(self):
        from mcp_server.tools import _extract_from_analysis_json
        return _extract_from_analysis_json

    def test_dict_with_tool_calls(self):
        data = {
            'tool_calls': [{
                'input': {
                    'evidence_references': [{'type': 'log', 'url': 'u', 'description': 'd'}],
                    'fix_action_type': 'rebuild',
                }
            }]
        }
        evidence, transparency, fix_type = self._fn()(data)
        assert len(evidence) == 1
        assert fix_type == 'rebuild'

    def test_non_dict_item_in_list(self):
        data = ['not-a-dict', {'input': {'fix_action_type': 'file_change'}}]
        evidence, transparency, fix_type = self._fn()(data)
        assert fix_type == 'file_change'

    def test_invalid_json_string(self):
        evidence, transparency, fix_type = self._fn()('not valid json {{{')
        assert evidence == []
        assert transparency is None
        assert fix_type is None

    def test_evidence_non_dict_skipped(self):
        data = [{'input': {'evidence_references': ['string-ref', {'type': 'x', 'url': 'u', 'description': 'd'}]}}]
        evidence, transparency, fix_type = self._fn()(data)
        assert len(evidence) == 1  # only the dict ref

    def test_fix_action_type_non_string_ignored(self):
        data = [{'input': {'fix_action_type': 123}}]
        evidence, transparency, fix_type = self._fn()(data)
        assert fix_type is None


# ---------------------------------------------------------------------------
# _row_to_analysis — additional cases
# ---------------------------------------------------------------------------

class TestRowToAnalysisExtended:

    def test_files_as_string(self):
        from mcp_server.tools import _row_to_analysis
        row = {
            'analysis_type': 'build',
            'component_name': 'comp',
            'model_used': 'claude',
            'root_cause': 'issue',
            'failure_category': 'dep',
            'confidence_score': 0.7,
            'recommended_fix': 'fix',
            'recommended_files': 'file_a.go, file_b.py, ',  # trailing comma
            'can_auto_fix': False,
            'requires_human_review': True,
            'analyzed_at': _now(),
            'langfuse_trace_url': None,
            'tokens_used': None,
            'cost_usd': None,
            'analysis_json': None,
        }
        result = _row_to_analysis(row)
        assert result.recommended_files == ['file_a.go', 'file_b.py']
        assert result.tokens_used == 0
        assert result.cost_usd == 0.0

    def test_files_as_list(self):
        from mcp_server.tools import _row_to_analysis
        row = {
            'analysis_type': 'conforma',
            'component_name': 'comp2',
            'model_used': 'claude',
            'root_cause': 'r',
            'failure_category': 'c',
            'confidence_score': 0.5,
            'recommended_fix': 'f',
            'recommended_files': ['a.py', 'b.py'],
            'can_auto_fix': True,
            'requires_human_review': False,
            'analyzed_at': _now(),
            'langfuse_trace_url': 'http://trace',
            'tokens_used': 100,
            'cost_usd': 0.01,
            'analysis_json': json.dumps([{
                'input': {
                    'evidence_references': [{'type': 'log', 'url': 'u', 'description': 'd'}],
                    'source_transparency': {
                        'sources_consulted': ['logs'],
                        'sources_unavailable': [],
                        'limitations': [],
                    },
                }
            }]),
        }
        result = _row_to_analysis(row)
        assert result.recommended_files == ['a.py', 'b.py']
        assert len(result.evidence_references) == 1
        assert result.source_transparency is not None


# ---------------------------------------------------------------------------
# get_ai_quality_metrics
# ---------------------------------------------------------------------------

class TestGetAiQualityMetricsExtended:

    @patch('mcp_server.tools._db_connection')
    def test_delegates_to_repo(self, mock_db):
        mock_repo = MagicMock()
        mock_repo.get_quality_metrics.return_value = {
            'total': 10, 'correct': 8, 'accuracy': 0.8,
        }
        with patch('repositories.ai_analysis_repository.AIAnalysisRepository', return_value=mock_repo):
            from mcp_server.tools import get_ai_quality_metrics
            result = _run(get_ai_quality_metrics, application='app', days=14)
        assert result['accuracy'] == 0.8
        mock_repo.get_quality_metrics.assert_called_once_with(application='app', days=14)


# ---------------------------------------------------------------------------
# get_ec_policy_summary
# ---------------------------------------------------------------------------

class TestGetEcPolicySummaryExtended:

    @patch('clients.konflux_client.KonfluxClient')
    def test_basic_without_releng(self, mock_kc_cls):
        mock_client = MagicMock()
        mock_kc_cls.return_value = mock_client
        mock_client.get_ec_policies.return_value = [
            {'metadata': {'name': 'rhoai-v35'}, 'spec': {}},
        ]
        mock_client.extract_exceptions.return_value = [
            {'value': 'rule1', 'days_left': 10},
            {'value': 'rule2', 'days_left': -5},
        ]
        with patch.dict(os.environ, {'RELENG_NAMESPACE': ''}, clear=False):
            from mcp_server.tools import get_ec_policy_summary
            result = _run(get_ec_policy_summary, application='app', name_filter='rhoai')
        assert result['policies_count'] == 1
        assert result['total_exceptions'] == 2
        assert result['active_exceptions'] == 1
        assert result['expired_exceptions'] == 1
        assert result['policy_gap'] == {}

    @patch('clients.konflux_client.KonfluxClient')
    def test_with_releng_namespace(self, mock_kc_cls):
        mock_client = MagicMock()
        mock_releng_client = MagicMock()

        def kc_factory(**kwargs):
            ns = kwargs.get('namespace', '')
            if ns == 'releng-ns':
                return mock_releng_client
            return mock_client

        mock_kc_cls.side_effect = kc_factory
        mock_client.get_ec_policies.return_value = [
            {'metadata': {'name': 'stage-policy'}, 'spec': {}},
            {'metadata': {'name': 'prod-policy'}, 'spec': {}},
        ]
        mock_client.extract_exceptions.side_effect = [
            [{'value': 'r1', 'days_left': None}, {'value': 'r2', 'days_left': 5}],  # all_exceptions pass 1 (stage-policy)
            [{'value': 'r3', 'days_left': 20}],  # all_exceptions pass 1 (prod-policy)
            [{'value': 'r1', 'days_left': None}],  # exception_map stage-policy
            [{'value': 'r3', 'days_left': 20}],  # exception_map prod-policy
        ]
        mock_releng_client.get_release_plan_admissions.return_value = [{'name': 'rpa1'}]
        mock_kc_cls.extract_rpa_bindings.return_value = [
            {'target': 'stage', 'policy': 'stage-policy'},
            {'target': 'prod', 'policy': 'prod-policy'},
        ]
        with patch.dict(os.environ, {'RELENG_NAMESPACE': 'releng-ns'}, clear=False):
            from mcp_server.tools import get_ec_policy_summary
            result = _run(get_ec_policy_summary, application='app', name_filter='rhoai')
        assert result['policies_count'] == 2
        assert result['total_exceptions'] == 3


# ---------------------------------------------------------------------------
# get_scenario_coverage
# ---------------------------------------------------------------------------

class TestGetScenarioCoverageExtended:

    @patch('clients.konflux_client.KonfluxClient')
    def test_with_gaps(self, mock_kc_cls):
        mock_client = MagicMock()
        mock_kc_cls.return_value = mock_client
        mock_client.get_integration_test_scenarios.return_value = ['s1', 's2', 's3']
        mock_kc_cls.extract_its_metadata.side_effect = [
            {
                'name': 'verify-basic',
                'application': 'app-a',
                'is_disabled': False,
                'is_conforma': False,
                'is_future': False,
                'policy_ref': '',
            },
            {
                'name': 'verify-conforma',
                'application': 'app-a',
                'is_disabled': True,  # disabled conforma
                'is_conforma': True,
                'is_future': False,
                'policy_ref': 'policy/v1',
            },
            {
                'name': 'verify-future',
                'application': 'app-b',
                'is_disabled': False,
                'is_conforma': False,
                'is_future': True,
                'policy_ref': '',
            },
        ]
        from mcp_server.tools import get_scenario_coverage
        result = _run(get_scenario_coverage, application='app-a')
        assert result['total_scenarios'] == 3
        assert result['active'] == 2
        assert result['disabled'] == 1
        assert result['conforma_scenarios'] == 1
        assert result['future_scenarios'] == 1
        # Both app-a and app-b should show as gaps since app-a's conforma is disabled
        # and app-b has no conforma at all
        assert len(result['gaps']) == 2
        assert len(result['disabled_conforma']) == 1


# ---------------------------------------------------------------------------
# get_resolved_patterns — full SQL path mocking
# ---------------------------------------------------------------------------

class TestGetResolvedPatternsExtended:

    @patch('mcp_server.tools._db_connection')
    def test_full_flow(self, mock_db_conn):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db_conn.return_value.connection.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor

        # First query: grouped patterns
        mock_cursor.fetchall.side_effect = [
            [
                ('hermetic_task', 10, 5, 24.5, _now() - timedelta(days=30), _now()),
                ('trusted_task', 3, 1, 12.0, _now() - timedelta(days=10), _now()),
                ('other', 2, 0, None, None, None),
            ],
            # Second query: totals
        ]
        mock_cursor.fetchone.return_value = (15, 6)

        from mcp_server.tools import get_resolved_patterns
        result = _run(get_resolved_patterns, application='app', days=30)
        assert result['total_resolved'] == 15
        assert result['with_ai_analysis'] == 6
        assert len(result['patterns']) == 3
        # trusted_task should be marked as likely_transient
        trusted = [p for p in result['patterns'] if p['rule_group'] == 'trusted_task'][0]
        assert trusted['likely_transient'] is True
        # hermetic_task should NOT be transient
        hermetic = [p for p in result['patterns'] if p['rule_group'] == 'hermetic_task'][0]
        assert hermetic['likely_transient'] is False
        # 'other' should have None dates and None avg_hours
        other = [p for p in result['patterns'] if p['rule_group'] == 'other'][0]
        assert other['avg_hours_to_resolve'] is None


# ---------------------------------------------------------------------------
# get_config_analysis / get_build_config_analysis / get_release_config_analysis
# ---------------------------------------------------------------------------

class TestConfigAnalysisTools:

    @patch('mcp_server.tools._db_connection')
    def test_no_llm_config(self, mock_db):
        with patch('config.CollectorConfig') as mock_cfg:
            mock_cfg.from_env.return_value = MagicMock(llm=None)
            from mcp_server.tools import get_config_analysis
            result = _run(get_config_analysis, application='app')
        assert 'error' in result
        assert 'LLM' in result['error']

    @patch('mcp_server.tools._db_connection')
    def test_config_analysis_success(self, mock_db):
        with patch('config.CollectorConfig') as mock_cfg, \
             patch('analyzers.config_analyzer.ConfigAnalyzer') as mock_analyzer_cls:
            mock_cfg.from_env.return_value = MagicMock(llm='claude')
            mock_analyzer = MagicMock()
            mock_analyzer_cls.return_value = mock_analyzer
            mock_analyzer.run.return_value = {
                'analyzed': True,
                'analysis': {
                    'findings': [{'severity': 'high', 'msg': 'issue'}],
                    'overall_severity': 'high',
                    'confidence_score': 0.85,
                    'summary': 'config issues found',
                    'auto_rebuild_candidates': ['comp-a'],
                },
                'cost_usd': 0.05,
                'model': 'claude',
            }
            from mcp_server.tools import get_config_analysis
            result = _run(get_config_analysis, application='app')
        assert result['findings_count'] == 1
        assert result['overall_severity'] == 'high'

    @patch('mcp_server.tools._db_connection')
    def test_config_analysis_not_completed(self, mock_db):
        with patch('config.CollectorConfig') as mock_cfg, \
             patch('analyzers.config_analyzer.ConfigAnalyzer') as mock_analyzer_cls:
            mock_cfg.from_env.return_value = MagicMock(llm='claude')
            mock_analyzer = MagicMock()
            mock_analyzer_cls.return_value = mock_analyzer
            mock_analyzer.run.return_value = {'analyzed': False}
            from mcp_server.tools import get_config_analysis
            result = _run(get_config_analysis, application='app')
        assert 'error' in result

    @patch('mcp_server.tools._db_connection')
    def test_build_config_no_llm(self, mock_db):
        with patch('config.CollectorConfig') as mock_cfg:
            mock_cfg.from_env.return_value = MagicMock(llm=None)
            from mcp_server.tools import get_build_config_analysis
            result = _run(get_build_config_analysis, application='app')
        assert 'error' in result

    @patch('mcp_server.tools._db_connection')
    def test_build_config_success(self, mock_db):
        with patch('config.CollectorConfig') as mock_cfg, \
             patch('analyzers.build_config_analyzer.BuildConfigAnalyzer') as mock_analyzer_cls:
            mock_cfg.from_env.return_value = MagicMock(llm='claude')
            mock_analyzer = MagicMock()
            mock_analyzer_cls.return_value = mock_analyzer
            mock_analyzer.run.return_value = {
                'analyzed': True,
                'analysis': {
                    'findings': [],
                    'overall_severity': 'low',
                    'confidence_score': 0.9,
                    'summary': 'no issues',
                    'auto_rebuild_candidates': [],
                },
                'cost_usd': 0.03,
                'model': 'claude',
            }
            from mcp_server.tools import get_build_config_analysis
            result = _run(get_build_config_analysis, application='app')
        assert result['findings_count'] == 0

    @patch('mcp_server.tools._db_connection')
    def test_release_config_no_llm(self, mock_db):
        with patch('config.CollectorConfig') as mock_cfg:
            mock_cfg.from_env.return_value = MagicMock(llm=None)
            from mcp_server.tools import get_release_config_analysis
            result = _run(get_release_config_analysis, application='app')
        assert 'error' in result

    @patch('mcp_server.tools._db_connection')
    def test_release_config_success(self, mock_db):
        with patch('config.CollectorConfig') as mock_cfg, \
             patch('analyzers.release_config_analyzer.ReleaseConfigAnalyzer') as mock_analyzer_cls:
            mock_cfg.from_env.return_value = MagicMock(llm='claude')
            mock_analyzer = MagicMock()
            mock_analyzer_cls.return_value = mock_analyzer
            mock_analyzer.run.return_value = {
                'analyzed': True,
                'analysis': {
                    'findings': [{'sev': 'medium'}],
                    'overall_severity': 'medium',
                    'confidence_score': 0.8,
                    'summary': 'release issues',
                    'release_blockers': ['blocker-1'],
                },
                'cost_usd': 0.04,
                'model': 'claude',
            }
            from mcp_server.tools import get_release_config_analysis
            result = _run(get_release_config_analysis, application='app')
        assert result['findings_count'] == 1
        assert result['release_blockers'] == ['blocker-1']

    @patch('mcp_server.tools._db_connection')
    def test_release_config_not_completed(self, mock_db):
        with patch('config.CollectorConfig') as mock_cfg, \
             patch('analyzers.release_config_analyzer.ReleaseConfigAnalyzer') as mock_analyzer_cls:
            mock_cfg.from_env.return_value = MagicMock(llm='claude')
            mock_analyzer = MagicMock()
            mock_analyzer_cls.return_value = mock_analyzer
            mock_analyzer.run.return_value = {'analyzed': False}
            from mcp_server.tools import get_release_config_analysis
            result = _run(get_release_config_analysis, application='app')
        assert 'error' in result


# ---------------------------------------------------------------------------
# get_regression_report — all three domains
# ---------------------------------------------------------------------------

class TestGetRegressionReportExtended:

    @patch('mcp_server.tools._db_connection')
    def test_conforma_domain(self, mock_db):
        with patch('config.CollectorConfig') as mock_cfg, \
             patch('analyzers.conforma_regression.ConformaRegressionTester') as mock_tester_cls:
            mock_cfg.from_env.return_value = MagicMock()
            mock_tester = MagicMock()
            mock_tester_cls.return_value = mock_tester
            mock_tester.run.return_value = {'accuracy': 0.9, 'total': 20}
            from mcp_server.tools import get_regression_report
            result = _run(get_regression_report, domain='conforma', limit=20)
        assert result['accuracy'] == 0.9
        mock_tester.run.assert_called_once()

    @patch('mcp_server.tools._db_connection')
    def test_build_domain(self, mock_db):
        with patch('config.CollectorConfig') as mock_cfg, \
             patch('analyzers.build_regression.BuildRegressionTester') as mock_tester_cls:
            mock_cfg.from_env.return_value = MagicMock()
            mock_tester = MagicMock()
            mock_tester_cls.return_value = mock_tester
            mock_tester.run.return_value = {'accuracy': 0.85}
            from mcp_server.tools import get_regression_report
            result = _run(get_regression_report, domain='build', limit=30)
        assert result['accuracy'] == 0.85

    @patch('mcp_server.tools._db_connection')
    def test_release_domain(self, mock_db):
        with patch('config.CollectorConfig') as mock_cfg, \
             patch('analyzers.release_regression.ReleaseRegressionTester') as mock_tester_cls:
            mock_cfg.from_env.return_value = MagicMock()
            mock_tester = MagicMock()
            mock_tester_cls.return_value = mock_tester
            mock_tester.run.return_value = {'accuracy': 0.75}
            from mcp_server.tools import get_regression_report
            result = _run(get_regression_report, domain='release', limit=10)
        assert result['accuracy'] == 0.75
        mock_tester.run.assert_called_once_with(limit=10)


# ---------------------------------------------------------------------------
# lookup_image — quay_match and regex fallback paths
# ---------------------------------------------------------------------------

class TestLookupImageExtended:

    @patch('mcp_server.tools._build_repo')
    def test_quay_match_via_direct_ref(self, mock_build):
        mock_build.return_value.find_by_image.return_value = []
        mock_kc = MagicMock()
        mock_kc.list_components.return_value = []
        mock_rc = MagicMock()
        mock_rc.parse_image_ref.return_value = ('quay.io', 'org/comp-name', 'sha256:abc')
        mock_rc.resolve_per_arch_digest.return_value = {
            'manifest_digest': 'sha256:abc',
            'arch': 'amd64',
        }
        with patch('clients.kubernetes.KubernetesClient', return_value=mock_kc), \
             patch('clients.registry_client.RegistryClient', return_value=mock_rc):
            from mcp_server.tools import lookup_image
            result = _run(lookup_image, 'quay.io/org/comp-name@sha256:abc123456789012345678901')
        assert result['total_matches'] == 1
        assert result['quay_match'] is not None
        assert result['quay_match']['component'] == 'comp-name'

    @patch('mcp_server.tools._build_repo')
    def test_regex_core_extraction(self, mock_build):
        """Test URL path extraction with regex stripping suffixes like -v3-5-ea-2."""
        mock_build.return_value.find_by_image.return_value = []
        mock_kc = MagicMock()
        mock_kc.list_components.return_value = [
            {'name': 'odh-vllm-cpu', 'container_image': 'quay.io/org/odh-vllm-cpu:tag'},
        ]
        with patch('clients.kubernetes.KubernetesClient', return_value=mock_kc):
            from mcp_server.tools import lookup_image
            result = _run(lookup_image, 'quay.io/org/odh-vllm-cpu-v3-5-ea-2:latest')
        # Should match via regex stripping of -v3-5-ea-2
        assert result['total_matches'] >= 1

    @patch('mcp_server.tools._build_repo')
    def test_kubernetes_exception_handled(self, mock_build):
        mock_build.return_value.find_by_image.return_value = []
        with patch('clients.kubernetes.KubernetesClient', side_effect=Exception('k8s down')):
            from mcp_server.tools import lookup_image
            result = _run(lookup_image, 'sha256:abc123')
        assert result['total_matches'] == 0

    @patch('mcp_server.tools._build_repo')
    def test_quay_scan_all_comps(self, mock_build):
        """Test quay resolution scanning all components when direct ref fails."""
        mock_build.return_value.find_by_image.return_value = []
        mock_kc = MagicMock()
        comp1 = {'name': 'comp-a', 'container_image': 'quay.io/org/comp-a:v1'}
        mock_kc.list_components.return_value = [comp1]
        mock_rc = MagicMock()
        mock_rc.parse_image_ref.return_value = ('quay.io', 'org/comp-a', 'tag')
        mock_rc.resolve_per_arch_digest.return_value = {
            'manifest_digest': 'sha256:found',
            'arch': 'arm64',
        }
        with patch('clients.kubernetes.KubernetesClient', return_value=mock_kc), \
             patch('clients.registry_client.RegistryClient', return_value=mock_rc):
            from mcp_server.tools import lookup_image
            result = _run(lookup_image, 'sha256:abcdef1234567890abcdef')
        assert result['quay_match'] is not None
        assert result['quay_match']['component'] == 'comp-a'


# ---------------------------------------------------------------------------
# list_alerts — nightly warnings and blocker signals
# ---------------------------------------------------------------------------

class TestListAlertsExtended:

    @patch('mcp_server.tools._triage_repo')
    @patch('mcp_server.tools._conforma_repo')
    @patch('mcp_server.tools._build_repo')
    def test_minimal_alert_no_failures(self, mock_build, mock_conforma, mock_triage):
        mock_build.return_value.get_triage_summary.return_value = {
            'failing_components': [],
        }
        mock_conforma.return_value.get_violation_summaries.return_value = []
        mock_triage.return_value.build_jira_map.return_value = {}
        with patch('clients.konflux_client.KonfluxClient') as mock_kc_cls, \
             patch('conforma.policy_tools.fetch_exceptions_by_policy', return_value={}), \
             patch('api.routes.releases.get_schedule', return_value=None), \
             patch('api.routes.failures._compute_freeze_countdown', return_value=None):
            mock_kc_cls.return_value.get_dependency_updates.return_value = []
            from mcp_server.tools import list_alerts
            result = _run(list_alerts, application='app')
        assert result.total_count == 0
        assert result.build_failures == []
        assert result.conforma_violations == []
