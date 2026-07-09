"""Tests for uncovered CLI commands in cli/main.py — batch 4.

Covers: resolved, working, stale, nightly, history, dashboard, fix, export,
source, lookup, snapshot-check, schedule, conforma categories/rules/csv,
report build/conforma, release status/diff/verify, jira commands,
patterns commands, config set-app DB path, ai review, _format_since,
_short_diagnosis, _print_stale_footer, _extract_dates_from_pp, and more.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from cli.main import cli


@pytest.fixture
def runner():
    return CliRunner()


# ---------------------------------------------------------------------------
# _format_since helper
# ---------------------------------------------------------------------------
class TestFormatSince:
    def test_full_iso_format(self):
        from cli.main import _format_since
        result = _format_since('2024-06-30T08:04:12.123456')
        assert '30 Jun' in result
        assert '08:04' in result

    def test_date_only(self):
        from cli.main import _format_since
        result = _format_since('2024-06-30')
        assert '30 Jun' in result

    def test_none_input(self):
        from cli.main import _format_since
        assert _format_since(None) == ''

    def test_empty_string(self):
        from cli.main import _format_since
        assert _format_since('') == ''

    def test_short_garbage(self):
        from cli.main import _format_since
        result = _format_since('abc')
        assert result == 'abc'


# ---------------------------------------------------------------------------
# _short_diagnosis helper
# ---------------------------------------------------------------------------
class TestShortDiagnosis:
    def test_known_causes(self):
        from cli.main import _short_diagnosis
        assert _short_diagnosis({'cause': 'missing_pac_repository'}) == 'No webhook'
        assert _short_diagnosis({'cause': 'nudge_misconfiguration'}) == 'Bad nudge'
        assert _short_diagnosis({'cause': 'recent_build_failed'}) == 'Build failed'
        assert _short_diagnosis({'cause': 'build_running'}) == 'Building...'
        assert _short_diagnosis({'cause': 'unknown'}) == '?'

    def test_unlisted_cause(self):
        from cli.main import _short_diagnosis
        assert _short_diagnosis({'cause': 'other_thing'}) == 'other_thing'

    def test_empty_dict(self):
        from cli.main import _short_diagnosis
        assert _short_diagnosis({}) == '?'


# ---------------------------------------------------------------------------
# _policy_filter_to_include_future helper
# ---------------------------------------------------------------------------
class TestPolicyFilterToIncludeFuture:
    def test_future(self):
        from cli.main import _policy_filter_to_include_future
        assert _policy_filter_to_include_future('future') is True

    def test_all(self):
        from cli.main import _policy_filter_to_include_future
        assert _policy_filter_to_include_future('all') == 'all'

    def test_current(self):
        from cli.main import _policy_filter_to_include_future
        assert _policy_filter_to_include_future('current') is None


# ---------------------------------------------------------------------------
# _extract_dates_from_pp helper
# ---------------------------------------------------------------------------
class TestExtractDatesFromPP:
    def test_extracts_dates(self):
        from cli.main import _extract_dates_from_pp
        tasks = [
            {'name': '3.5 GA Planning Freeze', 'date_finish': '2026-06-01'},
            {'name': '3.5 GA Feature Freeze', 'date_finish': '2026-07-01'},
            {'name': '3.5 GA RHOAI Code Freeze', 'date_finish': '2026-07-10'},
            {'name': '3.5 GA RHOAI Initial RC', 'date_finish': '2026-07-15'},
            {'name': '3.5 GA RHOAI Release Window', 'date_finish': '2026-08-01'},
            {'name': '3.5 GA RHOAI Release', 'date_finish': '2026-08-20',
             'flags': ['ga']},
        ]
        dates = _extract_dates_from_pp(tasks, '3.5 GA')
        assert dates.get('planning_freeze') == '2026-06-01'
        assert dates.get('feature_freeze') == '2026-07-01'
        assert dates.get('code_freeze') == '2026-07-10'
        assert dates.get('initial_rc') == '2026-07-15'
        assert dates.get('release_date') == '2026-08-20'

    def test_no_matching_prefix(self):
        from cli.main import _extract_dates_from_pp
        tasks = [{'name': 'Other task', 'date_finish': '2026-01-01'}]
        assert _extract_dates_from_pp(tasks, '3.5 GA') == {}

    def test_skips_no_date_finish(self):
        from cli.main import _extract_dates_from_pp
        tasks = [{'name': '3.5 GA Feature Freeze'}]
        assert _extract_dates_from_pp(tasks, '3.5 GA') == {}


# ---------------------------------------------------------------------------
# resolved command
# ---------------------------------------------------------------------------
class TestResolvedCommand:
    @patch('cli.db.require_db', return_value=False)
    def test_resolved_no_db(self, mock_db, runner):
        result = runner.invoke(cli, ['resolved'])
        assert result.exit_code == 0

    @patch('cli.db.get_repo')
    @patch('cli.db.require_db', return_value=True)
    def test_resolved_empty(self, mock_db, mock_repo, runner):
        mock_repo.return_value.get_resolved_components.return_value = []
        result = runner.invoke(cli, ['resolved'])
        assert 'No resolved components' in result.output

    @patch('cli.db.get_repo')
    @patch('cli.db.require_db', return_value=True)
    def test_resolved_with_data(self, mock_db, mock_repo, runner):
        mock_repo.return_value.get_resolved_components.return_value = [
            {'component': 'comp-a', 'resolved_at': '2026-06-30 12:00:00',
             'count': 2, 'commit_sha': 'abc1234567', 'resolution_url': ''},
        ]
        result = runner.invoke(cli, ['resolved'])
        assert 'comp-a' in result.output

    @patch('cli.db.get_repo')
    @patch('cli.db.require_db', return_value=True)
    def test_resolved_with_days(self, mock_db, mock_repo, runner):
        mock_repo.return_value.get_resolved_components.return_value = []
        result = runner.invoke(cli, ['resolved', '--days', '7'])
        assert 'last 7 days' in result.output

    @patch('cli.db.get_repo')
    @patch('cli.db.require_db', return_value=True)
    def test_resolved_with_since(self, mock_db, mock_repo, runner):
        mock_repo.return_value.get_resolved_components.return_value = []
        result = runner.invoke(cli, ['resolved', '--since', '2026-06-01'])
        assert 'since 2026-06-01' in result.output

    def test_resolved_invalid_date(self, runner):
        result = runner.invoke(cli, ['resolved', '--since', 'bad-date'])
        assert 'must be YYYY-MM-DD' in result.output

    @patch('cli.db.get_repo')
    @patch('cli.db.require_db', return_value=True)
    def test_resolved_json(self, mock_db, mock_repo, runner):
        mock_repo.return_value.get_resolved_components.return_value = []
        result = runner.invoke(cli, ['--json', 'resolved'])
        parsed = json.loads(result.output)
        assert 'items' in parsed


# ---------------------------------------------------------------------------
# working command
# ---------------------------------------------------------------------------
class TestWorkingCommand:
    @patch('cli.db.require_db', return_value=False)
    def test_working_no_db(self, mock_db, runner):
        result = runner.invoke(cli, ['working'])
        assert result.exit_code == 0

    @patch('cli.db.get_repo')
    @patch('cli.db.require_db', return_value=True)
    def test_working_with_data(self, mock_db, mock_repo, runner):
        mock_repo.return_value.get_working_components.return_value = [
            {'component': 'c1', 'commit': 'abc', 'message': 'fix', 'date': '2026-06-30'},
        ]
        result = runner.invoke(cli, ['working'])
        assert 'Currently Working' in result.output
        assert 'c1' in result.output


# ---------------------------------------------------------------------------
# history command
# ---------------------------------------------------------------------------
class TestHistoryCommand:
    def test_history_no_component(self, runner):
        result = runner.invoke(cli, ['history'])
        assert result.exit_code != 0

    @patch('cli.db.get_repo')
    @patch('cli.db.require_db', return_value=True)
    def test_history_with_data(self, mock_db, mock_repo, runner):
        mock_repo.return_value.get_component_history.return_value = {
            'summary': {'total': 5, 'failures': 2, 'successes': 3, 'resolved': 1},
            'builds': [
                {'pipelinerun': 'pr-1', 'status': 'Failed', 'resolved': False,
                 'commit': 'abc', 'date': '2026-06-30'},
                {'pipelinerun': 'pr-2', 'status': 'Succeeded', 'resolved': True,
                 'commit': 'def', 'date': '2026-06-29'},
            ],
            'last_status': 'Succeeded',
        }
        result = runner.invoke(cli, ['history', 'my-comp'])
        assert 'History: my-comp' in result.output
        assert 'Currently working' in result.output

    @patch('cli.db.get_repo')
    @patch('cli.db.require_db', return_value=True)
    def test_history_failing(self, mock_db, mock_repo, runner):
        mock_repo.return_value.get_component_history.return_value = {
            'summary': {'total': 3, 'failures': 3, 'successes': 0, 'resolved': 0},
            'builds': [],
            'last_status': 'Failed',
        }
        result = runner.invoke(cli, ['history', 'bad-comp'])
        assert 'Currently failing' in result.output


# ---------------------------------------------------------------------------
# stale command
# ---------------------------------------------------------------------------
class TestStaleCommand:
    @patch('proactive.health_monitor.HealthMonitor.get_stale_components')
    def test_stale_no_stale(self, mock_stale, runner):
        mock_stale.return_value = {'stale': [], 'checked': 10, 'skipped': 0}
        result = runner.invoke(cli, ['stale'])
        assert 'All components up to date' in result.output

    @patch('proactive.health_monitor.HealthMonitor.get_stale_components')
    def test_stale_error(self, mock_stale, runner):
        mock_stale.return_value = {'error': 'cluster unreachable'}
        result = runner.invoke(cli, ['stale'])
        assert 'cluster unreachable' in result.output

    @patch('proactive.health_monitor.HealthMonitor.get_stale_components')
    def test_stale_with_results(self, mock_stale, runner):
        mock_stale.return_value = {
            'stale': [
                {'component': 'comp-x', 'branch': 'rhoai-3.5',
                 'built_commit': 'aaa', 'head_commit': 'bbb',
                 'repository_url': 'https://github.com/org/repo'},
            ],
            'checked': 5, 'skipped': 0,
        }
        result = runner.invoke(cli, ['stale'])
        assert 'comp-x' in result.output
        assert 'untriggered commits' in result.output

    @patch('proactive.health_monitor.HealthMonitor.get_stale_components')
    def test_stale_with_diagnosis(self, mock_stale, runner):
        mock_stale.return_value = {
            'stale': [
                {'component': 'comp-y', 'branch': 'rhoai-3.5',
                 'built_commit': 'aaa', 'head_commit': 'bbb',
                 'repository_url': 'https://github.com/org/repo',
                 'diagnosis': {'cause': 'missing_pac_repository'}},
            ],
            'checked': 3, 'skipped': 0,
        }
        result = runner.invoke(cli, ['stale'])
        assert 'comp-y' in result.output

    @patch('proactive.health_monitor.HealthMonitor.get_stale_components')
    def test_stale_json(self, mock_stale, runner):
        mock_stale.return_value = {'stale': [], 'checked': 0}
        result = runner.invoke(cli, ['--json', 'stale'])
        parsed = json.loads(result.output)
        assert 'stale' in parsed

    @patch('proactive.health_monitor.HealthMonitor.get_stale_components')
    def test_stale_with_warning(self, mock_stale, runner):
        mock_stale.return_value = {
            'stale': [],
            'checked': 5, 'skipped': 0,
            'warning': 'Rate limit reached',
        }
        result = runner.invoke(cli, ['stale'])
        assert 'Rate limit' in result.output


# ---------------------------------------------------------------------------
# fix command
# ---------------------------------------------------------------------------
class TestFixCommand:
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_fix_with_component(self, mock_cluster, runner):
        result = runner.invoke(cli, ['fix', 'my-comp'])
        assert 'Fix: my-comp' in result.output
        assert 'Available actions' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_fix_no_args(self, mock_cluster, runner):
        # no args -> shows usage error and exits with code 1
        result = runner.invoke(cli, ['fix'])
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# export command
# ---------------------------------------------------------------------------
class TestExportCommand:
    @patch('cli.data.get_export')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_export_slack(self, mock_cluster, mock_export, runner):
        mock_export.return_value = {'text': 'Slack message here'}
        result = runner.invoke(cli, ['export', 'comp-a', 'slack'])
        assert 'Slack message here' in result.output

    @patch('cli.data.get_export')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_export_no_data(self, mock_cluster, mock_export, runner):
        mock_export.return_value = None
        result = runner.invoke(cli, ['export', 'comp-a'])
        assert 'No export data' in result.output

    @patch('cli.data.get_export')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_export_dict_no_text(self, mock_cluster, mock_export, runner):
        mock_export.return_value = {'key': 'value'}
        result = runner.invoke(cli, ['export', 'comp-a', 'jira'])
        assert '"key"' in result.output


# ---------------------------------------------------------------------------
# source command
# ---------------------------------------------------------------------------
class TestSourceCommand:
    @patch('openshift_auth._ensure_k8s_config')
    @patch('clients.kubernetes.KubernetesClient.get_component_metadata')
    def test_source_not_found(self, mock_meta, mock_k8s, runner):
        mock_meta.return_value = None
        result = runner.invoke(cli, ['source', 'missing-comp'])
        assert 'not found' in result.output

    @patch('openshift_auth._ensure_k8s_config')
    @patch('clients.kubernetes.KubernetesClient.get_component_metadata')
    def test_source_found(self, mock_meta, mock_k8s, runner):
        mock_meta.return_value = {
            'repository_url': 'https://github.com/org/repo',
            'branch': 'main',
            'container_image': 'quay.io/org/img:latest',
            'last_built_commit': 'abc123def',
            'last_promoted_image': 'registry.io/img@sha256:abc',
            'nudges': ['nudge-a', 'nudge-b'],
        }
        result = runner.invoke(cli, ['source', 'my-comp'])
        assert 'github.com/org/repo' in result.output
        assert 'nudge-a' in result.output

    @patch('openshift_auth._ensure_k8s_config')
    @patch('clients.kubernetes.KubernetesClient.get_component_metadata')
    def test_source_json_no_prs(self, mock_meta, mock_k8s, runner):
        mock_meta.return_value = {
            'repository_url': 'https://github.com/org/repo',
            'branch': 'main',
            'container_image': 'quay.io/org/img:latest',
        }
        result = runner.invoke(cli, ['--json', 'source', 'my-comp'])
        parsed = json.loads(result.output)
        assert parsed['component'] == 'my-comp'


# ---------------------------------------------------------------------------
# lookup command
# ---------------------------------------------------------------------------
class TestLookupCommand:
    @patch('cli.db.get_repo')
    @patch('cli.db.require_db', return_value=True)
    def test_lookup_found_in_db(self, mock_db, mock_repo, runner):
        mock_repo.return_value.find_by_image.return_value = [
            {'component_name': 'comp-a', 'application': 'rhoai-v3-5',
             'status': 'Failed', 'is_resolved': False,
             'pipelinerun_name': 'pr-123', 'first_detected_at': '2026-06-30',
             'repository_url': 'https://github.com/org/repo', 'branch': 'main'},
        ]
        with patch('clients.kubernetes.KubernetesClient') as mock_kc:
            mock_kc.return_value.list_components.return_value = []
            result = runner.invoke(cli, ['lookup', 'quay.io/org/img@sha256:abc123'])
        assert 'comp-a' in result.output
        assert 'Found in build records' in result.output

    @patch('cli.db.get_repo')
    @patch('cli.db.require_db', return_value=True)
    def test_lookup_not_found(self, mock_db, mock_repo, runner):
        mock_repo.return_value.find_by_image.return_value = []
        with patch('clients.kubernetes.KubernetesClient') as mock_kc:
            mock_kc.return_value.list_components.return_value = []
            with patch('cli.main._resolve_via_quay', return_value=None):
                result = runner.invoke(cli, ['lookup', 'nonexistent'])
        assert 'No matches found' in result.output

    @patch('cli.db.get_repo')
    @patch('cli.db.require_db', return_value=True)
    def test_lookup_json(self, mock_db, mock_repo, runner):
        mock_repo.return_value.find_by_image.return_value = []
        with patch('clients.kubernetes.KubernetesClient') as mock_kc:
            mock_kc.return_value.list_components.return_value = []
            with patch('cli.main._resolve_via_quay', return_value=None):
                result = runner.invoke(cli, ['--json', 'lookup', 'some-image'])
        parsed = json.loads(result.output)
        assert 'query' in parsed


# ---------------------------------------------------------------------------
# snapshot-check command
# ---------------------------------------------------------------------------
class TestSnapshotCheckCommand:
    @patch('proactive.health_monitor.HealthMonitor.check_snapshot_freshness')
    def test_snapshot_all_fresh(self, mock_check, runner):
        mock_check.return_value = {
            'application': 'rhoai-v3-5',
            'snapshot': 'snap-123',
            'snapshot_created': '2026-06-30',
            'total_components': 10,
            'stale': [],
            'no_builds': [],
            'fresh_count': 10,
        }
        result = runner.invoke(cli, ['snapshot-check'])
        assert 'All 10 components are fresh' in result.output

    @patch('proactive.health_monitor.HealthMonitor.check_snapshot_freshness')
    def test_snapshot_stale(self, mock_check, runner):
        mock_check.return_value = {
            'application': 'rhoai-v3-5',
            'snapshot': 'snap-123',
            'snapshot_created': '2026-06-30',
            'total_components': 10,
            'stale': [
                {'component': 'comp-x', 'reason': 'latest_build_failed',
                 'latest_build_pr': 'pr-1', 'latest_build_date': '2026-06-29'},
            ],
            'no_builds': [{'component': 'comp-y'}],
            'fresh_count': 8,
        }
        result = runner.invoke(cli, ['snapshot-check'])
        assert 'comp-x' in result.output
        assert 'FAILED' in result.output
        assert 'comp-y' in result.output

    @patch('proactive.health_monitor.HealthMonitor.check_snapshot_freshness')
    def test_snapshot_error(self, mock_check, runner):
        mock_check.return_value = {'error': 'No snapshots'}
        result = runner.invoke(cli, ['snapshot-check'])
        assert result.exit_code != 0

    @patch('proactive.health_monitor.HealthMonitor.check_snapshot_freshness')
    def test_snapshot_json(self, mock_check, runner):
        mock_check.return_value = {'application': 'app', 'stale': []}
        result = runner.invoke(cli, ['--json', 'snapshot-check'])
        parsed = json.loads(result.output)
        assert 'application' in parsed

    @patch('proactive.health_monitor.HealthMonitor.check_snapshot_freshness')
    def test_snapshot_newer_build(self, mock_check, runner):
        mock_check.return_value = {
            'application': 'rhoai-v3-5',
            'snapshot': 'snap-1',
            'snapshot_created': '2026-06-30',
            'total_components': 5,
            'stale': [
                {'component': 'comp-z', 'reason': 'newer_build_available',
                 'latest_build_date': '2026-07-01'},
            ],
            'no_builds': [],
            'fresh_count': 4,
        }
        result = runner.invoke(cli, ['snapshot-check'])
        assert 'STALE' in result.output


# ---------------------------------------------------------------------------
# dashboard command
# ---------------------------------------------------------------------------
class TestDashboardCommand:
    @patch('cli.db.require_db', return_value=False)
    def test_dashboard_no_db(self, mock_db, runner):
        result = runner.invoke(cli, ['dashboard'])
        assert result.exit_code == 0

    @patch('cli.db.get_repo')
    @patch('cli.db.require_db', return_value=True)
    def test_dashboard_success(self, mock_db, mock_repo, runner):
        build_mock = MagicMock()
        ai_mock = MagicMock()
        pattern_mock = MagicMock()
        fix_mock = MagicMock()

        build_mock.get_analysis_queue.return_value = {'analyzed': 10, 'pending': 2}
        ai_mock.get_conforma_queue.return_value = {'analyzed': 5, 'pending': 1}
        build_mock.get_enrichment_coverage.return_value = {
            'enriched': 8, 'total': 10, 'pct': 80, 'failed': 1}
        pattern_mock.get_library_summary.return_value = {
            'total': 15, 'with_confidence': 12,
            'avg_confidence': 85, 'total_occurrences': 50}
        fix_mock.get_outcome_summary.return_value = {
            'total': 20, 'successful': 15, 'failed': 3, 'pending': 2, 'rate': 75}
        ai_mock.get_cost_summary.return_value = {
            'analyses': 30, 'tokens': 50000, 'cost_usd': 1.25}

        def repo_factory(cls):
            name = cls.__name__
            if 'BuildFailure' in name:
                return build_mock
            elif 'AIAnalysis' in name:
                return ai_mock
            elif 'ErrorPattern' in name:
                return pattern_mock
            elif 'Resolution' in name:
                return fix_mock
            return MagicMock()

        mock_repo.side_effect = repo_factory
        result = runner.invoke(cli, ['dashboard'])
        assert 'Dashboard' in result.output
        assert 'Analysis Queue' in result.output


# ---------------------------------------------------------------------------
# conforma categories command
# ---------------------------------------------------------------------------
class TestConformaCategoriesCommand:
    @patch('cli.data.get_conforma_violations')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_categories_with_data(self, mock_cluster, mock_violations, runner):
        mock_violations.return_value = [
            {'component': 'comp-a', 'scenario': 'fbc-scenario',
             'unique_violations': 3, 'violation_summary': ''},
            {'component': 'comp-b', 'scenario': 'comp-scenario',
             'unique_violations': 1, 'violation_summary': ''},
        ]
        with patch('conforma.policy_tools.categorize_policy') as mock_cat:
            mock_cat.side_effect = lambda s: 'FBC' if 'fbc' in s else 'Components'
            with patch('conforma.policy_tools.count_unique_violations', return_value=(0, 0)):
                result = runner.invoke(cli, ['conforma', 'categories'])
        assert 'Categories' in result.output


# ---------------------------------------------------------------------------
# conforma rules command
# ---------------------------------------------------------------------------
class TestConformaRulesCommand:
    @patch('cli.data.get_conforma_rules')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_rules_with_data(self, mock_cluster, mock_rules, runner):
        mock_rules.return_value = [
            {'rule': 'test_rule_1', 'count': 3,
             'solution': 'Fix by doing X',
             'components': ['comp-a-v3-5', 'comp-b-v3-5', 'comp-c-v3-5']},
        ]
        result = runner.invoke(cli, ['conforma', 'rules'])
        assert 'test_rule_1' in result.output
        assert '3 components' in result.output

    @patch('cli.data.get_conforma_rules')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_rules_no_violations(self, mock_cluster, mock_rules, runner):
        mock_rules.return_value = []
        result = runner.invoke(cli, ['conforma', 'rules'])
        assert 'No violations found' in result.output


# ---------------------------------------------------------------------------
# conforma csv command
# ---------------------------------------------------------------------------
class TestConformaCsvCommand:
    @patch('cli.data.get_conforma_violations')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_csv_output(self, mock_cluster, mock_violations, runner):
        mock_violations.return_value = [
            {'component': 'comp-a', 'violations_count': 3,
             'unique_violations': 2, 'warnings_count': 1,
             'scenario': 'test-scen', 'first_seen': '2026-06-30'},
        ]
        result = runner.invoke(cli, ['conforma', 'csv'])
        assert 'component,violations' in result.output
        assert 'comp-a' in result.output


# ---------------------------------------------------------------------------
# report build / conforma commands
# ---------------------------------------------------------------------------
class TestReportCommands:
    @patch('cli.data.get_alerts')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_report_build_no_failures(self, mock_cluster, mock_alerts, runner):
        mock_alerts.return_value = {'build_failures': [], 'conforma_violations': []}
        result = runner.invoke(cli, ['report', 'build'])
        assert 'No build failures' in result.output

    @patch('cli.data.get_alerts')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_report_build_with_failures(self, mock_cluster, mock_alerts, runner):
        mock_alerts.return_value = {
            'build_failures': [
                {'component': 'comp-x', 'error_type': 'build_error'},
            ],
            'conforma_violations': [],
        }
        result = runner.invoke(cli, ['report', 'build'])
        assert 'comp-x' in result.output

    @patch('cli.data.get_conforma_report')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_report_conforma(self, mock_cluster, mock_report, runner):
        mock_report.return_value = [
            {'component_name': 'comp-a', 'violations_count': 2,
             'warnings_count': 1, 'scenario': 'test'},
        ]
        result = runner.invoke(cli, ['report', 'conforma'])
        assert 'comp-a' in result.output


# ---------------------------------------------------------------------------
# release status command
# ---------------------------------------------------------------------------
class TestReleaseStatusCommand:
    @patch('cli.api_client.get_client')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_release_status_ready(self, mock_cluster, mock_client, runner):
        mock_client.return_value.get.return_value = {
            'build_failures': 0,
            'conforma_violations': 0,
            'verdict': 'READY',
            'blockers': [],
            'risks': [],
            'checks': [],
        }
        result = runner.invoke(cli, ['release', 'status'])
        assert 'READY' in result.output

    @patch('cli.api_client.get_client')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_release_status_not_ready(self, mock_cluster, mock_client, runner):
        mock_client.return_value.get.return_value = {
            'build_failures': 3,
            'conforma_violations': 2,
            'verdict': 'NOT_READY',
            'blockers': ['3 build failures', '2 conforma violations'],
            'risks': [],
            'checks': [
                {'name': 'FBC Health', 'status': 'PASS', 'detail': 'OK'},
                {'name': 'Stale Components', 'status': 'WARN',
                 'detail': '1 stale', 'fix': 'Rebuild'},
            ],
        }
        result = runner.invoke(cli, ['release', 'status'])
        assert 'NOT READY' in result.output
        assert '3 build failures' in result.output
        assert 'FBC Health' in result.output

    @patch('cli.api_client.get_client')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_release_status_no_data(self, mock_cluster, mock_client, runner):
        mock_client.return_value.get.return_value = None
        result = runner.invoke(cli, ['release', 'status'])
        assert 'No readiness data' in result.output

    @patch('cli.api_client.get_client')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_release_status_at_risk(self, mock_cluster, mock_client, runner):
        mock_client.return_value.get.return_value = {
            'build_failures': 0,
            'conforma_violations': 0,
            'verdict': 'AT_RISK',
            'blockers': [],
            'risks': ['Stale components detected'],
            'checks': [],
        }
        result = runner.invoke(cli, ['release', 'status'])
        assert 'AT RISK' in result.output
        assert 'Stale components detected' in result.output

    @patch('cli.api_client.get_client')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_release_status_with_schedule(self, mock_cluster, mock_client, runner):
        mock_client.return_value.get.return_value = {
            'build_failures': 0,
            'conforma_violations': 0,
            'verdict': 'READY',
            'blockers': [],
            'risks': [],
            'checks': [],
            'schedule': {
                'release_date': '2026-08-20',
                'release_date_days': 50,
                'code_freeze': '2026-07-24',
                'code_freeze_days': 23,
            },
        }
        result = runner.invoke(cli, ['release', 'status'])
        assert 'Release' in result.output
        assert 'Code Freeze' in result.output

    @patch('cli.api_client.get_client')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_release_status_with_freeze(self, mock_cluster, mock_client, runner):
        mock_client.return_value.get.return_value = {
            'build_failures': 0,
            'conforma_violations': 0,
            'verdict': 'NOT_READY',
            'blockers': ['Active pipeline freeze'],
            'risks': [],
            'checks': [],
            'freeze': {'end_date': '2026-07-10', 'reason': 'EA2 release'},
        }
        result = runner.invoke(cli, ['release', 'status'])
        assert 'Frozen until' in result.output


# ---------------------------------------------------------------------------
# release diff / verify commands
# ---------------------------------------------------------------------------
class TestReleaseDiffVerify:
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_release_diff(self, mock_cluster, runner):
        result = runner.invoke(cli, ['release', 'diff'])
        assert 'requires direct cluster access' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_release_verify(self, mock_cluster, runner):
        result = runner.invoke(cli, ['release', 'verify'])
        assert 'requires direct cluster' in result.output


# ---------------------------------------------------------------------------
# jira commands
# ---------------------------------------------------------------------------
class TestJiraCommands:
    @patch('cli.data.get_export')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_jira_create_with_data(self, mock_cluster, mock_export, runner):
        mock_export.return_value = {'text': 'Jira ticket content'}
        result = runner.invoke(cli, ['jira', 'create', 'comp-a'])
        assert 'Jira ticket content' in result.output

    @patch('cli.data.get_export')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_jira_create_no_data(self, mock_cluster, mock_export, runner):
        mock_export.return_value = None
        result = runner.invoke(cli, ['jira', 'create', 'comp-a'])
        assert 'No data for' in result.output

    @patch('cli.data.get_triage_items')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_jira_inbox_with_items(self, mock_cluster, mock_items, runner):
        mock_items.return_value = [
            {'jira_key': 'RHOAIENG-123', 'components': ['comp-a']},
        ]
        result = runner.invoke(cli, ['jira', 'inbox'])
        assert 'RHOAIENG-123' in result.output

    @patch('cli.data.get_triage_items')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_jira_inbox_no_jira(self, mock_cluster, mock_items, runner):
        mock_items.return_value = [{'components': ['comp-a']}]
        result = runner.invoke(cli, ['jira', 'inbox'])
        assert 'No Jira tickets' in result.output

    @patch('cli.data.get_alerts')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_jira_status_with_links(self, mock_cluster, mock_alerts, runner):
        mock_alerts.return_value = {
            'build_failures': [{'component': 'comp-a', 'jira_key': 'RHOAIENG-1'}],
            'conforma_violations': [],
        }
        result = runner.invoke(cli, ['jira', 'status'])
        assert 'RHOAIENG-1' in result.output

    @patch('cli.data.get_alerts')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_jira_status_no_links(self, mock_cluster, mock_alerts, runner):
        mock_alerts.return_value = {
            'build_failures': [{'component': 'comp-a'}],
            'conforma_violations': [],
        }
        result = runner.invoke(cli, ['jira', 'status'])
        assert 'No Jira tickets' in result.output


# ---------------------------------------------------------------------------
# patterns commands
# ---------------------------------------------------------------------------
class TestPatternsCommands:
    @patch('cli.db.get_repo')
    @patch('cli.db.require_db', return_value=True)
    def test_patterns_learn_created(self, mock_db, mock_repo, runner):
        mock_repo.return_value.discover_new_patterns.return_value = [
            {'pattern_name': 'go-mismatch', 'failure_category': 'dependency_issue'},
        ]
        result = runner.invoke(cli, ['patterns', 'learn'])
        assert 'Created 1 new pattern' in result.output

    @patch('cli.db.get_repo')
    @patch('cli.db.require_db', return_value=True)
    def test_patterns_learn_none(self, mock_db, mock_repo, runner):
        mock_repo.return_value.discover_new_patterns.return_value = []
        result = runner.invoke(cli, ['patterns', 'learn'])
        assert 'No new patterns' in result.output

    @patch('cli.db.get_repo')
    @patch('cli.db.require_db', return_value=True)
    def test_patterns_stale_found(self, mock_db, mock_repo, runner):
        mock_repo.return_value.get_stale_patterns.return_value = [
            {'pattern_name': 'old-pat', 'avg_confidence': 0.45,
             'last_used_at': '2026-01-01'},
        ]
        result = runner.invoke(cli, ['patterns', 'stale'])
        assert 'old-pat' in result.output

    @patch('cli.db.get_repo')
    @patch('cli.db.require_db', return_value=True)
    def test_patterns_stale_empty(self, mock_db, mock_repo, runner):
        mock_repo.return_value.get_stale_patterns.return_value = []
        result = runner.invoke(cli, ['patterns', 'stale'])
        assert 'No stale patterns' in result.output

    @patch('cli.api_client.get_client')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_patterns_shared_found(self, mock_cluster, mock_client, runner):
        mock_client.return_value.get.return_value = [
            {'pattern_name': 'shared-pat', 'failure_category': 'dep',
             'occurrence_count': 5},
        ]
        result = runner.invoke(cli, ['patterns', 'shared'])
        assert 'shared-pat' in result.output
        assert '5 occurrences' in result.output

    @patch('cli.api_client.get_client')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_patterns_shared_none(self, mock_cluster, mock_client, runner):
        mock_client.return_value.get.return_value = []
        result = runner.invoke(cli, ['patterns', 'shared'])
        assert 'No shared patterns' in result.output

    @patch('cli.db.get_repo')
    @patch('cli.db.require_db', return_value=True)
    def test_patterns_list(self, mock_db, mock_repo, runner):
        mock_repo.return_value.get_all.return_value = [
            {'failure_type': 'build', 'failure_category': 'dep',
             'occurrence_count': 3, 'avg_confidence': 0.85,
             'doc_context': None, 'last_seen_at': '2026-06-30',
             'pattern_name': 'test-pat'},
        ]
        mock_repo.return_value.get_library_summary.return_value = {'total': 1}
        result = runner.invoke(cli, ['patterns', 'list'])
        assert 'test-pat' in result.output

    @patch('cli.db.get_repo')
    @patch('cli.db.require_db', return_value=True)
    def test_patterns_show_found(self, mock_db, mock_repo, runner):
        mock_repo.return_value.get_by_name_or_category.return_value = {
            'pattern_name': 'my-pattern', 'failure_type': 'build',
            'failure_category': 'build_error', 'created_by': 'user',
            'occurrence_count': 10, 'avg_confidence': 0.9,
            'first_seen_at': '2026-01-01', 'last_seen_at': '2026-06-30',
            'description': 'A pattern description', 'typical_fix': 'Do something',
            'doc_url': 'https://docs.example.com', 'doc_context': 'Some doc text',
        }
        result = runner.invoke(cli, ['patterns', 'show', 'my-pattern'])
        assert 'my-pattern' in result.output
        assert 'Do something' in result.output

    @patch('cli.db.get_repo')
    @patch('cli.db.require_db', return_value=True)
    def test_patterns_show_not_found(self, mock_db, mock_repo, runner):
        mock_repo.return_value.get_by_name_or_category.return_value = None
        result = runner.invoke(cli, ['patterns', 'show', 'nonexistent'])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# nightly command
# ---------------------------------------------------------------------------
class TestNightlyCommand:
    @patch('repositories.connection.DatabaseConnection')
    @patch('config.CollectorConfig.from_env')
    @patch('proactive.health_monitor.HealthMonitor.get_nightly_status')
    def test_nightly_with_blockers(self, mock_status, mock_config, mock_db, runner):
        mock_config.return_value.db = MagicMock()
        mock_status.return_value = {
            'fbc_health': {
                'health_score': 80,
                'current_status': 'Succeeded',
                'last_successful_build': '2026-06-30 10:00:00',
            },
            'fbc_component': 'fbc-fragment-ci',
            'fbc_image': 'quay.io/org/fbc:latest',
            'fbc_conforma': None,
            'blockers': [
                {'component': 'comp-x', 'error_type': 'build_error',
                 'root_cause_summary': 'Go version mismatch',
                 'last_successful_build': '2026-06-28'},
            ],
        }
        result = runner.invoke(cli, ['nightly'])
        assert 'Nightly Status' in result.output
        assert 'comp-x' in result.output
        assert '1 blocker' in result.output

    @patch('repositories.connection.DatabaseConnection')
    @patch('config.CollectorConfig.from_env')
    @patch('proactive.health_monitor.HealthMonitor.get_nightly_status')
    def test_nightly_no_blockers(self, mock_status, mock_config, mock_db, runner):
        mock_config.return_value.db = MagicMock()
        mock_status.return_value = {
            'fbc_health': {'health_score': 100, 'current_status': 'Succeeded'},
            'fbc_component': 'fbc-frag',
            'blockers': [],
        }
        result = runner.invoke(cli, ['nightly'])
        assert 'No build blockers' in result.output

    @patch('repositories.connection.DatabaseConnection')
    @patch('config.CollectorConfig.from_env')
    @patch('proactive.health_monitor.HealthMonitor.get_nightly_status')
    def test_nightly_json(self, mock_status, mock_config, mock_db, runner):
        mock_config.return_value.db = MagicMock()
        mock_status.return_value = {'fbc_component': 'fbc', 'blockers': []}
        result = runner.invoke(cli, ['--json', 'nightly'])
        parsed = json.loads(result.output)
        assert 'blockers' in parsed

    @patch('repositories.connection.DatabaseConnection')
    @patch('config.CollectorConfig.from_env')
    @patch('proactive.health_monitor.HealthMonitor.get_nightly_status')
    def test_nightly_fbc_failed(self, mock_status, mock_config, mock_db, runner):
        mock_config.return_value.db = MagicMock()
        mock_status.return_value = {
            'fbc_health': {
                'health_score': 30,
                'current_status': 'Failed',
            },
            'fbc_component': 'fbc-frag',
            'fbc_conforma': {'violations_count': 2, 'warnings_count': 1},
            'blockers': [],
        }
        result = runner.invoke(cli, ['nightly'])
        assert 'Failed' in result.output
        assert '2 violations' in result.output

    @patch('repositories.connection.DatabaseConnection')
    @patch('config.CollectorConfig.from_env')
    @patch('proactive.health_monitor.HealthMonitor.get_nightly_status')
    def test_nightly_no_fbc_health(self, mock_status, mock_config, mock_db, runner):
        mock_config.return_value.db = MagicMock()
        mock_status.return_value = {
            'fbc_health': None,
            'fbc_component': 'fbc-frag',
            'blockers': [],
        }
        result = runner.invoke(cli, ['nightly'])
        assert 'No health data' in result.output

    @patch('repositories.connection.DatabaseConnection')
    @patch('config.CollectorConfig.from_env')
    @patch('proactive.health_monitor.HealthMonitor.get_nightly_status')
    def test_nightly_with_pcc_stale(self, mock_status, mock_config, mock_db, runner):
        mock_config.return_value.db = MagicMock()
        mock_status.return_value = {
            'fbc_health': {'health_score': 90, 'current_status': 'Succeeded'},
            'fbc_component': 'fbc-frag',
            'blockers': [],
            'pcc_freshness': {
                'status': 'stale',
                'missing_versions': ['3.5.0', '3.5.1'],
                'last_regen': {
                    'date': '2026-06-25',
                    'conclusion': 'success',
                    'url': 'https://github.com/actions/1',
                },
            },
        }
        result = runner.invoke(cli, ['nightly'])
        assert 'STALE' in result.output
        assert '3.5.0' in result.output

    @patch('repositories.connection.DatabaseConnection')
    @patch('config.CollectorConfig.from_env')
    @patch('proactive.health_monitor.HealthMonitor.get_nightly_status')
    def test_nightly_with_gha(self, mock_status, mock_config, mock_db, runner):
        mock_config.return_value.db = MagicMock()
        mock_status.return_value = {
            'fbc_health': {'health_score': 100, 'current_status': 'Succeeded'},
            'fbc_component': 'fbc-frag',
            'blockers': [],
            'gha_validation': {
                'conclusion': 'success',
                'created_at': '2026-06-30',
                'url': 'https://github.com/actions/2',
            },
        }
        result = runner.invoke(cli, ['nightly'])
        assert 'GHA Validation' in result.output


# ---------------------------------------------------------------------------
# schedule command
# ---------------------------------------------------------------------------
class TestScheduleCommand:
    @patch('cli.data.get_schedule_all')
    def test_schedule_no_data(self, mock_sched, runner):
        mock_sched.return_value = []
        result = runner.invoke(cli, ['schedule'])
        assert 'No release schedule data' in result.output

    @patch('cli.data.get_schedule_all')
    def test_schedule_with_data(self, mock_sched, runner):
        mock_sched.return_value = [
            {
                'application': 'rhoai-v3-5',
                'release_date': '2026-08-20',
                'release_date_days': 50,
                'code_freeze': '2026-07-24',
                'code_freeze_days': 23,
                'feature_freeze': '2026-07-17',
                'feature_freeze_days': 16,
                'initial_rc': '2026-07-30',
                'initial_rc_days': 29,
            },
        ]
        result = runner.invoke(cli, ['schedule'])
        assert 'Release Schedule' in result.output
        assert 'rhoai-v3-5' in result.output
        assert 'Code Freeze' in result.output

    @patch('cli.data.get_schedule_all')
    def test_schedule_json(self, mock_sched, runner):
        mock_sched.return_value = [{'application': 'rhoai-v3-5'}]
        result = runner.invoke(cli, ['--json', 'schedule'])
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)

    @patch('cli.data.get_schedule_all')
    def test_schedule_milestone_done(self, mock_sched, runner):
        mock_sched.return_value = [
            {
                'application': 'rhoai-v3-5-ea-1',
                'release_date': '2026-06-18',
                'release_date_days': -13,
                'code_freeze': '2026-05-15',
                'code_freeze_days': -47,
            },
        ]
        result = runner.invoke(cli, ['schedule'])
        assert 'rhoai-v3-5-ea-1' in result.output

    @patch('cli.data.get_schedule_all')
    def test_schedule_urgent(self, mock_sched, runner):
        mock_sched.return_value = [
            {
                'application': 'rhoai-v3-5',
                'release_date': '2026-07-05',
                'release_date_days': 4,
                'code_freeze': '2026-07-02',
                'code_freeze_days': 1,
            },
        ]
        result = runner.invoke(cli, ['schedule'])
        assert 'CRITICAL' in result.output


# ---------------------------------------------------------------------------
# _print_check_line helper
# ---------------------------------------------------------------------------
class TestPrintCheckLine:
    def test_pass(self, capsys):
        from cli.main import _print_check_line
        _print_check_line('Test Check', 'PASS', 'All good', None)
        output = capsys.readouterr().out
        assert 'PASS' in output
        assert 'Test Check' in output

    def test_fail_with_fix(self, capsys):
        from cli.main import _print_check_line
        _print_check_line('Bad Check', 'FAIL', 'Something wrong', 'Fix it')
        output = capsys.readouterr().out
        assert 'FAIL' in output
        assert 'Fix: Fix it' in output

    def test_skip(self, capsys):
        from cli.main import _print_check_line
        _print_check_line('Skip Check', 'SKIP', 'Not available')
        output = capsys.readouterr().out
        assert 'SKIP' in output


# ---------------------------------------------------------------------------
# health commands
# ---------------------------------------------------------------------------
class TestHealthCommands:
    @patch('cli.db.require_db', return_value=False)
    def test_health_no_db(self, mock_db, runner):
        result = runner.invoke(cli, ['health'])
        assert result.exit_code == 0

    @patch('cli.db._get_db_connection')
    @patch('cli.db.require_db', return_value=True)
    def test_health_with_data(self, mock_db, mock_conn, runner):
        with patch('proactive.health_monitor.HealthMonitor.get_component_health_summary') as mock_health:
            mock_health.return_value = [
                {'component_name': 'comp-a', 'health_score': 90,
                 'health_status': 'healthy', 'consecutive_failures': 0,
                 'success_rate_last_7d': 100.0},
                {'component_name': 'comp-b', 'health_score': 30,
                 'health_status': 'critical', 'consecutive_failures': 5,
                 'success_rate_last_7d': 20.0},
            ]
            result = runner.invoke(cli, ['health'])
        assert 'Component Health' in result.output
        assert 'comp-b' in result.output

    @patch('cli.db._get_db_connection')
    @patch('cli.db.require_db', return_value=True)
    def test_health_no_data(self, mock_db, mock_conn, runner):
        with patch('proactive.health_monitor.HealthMonitor.get_component_health_summary') as mock_health:
            mock_health.return_value = []
            result = runner.invoke(cli, ['health'])
        assert 'No component health data' in result.output

    @patch('cli.db._get_db_connection')
    @patch('cli.db.require_db', return_value=True)
    def test_health_warnings(self, mock_db, mock_conn, runner):
        mock_warning = MagicMock()
        mock_warning.signal_type = 'degradation'
        mock_warning.severity = 'critical'
        mock_warning.message = 'Component X is degrading'
        with patch('proactive.health_monitor.HealthMonitor.run_checks') as mock_checks:
            mock_checks.return_value = [mock_warning]
            result = runner.invoke(cli, ['health', 'warnings'])
        assert 'Proactive Warnings' in result.output
        assert 'Component X is degrading' in result.output

    @patch('cli.db._get_db_connection')
    @patch('cli.db.require_db', return_value=True)
    def test_health_warnings_empty(self, mock_db, mock_conn, runner):
        with patch('proactive.health_monitor.HealthMonitor.run_checks') as mock_checks:
            mock_checks.return_value = []
            result = runner.invoke(cli, ['health', 'warnings'])
        assert 'No warnings' in result.output


# ---------------------------------------------------------------------------
# config set-app with DB validation
# ---------------------------------------------------------------------------
class TestConfigSetAppDB:
    @patch('cli.db.check_db', return_value=True)
    @patch('cli.db._get_db_connection')
    def test_set_app_not_in_db(self, mock_conn, mock_check, runner, tmp_path):
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = [False]
        mock_conn.return_value.connection.return_value.__enter__ = MagicMock(
            return_value=MagicMock(cursor=MagicMock(return_value=mock_cursor)))
        mock_conn.return_value.connection.return_value.__exit__ = MagicMock(return_value=False)
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.invoke(cli, ['config', 'set-app', 'nonexistent-app'])
        assert 'not found in database' in result.output

    @patch('cli.db.check_db', return_value=True)
    @patch('cli.db._get_db_connection')
    def test_set_app_found_in_db(self, mock_conn, mock_check, runner, tmp_path):
        mock_cursor = MagicMock()
        mock_cursor.fetchone.side_effect = [[True], [1]]
        ctx_mock = MagicMock()
        ctx_mock.cursor.return_value = mock_cursor
        mock_conn.return_value.connection.return_value.__enter__ = MagicMock(return_value=ctx_mock)
        mock_conn.return_value.connection.return_value.__exit__ = MagicMock(return_value=False)
        env_file = tmp_path / '.env'
        env_file.write_text('APPLICATION_NAME=old-app\n')
        with patch('cli.config.PROJECT_DIR', str(tmp_path)):
            result = runner.invoke(cli, ['config', 'set-app', 'rhoai-v3-5'])
        assert 'Application set to' in result.output


# ---------------------------------------------------------------------------
# stats commands
# ---------------------------------------------------------------------------
class TestStatsCommands:
    @patch('cli.db.get_repo')
    @patch('cli.db.require_db', return_value=True)
    def test_stats_daily(self, mock_db, mock_repo, runner):
        mock_repo.return_value.get_daily_stats.return_value = [
            {'date': '2026-06-30', 'count': 5},
            {'date': '2026-06-29', 'count': 3},
        ]
        result = runner.invoke(cli, ['stats', 'daily'])
        assert 'Failures by Day' in result.output

    @patch('cli.db.get_repo')
    @patch('cli.db.require_db', return_value=True)
    def test_stats_resolved(self, mock_db, mock_repo, runner):
        mock_repo.return_value.get_resolved_stats.return_value = {
            'total': 20, 'resolved': 15, 'unresolved': 5, 'successes': 12,
        }
        result = runner.invoke(cli, ['stats', 'resolved'])
        assert 'Resolved Failures' in result.output

    @patch('cli.data.get_overview_stats')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_stats_overview_cluster(self, mock_cluster, mock_stats, runner):
        mock_stats.return_value = {
            'total_failures': 10,
            'components': 5,
        }
        result = runner.invoke(cli, ['stats'])
        assert 'Statistics' in result.output


# ---------------------------------------------------------------------------
# _compute_next_action helper
# ---------------------------------------------------------------------------
class TestComputeNextAction:
    def test_complete_with_items(self):
        from cli.main import _compute_next_action
        data = {
            'phase': 'complete',
            'report': {'action_items': [{'action': 'Review PR', 'priority': 'HIGH'}]},
        }
        result = _compute_next_action(data)
        assert 'Review PR' in result

    def test_complete_no_items(self):
        from cli.main import _compute_next_action
        data = {'phase': 'complete', 'report': {'action_items': []}}
        assert _compute_next_action(data) is None

    def test_blocked_analysis(self):
        from cli.main import _compute_next_action
        data = {
            'phase': 'in_progress',
            'analysis': {'fix_component': 'Fix the Dockerfile'},
            'steps': [],
        }
        result = _compute_next_action(data)
        assert 'Dockerfile' in result

    def test_pending_step(self):
        from cli.main import _compute_next_action
        data = {
            'phase': 'in_progress',
            'analysis': {},
            'steps': [
                {'status': 'done', 'label': 'Repo created', 'fix': ''},
                {'status': 'pending', 'label': 'PaC webhook', 'fix': 'configure webhook'},
            ],
        }
        result = _compute_next_action(data)
        assert 'PaC webhook' in result

    def test_bot_errors(self):
        from cli.main import _compute_next_action
        data = {
            'phase': 'in_progress',
            'analysis': {},
            'bot_error_analysis': {
                'stuck_steps': {'push-image': 5, 'build': 2},
            },
            'steps': [],
        }
        result = _compute_next_action(data)
        assert 'push-image' in result


# ---------------------------------------------------------------------------
# _get_review_jira_client helper
# ---------------------------------------------------------------------------
class TestGetReviewJiraClient:
    @patch.dict(os.environ, {'JIRA_EMAIL': '', 'JIRA_TOKEN': ''}, clear=False)
    def test_no_credentials(self):
        from cli.main import _get_review_jira_client
        assert _get_review_jira_client() is None

    @patch.dict(os.environ, {'JIRA_EMAIL': 'me@test.com', 'JIRA_TOKEN': 'tok123'}, clear=False)
    @patch('clients.jira_client.JiraClient')
    def test_with_credentials(self, mock_jira_cls):
        from cli.main import _get_review_jira_client
        client = _get_review_jira_client()
        assert client is not None


# ---------------------------------------------------------------------------
# nightly-history command
# ---------------------------------------------------------------------------
class TestNightlyHistoryCommand:
    @patch('cli.api_client.get_client')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_nightly_history_with_data(self, mock_cluster, mock_client, runner):
        mock_client.return_value.get.return_value = {
            'nightly_builds': [
                {'status': 'Succeeded', 'build_date': '2026-06-30',
                 'pipelinerun_name': 'pr-nightly-123'},
            ],
            'fbc_fragments': [
                {'component_name': 'fbc-frag', 'last_successful_build': '2026-06-30T10:00:00Z',
                 'current_status': 'Succeeded'},
            ],
        }
        result = runner.invoke(cli, ['nightly-history'])
        assert 'Nightly History' in result.output
        assert 'Operator Builds' in result.output

    @patch('cli.api_client.get_client')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_nightly_history_no_data(self, mock_cluster, mock_client, runner):
        mock_client.return_value.get.return_value = None
        result = runner.invoke(cli, ['nightly-history'])
        assert 'No nightly history' in result.output

    @patch('cli.api_client.get_client')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_nightly_history_json(self, mock_cluster, mock_client, runner):
        mock_client.return_value.get.return_value = {'nightly_builds': []}
        result = runner.invoke(cli, ['--json', 'nightly-history'])
        parsed = json.loads(result.output)
        assert 'nightly_builds' in parsed


# ---------------------------------------------------------------------------
# get vulnerabilities command
# ---------------------------------------------------------------------------
class TestGetVulnerabilities:
    @patch('clients.registry_client.RegistryClient')
    @patch('clients.konflux_client.KonfluxClient')
    def test_no_snapshots(self, mock_kc_cls, mock_rc_cls, runner):
        mock_kc_cls.return_value.get_snapshots.return_value = []
        result = runner.invoke(cli, ['get', 'vulnerabilities'])
        assert 'No snapshots found' in result.output

    def test_import_error(self, runner):
        with patch.dict('sys.modules', {'clients.konflux_client': None}):
            # Will get ImportError
            result = runner.invoke(cli, ['get', 'vulnerabilities'])
            # Should handle gracefully
            assert result.exit_code != 0


# ---------------------------------------------------------------------------
# get pipelineruns command
# ---------------------------------------------------------------------------
class TestGetPipelineruns:
    @patch('cli.data.get_failures')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_pipelineruns_display(self, mock_cluster, mock_failures, runner):
        mock_failures.return_value = [
            {'component_name': 'comp-a', 'status': 'Failed',
             'error_type': 'build_error'},
        ]
        result = runner.invoke(cli, ['get', 'pipelineruns'])
        assert 'comp-a' in result.output
        assert 'Build Failures' in result.output

    @patch('cli.data.get_failures')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_pipelineruns_json(self, mock_cluster, mock_failures, runner):
        mock_failures.return_value = []
        result = runner.invoke(cli, ['--json', 'get', 'pipelineruns'])
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)


# ---------------------------------------------------------------------------
# get pipelinerun (single) command
# ---------------------------------------------------------------------------
class TestGetPipelinerun:
    @patch('cli.data.get_failure_details')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_pipelinerun_found(self, mock_cluster, mock_details, runner):
        mock_details.return_value = {'component': 'comp-a', 'status': 'Failed'}
        result = runner.invoke(cli, ['get', 'pipelinerun', 'comp-a'])
        assert 'comp-a' in result.output

    @patch('cli.data.get_failure_details')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_pipelinerun_not_found(self, mock_cluster, mock_details, runner):
        mock_details.return_value = None
        result = runner.invoke(cli, ['get', 'pipelinerun', 'missing'])
        assert 'not found' in result.output


# ---------------------------------------------------------------------------
# describe pipelinerun command
# ---------------------------------------------------------------------------
class TestDescribePipelinerun:
    @patch('cli.data.get_failure_details')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_describe_pr_with_data(self, mock_cluster, mock_details, runner):
        mock_details.return_value = {
            'component': 'comp-a', 'status': 'Failed',
            'error_type': 'build_error',
        }
        result = runner.invoke(cli, ['describe', 'pipelinerun', 'comp-a'])
        assert 'PipelineRun: comp-a' in result.output

    @patch('cli.data.get_failure_details')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_describe_pr_not_found(self, mock_cluster, mock_details, runner):
        mock_details.return_value = None
        result = runner.invoke(cli, ['describe', 'pipelinerun', 'missing'])
        assert 'null' in result.output  # prints json null


# ---------------------------------------------------------------------------
# _update_env_var helper
# ---------------------------------------------------------------------------
class TestUpdateEnvVar:
    def test_update_existing_var(self, tmp_path):
        from cli.main import _update_env_var
        env_file = tmp_path / '.env'
        env_file.write_text('OTHER_VAR=hello\nMY_VAR=old\n')
        with patch('cli.config.PROJECT_DIR', str(tmp_path)):
            _update_env_var('MY_VAR', 'new')
        content = env_file.read_text()
        assert 'MY_VAR=new' in content
        assert os.environ.get('MY_VAR') == 'new'

    def test_add_new_var(self, tmp_path):
        from cli.main import _update_env_var
        env_file = tmp_path / '.env'
        env_file.write_text('OTHER=123\n')
        with patch('cli.config.PROJECT_DIR', str(tmp_path)):
            _update_env_var('NEW_VAR', 'value')
        content = env_file.read_text()
        assert 'NEW_VAR=value' in content

    def test_value_with_spaces(self, tmp_path):
        from cli.main import _update_env_var
        env_file = tmp_path / '.env'
        env_file.write_text('')
        with patch('cli.config.PROJECT_DIR', str(tmp_path)):
            _update_env_var('APPS', 'app1 app2')
        content = env_file.read_text()
        assert 'APPS="app1 app2"' in content

    def test_create_env_file(self, tmp_path):
        from cli.main import _update_env_var
        with patch('cli.config.PROJECT_DIR', str(tmp_path)):
            _update_env_var('TEST_VAR', 'val')
        assert (tmp_path / '.env').exists()


# ---------------------------------------------------------------------------
# conforma report command
# ---------------------------------------------------------------------------
class TestConformaReportCommand:
    @patch('cli.data.get_conforma_report')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_report_string(self, mock_cluster, mock_report, runner):
        mock_report.return_value = 'Report text output'
        result = runner.invoke(cli, ['conforma', 'report'])
        assert 'Report text output' in result.output

    @patch('cli.data.get_conforma_report')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_report_dict(self, mock_cluster, mock_report, runner):
        mock_report.return_value = {'key': 'value'}
        result = runner.invoke(cli, ['conforma', 'report'])
        assert '"key"' in result.output

    @patch('cli.data.get_conforma_report')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_report_list(self, mock_cluster, mock_report, runner):
        mock_report.return_value = [
            {'component_name': 'comp-a', 'violations_count': 3,
             'warnings_count': 0, 'scenario': 'test-scenario'},
        ]
        result = runner.invoke(cli, ['conforma', 'report'])
        assert 'comp-a' in result.output


# ---------------------------------------------------------------------------
# get releases command
# ---------------------------------------------------------------------------
class TestGetReleases:
    @patch('cli.data.get_schedule')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_releases_with_schedule(self, mock_cluster, mock_sched, runner):
        mock_sched.return_value = {
            'code_freeze': '2026-07-24',
            'code_freeze_days': 23,
            'release_date': '2026-08-20',
            'release_date_days': 50,
        }
        result = runner.invoke(cli, ['get', 'releases'])
        assert 'Release Schedule' in result.output
        assert 'code_freeze' in result.output

    @patch('cli.data.get_schedule')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_releases_no_schedule(self, mock_cluster, mock_sched, runner):
        mock_sched.return_value = None
        result = runner.invoke(cli, ['get', 'releases'])
        assert 'No release schedule data' in result.output

    @patch('cli.data.get_schedule')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_releases_json(self, mock_cluster, mock_sched, runner):
        mock_sched.return_value = {'release_date': '2026-08-20'}
        result = runner.invoke(cli, ['--json', 'get', 'releases'])
        parsed = json.loads(result.output)
        assert 'release_date' in parsed


# ---------------------------------------------------------------------------
# get fixes command
# ---------------------------------------------------------------------------
class TestGetFixes:
    @patch('cli.data.get_fixes')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_fixes_with_data(self, mock_cluster, mock_fixes, runner):
        mock_fixes.return_value = [
            {'component_name': 'comp-a', 'status': 'success', 'pr_url': 'https://pr.url'},
        ]
        result = runner.invoke(cli, ['get', 'fixes'])
        assert 'comp-a' in result.output
        assert 'success' in result.output

    @patch('cli.data.get_fixes')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_fixes_empty(self, mock_cluster, mock_fixes, runner):
        mock_fixes.return_value = []
        result = runner.invoke(cli, ['get', 'fixes'])
        assert 'No fix attempts' in result.output

    @patch('cli.data.get_fixes')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_fixes_json(self, mock_cluster, mock_fixes, runner):
        mock_fixes.return_value = []
        result = runner.invoke(cli, ['--json', 'get', 'fixes'])
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)


# ---------------------------------------------------------------------------
# main() entry point
# ---------------------------------------------------------------------------
class TestMainEntryPoint:
    @patch('cli.main._bash_fallback')
    def test_numeric_shortcut(self, mock_fallback):
        with patch('sys.argv', ['ic', '3']):
            from cli.main import main
            try:
                main()
            except SystemExit:
                pass
        mock_fallback.assert_called_once_with(['3'])

    @patch('cli.main.cli')
    def test_pr_alias(self, mock_cli):
        original_argv = sys.argv[:]
        try:
            sys.argv = ['ic', 'pr', 'comp-name']
            from cli.main import main
            try:
                main()
            except (SystemExit, Exception):
                pass
            # Verify sys.argv was rewritten to use 'get pipelinerun'
            assert sys.argv[1:4] == ['get', 'pipelinerun', 'comp-name']
        finally:
            sys.argv = original_argv

    @patch('cli.main.cli')
    def test_its_alias(self, mock_cli):
        original_argv = sys.argv[:]
        try:
            sys.argv = ['ic', 'its']
            from cli.main import main
            try:
                main()
            except (SystemExit, Exception):
                pass
            assert sys.argv[1:] == ['conforma', 'scenarios']
        finally:
            sys.argv = original_argv


# ---------------------------------------------------------------------------
# describe release command
# ---------------------------------------------------------------------------
class TestDescribeRelease:
    @patch('cli.data.get_release_details')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_release_not_found(self, mock_cluster, mock_details, runner):
        mock_details.return_value = None
        result = runner.invoke(cli, ['describe', 'release', 'missing-release'])
        assert result.exit_code != 0

    @patch('cli.data.get_release_details')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_release_basic(self, mock_cluster, mock_details, runner):
        mock_details.return_value = {
            'snapshot': 'snap-123',
            'component_count': 10,
            'release_plan': 'plan-1',
            'target': 'stage',
            'type_label': 'Managed',
            'created_at': '2026-06-30',
            'conditions': [
                {'type': 'Released', 'status': 'True', 'reason': 'Succeeded',
                 'message': 'OK'},
            ],
        }
        result = runner.invoke(cli, ['describe', 'release', 'rel-1'])
        assert 'Release: rel-1' in result.output
        assert 'snap-123' in result.output

    @patch('cli.data.get_release_details')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_release_failed_with_ai(self, mock_cluster, mock_details, runner):
        mock_details.return_value = {
            'snapshot': 'snap-123',
            'release_plan': 'plan-1',
            'target': 'prod',
            'type_label': 'Managed',
            'created_at': '2026-06-30',
            'is_failed': True,
            'error_details': ['EC policy check failed'],
            'ec_policy': 'prod-policy',
            'ec_policy_url': 'https://policy.url',
            'ai_analysis': {
                'root_cause': 'Missing signatures',
                'failure_category': 'signing_issue',
                'recommended_fix': 'Re-sign images',
                'confidence_score': 0.95,
            },
            'conditions': [
                {'type': 'Released', 'status': 'False', 'reason': 'Failed',
                 'message': 'EC check failed'},
            ],
        }
        result = runner.invoke(cli, ['describe', 'release', 'rel-2'])
        assert 'Error Details' in result.output
        assert 'EC Policy' in result.output
        assert 'AI Analysis' in result.output

    @patch('cli.data.get_release_details')
    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_release_json(self, mock_cluster, mock_details, runner):
        mock_details.return_value = {
            'snapshot': 'snap-123', 'release_plan': 'plan-1',
            'target': 'stage', 'type_label': 'M', 'created_at': '2026-06-30',
        }
        result = runner.invoke(cli, ['--json', 'describe', 'release', 'rel-3'])
        parsed = json.loads(result.output)
        assert parsed['snapshot'] == 'snap-123'
