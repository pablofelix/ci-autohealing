"""Comprehensive tests for remaining uncovered CLI commands in src/cli/main.py.

Covers: release group, report group, health group, jira group, patterns group,
conforma subcommands (rules/csv/snapshot/scenarios/evaluate/catalog),
describe release, backward-compat aliases, _bash_fallback, main() entry point,
_print_check_line helper, _compute_next_action, and additional paths in
already-tested commands.
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from cli.main import cli  # noqa: E402


@pytest.fixture
def runner():
    return CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _NoopSpinner:
    """A no-op replacement for cli.progress.Spinner context manager."""
    def __init__(self, *args, **kwargs):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass


NOOP_SPINNER = _NoopSpinner


# ===================================================================
#  RELEASE GROUP
# ===================================================================

class TestReleaseGroup:
    """Tests for `ic release` group."""

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.api_client.get_client')
    def test_release_no_subcommand_invokes_status(self, mock_client_fn, mock_cluster, runner):
        mock_client = MagicMock()
        mock_client.get.return_value = {
            'build_failures': 0,
            'conforma_violations': 0,
            'verdict': 'READY',
            'blockers': [],
            'risks': [],
        }
        mock_client_fn.return_value = mock_client
        result = runner.invoke(cli, ['release'])
        assert result.exit_code == 0

    @patch('cli.mode.ensure_cluster', return_value=False)
    @patch('cli.main._bash_fallback')
    def test_release_no_subcommand_fallback(self, mock_fallback, mock_cluster, runner):
        mock_fallback.side_effect = SystemExit(0)
        result = runner.invoke(cli, ['release'])
        mock_fallback.assert_called()

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.api_client.get_client')
    def test_release_status_ready(self, mock_client_fn, mock_cluster, runner):
        mock_client = MagicMock()
        mock_client.get.return_value = {
            'build_failures': 0,
            'conforma_violations': 0,
            'verdict': 'READY',
            'blockers': [],
            'risks': [],
            'checks': [
                {'name': 'Test Check', 'status': 'PASS', 'detail': 'All good'},
            ],
        }
        mock_client_fn.return_value = mock_client
        result = runner.invoke(cli, ['release', 'status'])
        assert result.exit_code == 0
        assert 'READY' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.api_client.get_client')
    def test_release_status_not_ready(self, mock_client_fn, mock_cluster, runner):
        mock_client = MagicMock()
        mock_client.get.return_value = {
            'build_failures': 3,
            'conforma_violations': 5,
            'verdict': 'NOT_READY',
            'blockers': ['Build failures exist', 'Conforma violations'],
            'risks': [],
            'freeze': {'end_date': '2025-07-15', 'reason': 'RC freeze'},
            'schedule': {
                'release_date': '2025-07-20',
                'release_date_days': 5,
                'code_freeze': '2025-07-10',
                'code_freeze_days': -2,
                'release_window_start': '2025-07-18',
                'release_window_start_days': 3,
            },
        }
        mock_client_fn.return_value = mock_client
        result = runner.invoke(cli, ['release', 'status'])
        assert result.exit_code == 0
        assert 'NOT READY' in result.output
        assert 'Build failures exist' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.api_client.get_client')
    def test_release_status_at_risk(self, mock_client_fn, mock_cluster, runner):
        mock_client = MagicMock()
        mock_client.get.return_value = {
            'build_failures': 0,
            'conforma_violations': 0,
            'verdict': 'AT_RISK',
            'blockers': [],
            'risks': ['Code freeze approaching'],
            'checks': [
                {'name': 'Infra Check', 'status': 'WARN', 'detail': 'Slow', 'fix': 'Upgrade'},
                {'name': 'Skipped', 'status': 'SKIP', 'detail': 'N/A'},
            ],
        }
        mock_client_fn.return_value = mock_client
        result = runner.invoke(cli, ['release', 'status'])
        assert result.exit_code == 0
        assert 'AT RISK' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.api_client.get_client')
    def test_release_status_full_option(self, mock_client_fn, mock_cluster, runner):
        mock_client = MagicMock()
        mock_client.get.return_value = {
            'build_failures': 0,
            'conforma_violations': 0,
            'verdict': 'READY',
            'blockers': [],
            'risks': [],
        }
        mock_client_fn.return_value = mock_client
        result = runner.invoke(cli, ['release', 'status', '--full'])
        assert result.exit_code == 0
        mock_client.get.assert_called_once()
        call_args = mock_client.get.call_args
        assert call_args[1].get('params', {}).get('full') == 'true'

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.api_client.get_client')
    def test_release_status_no_data(self, mock_client_fn, mock_cluster, runner):
        mock_client = MagicMock()
        mock_client.get.return_value = None
        mock_client_fn.return_value = mock_client
        result = runner.invoke(cli, ['release', 'status'])
        assert result.exit_code == 0
        assert 'No readiness data' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_release_diff(self, mock_cluster, runner):
        result = runner.invoke(cli, ['release', 'diff'])
        assert result.exit_code == 0
        assert 'release diff requires direct cluster access' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    def test_release_verify(self, mock_cluster, runner):
        result = runner.invoke(cli, ['release', 'verify'])
        assert result.exit_code == 0
        assert 'release verify requires direct cluster' in result.output


# ===================================================================
#  REPORT GROUP
# ===================================================================

class TestReportGroup:
    """Tests for `ic report build` and `ic report conforma`."""

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_alerts', return_value={
        'build_failures': [
            {'component': 'comp-a', 'error_type': 'compile-error'},
            {'component': 'comp-b', 'error_type': 'timeout'},
        ],
    })
    def test_report_build_with_failures(self, mock_alerts, mock_cluster, runner):
        result = runner.invoke(cli, ['report', 'build'])
        assert result.exit_code == 0
        assert 'Build Report' in result.output
        assert 'comp-a' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_alerts', return_value={'build_failures': []})
    def test_report_build_no_failures(self, mock_alerts, mock_cluster, runner):
        result = runner.invoke(cli, ['report', 'build'])
        assert result.exit_code == 0
        assert 'No build failures' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_conforma_report', return_value=[
        {'component': 'comp-a', 'violations_count': 2, 'warnings_count': 1,
         'scenario': 'its-scenario'},
    ])
    def test_report_conforma_delegates(self, mock_report, mock_cluster, runner):
        result = runner.invoke(cli, ['report', 'conforma'])
        assert result.exit_code == 0
        assert 'Conforma Report' in result.output


# ===================================================================
#  HEALTH GROUP
# ===================================================================

class TestHealthGroup:
    """Tests for `ic health` group."""

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db._get_db_connection')
    @patch('proactive.health_monitor.HealthMonitor')
    def test_health_no_subcommand_with_data(self, mock_monitor_cls, mock_conn, mock_db, runner):
        mock_monitor = MagicMock()
        mock_monitor.get_component_health_summary.return_value = [
            {'component_name': 'comp-a', 'health_status': 'critical',
             'health_score': 30, 'consecutive_failures': 5,
             'success_rate_last_7d': 40.0},
            {'component_name': 'comp-b', 'health_status': 'healthy',
             'health_score': 95, 'consecutive_failures': 0,
             'success_rate_last_7d': 100.0},
        ]
        mock_monitor_cls.return_value = mock_monitor
        result = runner.invoke(cli, ['health'])
        assert result.exit_code == 0
        assert 'Component Health' in result.output
        assert 'comp-a' in result.output

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db._get_db_connection')
    @patch('proactive.health_monitor.HealthMonitor')
    def test_health_no_data(self, mock_monitor_cls, mock_conn, mock_db, runner):
        mock_monitor = MagicMock()
        mock_monitor.get_component_health_summary.return_value = []
        mock_monitor_cls.return_value = mock_monitor
        result = runner.invoke(cli, ['health'])
        assert result.exit_code == 0
        assert 'No component health data' in result.output

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db._get_db_connection')
    @patch('proactive.health_monitor.HealthMonitor')
    def test_health_all_healthy(self, mock_monitor_cls, mock_conn, mock_db, runner):
        mock_monitor = MagicMock()
        mock_monitor.get_component_health_summary.return_value = [
            {'component_name': 'comp-a', 'health_status': 'healthy',
             'health_score': 95, 'consecutive_failures': 0,
             'success_rate_last_7d': 100.0},
        ]
        mock_monitor_cls.return_value = mock_monitor
        result = runner.invoke(cli, ['health'])
        assert result.exit_code == 0
        assert 'All components healthy' in result.output

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db._get_db_connection')
    @patch('proactive.health_monitor.HealthMonitor')
    def test_health_error(self, mock_monitor_cls, mock_conn, mock_db, runner):
        mock_monitor = MagicMock()
        mock_monitor.get_component_health_summary.side_effect = Exception('DB error')
        mock_monitor_cls.return_value = mock_monitor
        result = runner.invoke(cli, ['health'])
        assert result.exit_code == 0
        assert 'Error' in result.output

    @patch('cli.db.require_db', return_value=False)
    def test_health_no_db(self, mock_db, runner):
        result = runner.invoke(cli, ['health'])
        assert result.exit_code == 0

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db._get_db_connection')
    @patch('proactive.health_monitor.HealthMonitor')
    def test_health_warnings(self, mock_monitor_cls, mock_conn, mock_db, runner):
        mock_warning = MagicMock()
        mock_warning.signal_type = 'consecutive_failures'
        mock_warning.severity = 'critical'
        mock_warning.message = 'comp-x has 5 consecutive failures'
        mock_monitor = MagicMock()
        mock_monitor.run_checks.return_value = [mock_warning]
        mock_monitor_cls.return_value = mock_monitor
        result = runner.invoke(cli, ['health', 'warnings'])
        assert result.exit_code == 0
        assert 'Proactive Warnings' in result.output
        assert 'comp-x' in result.output

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db._get_db_connection')
    @patch('proactive.health_monitor.HealthMonitor')
    def test_health_warnings_none(self, mock_monitor_cls, mock_conn, mock_db, runner):
        mock_monitor = MagicMock()
        mock_monitor.run_checks.return_value = []
        mock_monitor_cls.return_value = mock_monitor
        result = runner.invoke(cli, ['health', 'warnings'])
        assert result.exit_code == 0
        assert 'No warnings' in result.output

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db._get_db_connection')
    @patch('proactive.health_monitor.HealthMonitor')
    def test_health_warnings_error(self, mock_monitor_cls, mock_conn, mock_db, runner):
        mock_monitor = MagicMock()
        mock_monitor.run_checks.side_effect = Exception('check failed')
        mock_monitor_cls.return_value = mock_monitor
        result = runner.invoke(cli, ['health', 'warnings'])
        assert result.exit_code == 0
        assert 'Error' in result.output


# ===================================================================
#  JIRA GROUP
# ===================================================================

class TestJiraGroup:
    """Tests for `ic jira` group commands."""

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_alerts', return_value={
        'build_failures': [
            {'component': 'comp-a', 'jira_key': 'RHODS-123'},
        ],
        'conforma_violations': [],
    })
    def test_jira_no_subcommand_invokes_status(self, mock_alerts, mock_cluster, runner):
        result = runner.invoke(cli, ['jira'])
        assert result.exit_code == 0
        assert 'Jira Status' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_export', return_value={'text': 'Bug: comp-a failed'})
    def test_jira_create_with_component(self, mock_export, mock_cluster, runner):
        result = runner.invoke(cli, ['jira', 'create', 'comp-a'])
        assert result.exit_code == 0
        assert 'Bug: comp-a failed' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_export', return_value=None)
    def test_jira_create_no_data(self, mock_export, mock_cluster, runner):
        result = runner.invoke(cli, ['jira', 'create', 'comp-x'])
        assert result.exit_code == 0
        assert 'No data for' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_export', return_value={'key': 'val'})
    def test_jira_create_json_export(self, mock_export, mock_cluster, runner):
        result = runner.invoke(cli, ['jira', 'create', 'comp-a'])
        assert result.exit_code == 0
        assert 'val' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_triage_items', return_value=[
        {'jira_key': 'RHODS-100', 'components': ['comp-a', 'comp-b']},
        {'jira_key': '', 'components': ['comp-c']},
    ])
    def test_jira_inbox(self, mock_items, mock_cluster, runner):
        result = runner.invoke(cli, ['jira', 'inbox'])
        assert result.exit_code == 0
        assert 'Jira Inbox' in result.output
        assert 'RHODS-100' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_triage_items', return_value=[
        {'components': ['comp-a']},
    ])
    def test_jira_inbox_no_jira(self, mock_items, mock_cluster, runner):
        result = runner.invoke(cli, ['jira', 'inbox'])
        assert result.exit_code == 0
        assert 'No Jira tickets linked' in result.output

    @patch('cli.main._bash_fallback')
    def test_jira_link_fallback(self, mock_fallback, runner):
        mock_fallback.side_effect = SystemExit(0)
        result = runner.invoke(cli, ['jira', 'link', 'RHODS-123'])
        mock_fallback.assert_called_with(['jira', 'link', 'RHODS-123'])

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_alerts', return_value={
        'build_failures': [
            {'component': 'comp-a', 'jira_key': 'RHODS-100'},
        ],
        'conforma_violations': [
            {'component': 'comp-b'},
        ],
    })
    def test_jira_status(self, mock_alerts, mock_cluster, runner):
        result = runner.invoke(cli, ['jira', 'status'])
        assert result.exit_code == 0
        assert 'Jira Status' in result.output
        assert 'RHODS-100' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_alerts', return_value={
        'build_failures': [],
        'conforma_violations': [],
    })
    def test_jira_status_no_jira(self, mock_alerts, mock_cluster, runner):
        result = runner.invoke(cli, ['jira', 'status'])
        assert result.exit_code == 0
        assert 'No Jira tickets linked' in result.output


# ===================================================================
#  PATTERNS GROUP
# ===================================================================

class TestPatternsGroup:
    """Tests for `ic patterns` group commands."""

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    @patch('cli.db.print_table')
    def test_patterns_no_subcommand(self, mock_print, mock_repo_fn, mock_db, mock_cluster, runner):
        mock_repo = MagicMock()
        mock_repo.get_all.return_value = [
            {'failure_type': 'build', 'failure_category': 'compile',
             'occurrence_count': 5, 'avg_confidence': 0.85,
             'doc_context': None, 'last_seen_at': '2025-06-01',
             'pattern_name': 'go-compile-error'},
        ]
        mock_repo.get_library_summary.return_value = {'total': 1}
        mock_repo_fn.return_value = mock_repo
        result = runner.invoke(cli, ['patterns'])
        assert result.exit_code == 0

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    def test_patterns_learn_creates(self, mock_repo_fn, mock_db, runner):
        mock_repo = MagicMock()
        mock_repo.discover_new_patterns.return_value = [
            {'pattern_name': 'new-p', 'failure_category': 'timeout'},
        ]
        mock_repo_fn.return_value = mock_repo
        result = runner.invoke(cli, ['patterns', 'learn'])
        assert result.exit_code == 0
        assert 'Created 1 new pattern' in result.output

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    def test_patterns_learn_none(self, mock_repo_fn, mock_db, runner):
        mock_repo = MagicMock()
        mock_repo.discover_new_patterns.return_value = []
        mock_repo_fn.return_value = mock_repo
        result = runner.invoke(cli, ['patterns', 'learn'])
        assert result.exit_code == 0
        assert 'No new patterns' in result.output

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    def test_patterns_learn_error(self, mock_repo_fn, mock_db, runner):
        mock_repo = MagicMock()
        mock_repo.discover_new_patterns.side_effect = Exception('DB err')
        mock_repo_fn.return_value = mock_repo
        result = runner.invoke(cli, ['patterns', 'learn'])
        assert result.exit_code == 0
        assert 'Error' in result.output

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    def test_patterns_stale(self, mock_repo_fn, mock_db, runner):
        mock_repo = MagicMock()
        mock_repo.get_stale_patterns.return_value = [
            {'pattern_name': 'old-p', 'avg_confidence': 0.3, 'last_used_at': '2024-01-01'},
        ]
        mock_repo_fn.return_value = mock_repo
        result = runner.invoke(cli, ['patterns', 'stale'])
        assert result.exit_code == 0
        assert 'old-p' in result.output

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    def test_patterns_stale_none(self, mock_repo_fn, mock_db, runner):
        mock_repo = MagicMock()
        mock_repo.get_stale_patterns.return_value = []
        mock_repo_fn.return_value = mock_repo
        result = runner.invoke(cli, ['patterns', 'stale'])
        assert result.exit_code == 0
        assert 'No stale patterns' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.api_client.get_client')
    def test_patterns_shared(self, mock_client_fn, mock_cluster, runner):
        mock_client = MagicMock()
        mock_client.get.return_value = [
            {'pattern_name': 'shared-p', 'failure_category': 'auth',
             'occurrence_count': 3},
        ]
        mock_client_fn.return_value = mock_client
        result = runner.invoke(cli, ['patterns', 'shared'])
        assert result.exit_code == 0
        assert 'shared-p' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.api_client.get_client')
    def test_patterns_shared_none(self, mock_client_fn, mock_cluster, runner):
        mock_client = MagicMock()
        mock_client.get.return_value = []
        mock_client_fn.return_value = mock_client
        result = runner.invoke(cli, ['patterns', 'shared'])
        assert result.exit_code == 0
        assert 'No shared patterns' in result.output

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    @patch('cli.db.print_table')
    def test_patterns_list(self, mock_print, mock_repo_fn, mock_db, runner):
        mock_repo = MagicMock()
        mock_repo.get_all.return_value = [
            {'failure_type': 'build', 'failure_category': 'compile',
             'occurrence_count': 5, 'avg_confidence': 0.85,
             'doc_context': 'some doc', 'last_seen_at': '2025-06-01',
             'pattern_name': 'go-compile-error'},
        ]
        mock_repo.get_library_summary.return_value = {'total': 1}
        mock_repo_fn.return_value = mock_repo
        result = runner.invoke(cli, ['patterns', 'list'])
        assert result.exit_code == 0
        assert '1 patterns total' in result.output

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    def test_patterns_show_found(self, mock_repo_fn, mock_db, runner):
        mock_repo = MagicMock()
        mock_repo.get_by_name_or_category.return_value = {
            'pattern_name': 'go-compile', 'failure_type': 'build',
            'failure_category': 'compile', 'created_by': 'ai',
            'occurrence_count': 10, 'avg_confidence': 0.9,
            'first_seen_at': '2025-01-01', 'last_seen_at': '2025-06-01',
            'description': 'Go compilation error',
            'typical_fix': 'Fix the syntax',
            'doc_url': 'https://example.com',
            'doc_context': 'line1\nline2\nline3',
        }
        mock_repo_fn.return_value = mock_repo
        result = runner.invoke(cli, ['patterns', 'show', 'go-compile'])
        assert result.exit_code == 0
        assert 'go-compile' in result.output
        assert 'Fix the syntax' in result.output

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    def test_patterns_show_not_found(self, mock_repo_fn, mock_db, runner):
        mock_repo = MagicMock()
        mock_repo.get_by_name_or_category.return_value = None
        mock_repo_fn.return_value = mock_repo
        result = runner.invoke(cli, ['patterns', 'show', 'nonexistent'])
        assert result.exit_code != 0
        assert 'Pattern not found' in result.output

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    @patch('skills.db_registry.get_registry')
    def test_patterns_link_skill_success(self, mock_registry_fn, mock_repo_fn, mock_db, runner):
        mock_repo = MagicMock()
        mock_repo.get_by_name_or_category.return_value = {
            'id': 1, 'pattern_name': 'go-compile',
        }
        mock_conn = MagicMock()
        mock_repo.db.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_repo.db.connection.return_value.__exit__ = MagicMock(return_value=False)
        mock_repo_fn.return_value = mock_repo

        mock_skill = MagicMock()
        mock_skill.qualified_name = 'src/fix-compile'
        mock_skill.name = 'fix-compile'
        mock_registry = MagicMock()
        mock_registry.get_skill.return_value = mock_skill
        mock_registry_fn.return_value = mock_registry

        result = runner.invoke(cli, ['patterns', 'link-skill', 'go-compile', 'fix-compile'])
        assert result.exit_code == 0
        assert 'Linked' in result.output

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    def test_patterns_link_skill_pattern_not_found(self, mock_repo_fn, mock_db, runner):
        mock_repo = MagicMock()
        mock_repo.get_by_name_or_category.return_value = None
        mock_repo_fn.return_value = mock_repo
        result = runner.invoke(cli, ['patterns', 'link-skill', 'missing', 'skill'])
        assert result.exit_code != 0
        assert 'Pattern not found' in result.output


# ===================================================================
#  CONFORMA SUBCOMMANDS (rules, csv, snapshot, scenarios, evaluate, catalog)
# ===================================================================

class TestConformaRules:
    """Tests for `ic conforma rules`."""

    @patch('cli.db.check_db', return_value=True)
    @patch('cli.mode.ensure_cluster', return_value=False)
    @patch('cli.data.get_conforma_rules', return_value=[
        {'rule': 'hermetic_check', 'count': 5, 'solution': 'Set hermetic flag',
         'components': ['comp-a', 'comp-b']},
        {'rule': 'fips_check', 'count': 2, 'solution': '',
         'components': ['comp-c']},
    ])
    @patch('conforma.policy_tools.strip_version_suffix', side_effect=lambda x: x)
    def test_conforma_rules_with_data(self, mock_strip, mock_rules, mock_cluster, mock_db, runner):
        result = runner.invoke(cli, ['conforma', 'rules', '--app', 'rhoai-v2-17'])
        assert result.exit_code == 0
        assert 'hermetic_check' in result.output
        assert '5 components' in result.output

    @patch('cli.db.check_db', return_value=True)
    @patch('cli.mode.ensure_cluster', return_value=False)
    @patch('cli.data.get_conforma_rules', return_value=[])
    def test_conforma_rules_empty(self, mock_rules, mock_cluster, mock_db, runner):
        result = runner.invoke(cli, ['conforma', 'rules', '--app', 'rhoai-v2-17'])
        assert result.exit_code == 0
        assert 'No violations found' in result.output

    @patch('cli.db.check_db', return_value=True)
    @patch('cli.mode.ensure_cluster', return_value=False)
    @patch('cli.data.get_conforma_rules', return_value=[
        {'rule': 'fips_check', 'count': 1, 'solution': 'x' * 200,
         'components': ['c' + str(i) for i in range(12)]},
    ])
    @patch('conforma.policy_tools.strip_version_suffix', side_effect=lambda x: x)
    def test_conforma_rules_many_comps(self, mock_strip, mock_rules, mock_cluster, mock_db, runner):
        result = runner.invoke(cli, ['conforma', 'rules', '--app', 'rhoai-v2-17'])
        assert result.exit_code == 0
        assert '+' in result.output  # overflow indicator

    @patch('cli.db.check_db', return_value=True)
    @patch('cli.mode.ensure_cluster', return_value=False)
    @patch('cli.data.get_conforma_rules', return_value=[
        {'rule': 'r1', 'count': 1, 'solution': '', 'components': ['c1']},
    ])
    @patch('conforma.policy_tools.strip_version_suffix', side_effect=lambda x: x)
    def test_conforma_rules_stage(self, mock_strip, mock_rules, mock_cluster, mock_db, runner):
        result = runner.invoke(cli, ['conforma', 'rules', '--stage'])
        assert result.exit_code == 0
        assert 'stage' in result.output

    @patch('cli.db.check_db', return_value=True)
    @patch('cli.mode.ensure_cluster', return_value=False)
    @patch('cli.data.get_conforma_rules', return_value=[
        {'rule': 'r1', 'count': 1, 'solution': '', 'components': ['c1']},
    ])
    @patch('conforma.policy_tools.strip_version_suffix', side_effect=lambda x: x)
    def test_conforma_rules_nightly(self, mock_strip, mock_rules, mock_cluster, mock_db, runner):
        result = runner.invoke(cli, ['conforma', 'rules', '--nightly'])
        assert result.exit_code == 0
        assert 'nightly' in result.output


class TestConformaCsv:
    """Tests for `ic conforma csv`."""

    @patch('cli.db.check_db', return_value=True)
    @patch('cli.mode.ensure_cluster', return_value=False)
    @patch('cli.data.get_conforma_violations', return_value=[
        {'component': 'comp-a', 'violations_count': 3, 'unique_violations': 2,
         'warnings_count': 1, 'scenario': 'its-1', 'first_seen': '2025-06-01'},
    ])
    def test_csv_output(self, mock_violations, mock_cluster, mock_db, runner):
        result = runner.invoke(cli, ['conforma', 'csv', '--app', 'rhoai-v2-17'])
        assert result.exit_code == 0
        assert 'component,violations' in result.output
        assert 'comp-a' in result.output

    @patch('cli.db.check_db', return_value=True)
    @patch('cli.mode.ensure_cluster', return_value=False)
    @patch('cli.data.get_conforma_violations', return_value=[])
    def test_csv_empty(self, mock_violations, mock_cluster, mock_db, runner):
        result = runner.invoke(cli, ['conforma', 'csv', '--app', 'rhoai-v2-17'])
        assert result.exit_code == 0
        assert 'component,violations' in result.output


class TestConformaSnapshot:
    """Tests for `ic conforma snapshot`."""

    @patch('cli.main._bash_fallback')
    def test_snapshot_fallback(self, mock_fallback, runner):
        mock_fallback.side_effect = SystemExit(0)
        result = runner.invoke(cli, ['conforma', 'snapshot'])
        mock_fallback.assert_called_with(['conforma', 'snapshot'])


class TestConformaScenarios:
    """Tests for `ic conforma scenarios`."""

    @patch('cli.main._bash_fallback')
    def test_scenarios_fallback(self, mock_fallback, runner):
        mock_fallback.side_effect = SystemExit(0)
        result = runner.invoke(cli, ['conforma', 'scenarios'])
        mock_fallback.assert_called_with(['conforma', 'scenarios'])


class TestConformaEvaluate:
    """Tests for `ic conforma evaluate`."""

    @patch('cli.db.require_db', return_value=False)
    def test_evaluate_no_db(self, mock_db, runner):
        result = runner.invoke(cli, ['conforma', 'evaluate'])
        assert result.exit_code != 0

    @patch('cli.db.require_db', return_value=True)
    @patch('config.CollectorConfig.from_env')
    @patch('collectors.conforma_evaluator.ConformaEvaluator')
    def test_evaluate_success(self, mock_eval_cls, mock_config, mock_db, runner):
        mock_evaluator = MagicMock()
        mock_evaluator.run.return_value = {'evaluated': 10, 'violations': 3}
        mock_eval_cls.return_value = mock_evaluator
        result = runner.invoke(cli, ['conforma', 'evaluate'])
        assert result.exit_code == 0

    @patch('cli.db.require_db', return_value=True)
    @patch('config.CollectorConfig.from_env')
    @patch('collectors.conforma_evaluator.ConformaEvaluator')
    def test_evaluate_auto_stale(self, mock_eval_cls, mock_config, mock_db, runner):
        mock_evaluator = MagicMock()
        mock_evaluator.is_stale.return_value = True
        mock_evaluator.run.return_value = {'evaluated': 10, 'violations': 3}
        mock_eval_cls.return_value = mock_evaluator
        result = runner.invoke(cli, ['conforma', 'evaluate', '--auto'])
        assert result.exit_code == 0
        assert 'stale' in result.output.lower() or 'full evaluation' in result.output

    @patch('cli.db.require_db', return_value=True)
    @patch('config.CollectorConfig.from_env')
    @patch('collectors.conforma_evaluator.ConformaEvaluator')
    def test_evaluate_auto_fresh(self, mock_eval_cls, mock_config, mock_db, runner):
        mock_evaluator = MagicMock()
        mock_evaluator.is_stale.return_value = False
        mock_evaluator.run.return_value = {'evaluated': 2, 'violations': 0}
        mock_eval_cls.return_value = mock_evaluator
        result = runner.invoke(cli, ['conforma', 'evaluate', '--auto'])
        assert result.exit_code == 0
        assert 'fresh' in result.output.lower() or 'incremental' in result.output

    @patch('cli.db.require_db', return_value=True)
    @patch('config.CollectorConfig.from_env')
    @patch('collectors.conforma_evaluator.ConformaEvaluator')
    def test_evaluate_incremental_up_to_date(self, mock_eval_cls, mock_config, mock_db, runner):
        mock_evaluator = MagicMock()
        mock_evaluator.run.return_value = {'evaluated': 0, 'incremental': True}
        mock_eval_cls.return_value = mock_evaluator
        result = runner.invoke(cli, ['conforma', 'evaluate', '--incremental'])
        assert result.exit_code == 0
        assert 'up to date' in result.output.lower()

    @patch('cli.db.require_db', return_value=True)
    @patch('config.CollectorConfig.from_env', side_effect=Exception('bad env'))
    def test_evaluate_config_error(self, mock_config, mock_db, runner):
        result = runner.invoke(cli, ['conforma', 'evaluate'])
        assert result.exit_code != 0
        assert 'Error loading config' in result.output


class TestConformaCatalog:
    """Tests for `ic conforma catalog`."""

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    def test_catalog_stats(self, mock_repo_fn, mock_db, runner):
        mock_repo = MagicMock()
        mock_repo.get_catalog_stats.return_value = {
            'total': 189, 'with_reporter_solution': 50,
            'with_typical_fix': 30, 'packages': 15, 'policy_types': 3,
        }
        mock_repo_fn.return_value = mock_repo
        result = runner.invoke(cli, ['conforma', 'catalog', '--stats'])
        assert result.exit_code == 0
        assert '189' in result.output

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    def test_catalog_search(self, mock_repo_fn, mock_db, runner):
        mock_repo = MagicMock()
        mock_repo.search.return_value = [
            {'rule_id': 'hermetic_build_task', 'description': 'Check hermetic',
             'reporter_solution': 'Set hermetic'},
        ]
        mock_repo_fn.return_value = mock_repo
        result = runner.invoke(cli, ['conforma', 'catalog', '--search', 'hermetic'])
        assert result.exit_code == 0
        assert 'hermetic_build_task' in result.output

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    def test_catalog_by_package(self, mock_repo_fn, mock_db, runner):
        mock_repo = MagicMock()
        mock_repo.get_by_package.return_value = [
            {'rule_id': 'labels_check', 'description': 'Check labels',
             'reporter_solution': ''},
        ]
        mock_repo_fn.return_value = mock_repo
        result = runner.invoke(cli, ['conforma', 'catalog', '--package', 'labels'])
        assert result.exit_code == 0
        assert 'labels_check' in result.output

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    def test_catalog_all(self, mock_repo_fn, mock_db, runner):
        mock_repo = MagicMock()
        mock_repo.get_all.return_value = [
            {'rule_id': 'rule1', 'description': 'desc1', 'reporter_solution': ''},
            {'rule_id': 'rule2', 'description': 'desc2', 'reporter_solution': 'sol'},
        ]
        mock_repo_fn.return_value = mock_repo
        result = runner.invoke(cli, ['conforma', 'catalog'])
        assert result.exit_code == 0
        assert '2 rules' in result.output

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    def test_catalog_empty(self, mock_repo_fn, mock_db, runner):
        mock_repo = MagicMock()
        mock_repo.get_all.return_value = []
        mock_repo_fn.return_value = mock_repo
        result = runner.invoke(cli, ['conforma', 'catalog'])
        assert result.exit_code == 0
        assert 'No rules found' in result.output

    @patch('cli.db.require_db', return_value=False)
    def test_catalog_no_db(self, mock_db, runner):
        result = runner.invoke(cli, ['conforma', 'catalog'])
        # require_db returns False -> sys.exit(1)
        assert result.exit_code == 1


# ===================================================================
#  CONFORMA REPORT AND CATEGORIES
# ===================================================================

class TestConformaReport:
    """Tests for `ic conforma report`."""

    @patch('cli.db.check_db', return_value=True)
    @patch('cli.mode.ensure_cluster', return_value=False)
    @patch('cli.data.get_conforma_violations', return_value=[
        {'component': 'comp-a', 'violations_count': 3, 'unique_violations': 2,
         'warnings_count': 1},
    ])
    def test_conforma_report(self, mock_violations, mock_cluster, mock_db, runner):
        result = runner.invoke(cli, ['conforma', 'report'])
        assert result.exit_code == 0


class TestConformaCategories:
    """Tests for `ic conforma categories`."""

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_conforma_violations', return_value=[
        {'component': 'comp-a', 'violations_count': 3, 'scenario': 'its-scenario-1',
         'error_type': 'hermetic', 'unique_violations': 2, 'violation_summary': ''},
        {'component': 'comp-b', 'violations_count': 1, 'scenario': 'its-scenario-1',
         'error_type': 'hermetic', 'unique_violations': 1, 'violation_summary': ''},
        {'component': 'comp-c', 'violations_count': 2, 'scenario': 'its-scenario-2',
         'error_type': 'fips', 'unique_violations': 0, 'violation_summary': ''},
    ])
    @patch('conforma.policy_tools.categorize_policy', return_value='hermetic')
    @patch('conforma.policy_tools.count_unique_violations', return_value=(0, []))
    def test_categories_with_data(self, mock_count, mock_cat, mock_violations, mock_cluster, runner):
        result = runner.invoke(cli, ['conforma', 'categories'])
        assert result.exit_code == 0
        assert 'Conforma Categories' in result.output
        assert 'By Scenario' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_conforma_violations', return_value=[])
    def test_categories_empty(self, mock_violations, mock_cluster, runner):
        result = runner.invoke(cli, ['conforma', 'categories'])
        assert result.exit_code == 0
        # Even empty, it prints the category template with 0 counts
        assert 'Conforma Categories' in result.output
        assert '0 components' in result.output


# ===================================================================
#  DESCRIBE RELEASE
# ===================================================================

class TestDescribeRelease:
    """Tests for `ic describe release`."""

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_release_details')
    def test_describe_release_full(self, mock_details, mock_cluster, runner):
        mock_details.return_value = {
            'snapshot': 'snap-123',
            'component_count': 50,
            'release_plan': 'rp-prod',
            'type_label': 'push',
            'target': 'production',
            'created_at': '2025-06-01',
            'advisory_type': 'RHEA',
            'fixed_issues': ['RHODS-100', 'RHODS-200'],
            'cves': [{'key': 'CVE-2025-1', 'component': 'comp-a'}],
            'conditions': [
                {'type': 'Processed', 'status': 'True', 'reason': 'Succeeded', 'message': 'OK'},
                {'type': 'Released', 'status': 'False', 'reason': 'Failed', 'message': 'EC failed'},
            ],
            'pipeline_ref': 'plr-release-123',
            'start_time': '2025-06-01T10:00:00',
            'end_time': '2025-06-01T10:30:00',
            'duration_seconds': 1800,
            'pipeline_ui_url': 'https://console.redhat.com/pipeline/123',
            'is_failed': True,
            'error_details': ['EC validation failed for 3 components'],
            'ec_policy': 'rhoai-stage-policy',
            'ec_policy_url': 'https://policy.example.com',
            'ai_analysis': {
                'root_cause': 'Missing hermetic flag',
                'failure_category': 'ec_violation',
                'recommended_fix': 'Add hermetic=true',
                'confidence_score': 0.9,
            },
        }
        result = runner.invoke(cli, ['describe', 'release', 'release-123'])
        assert result.exit_code == 0
        assert 'snap-123' in result.output
        assert 'RHEA' in result.output
        assert 'CVE-2025-1' in result.output
        assert 'Processed' in result.output
        assert 'plr-release-123' in result.output
        assert 'Missing hermetic flag' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_release_details', return_value=None)
    def test_describe_release_not_found(self, mock_details, mock_cluster, runner):
        result = runner.invoke(cli, ['describe', 'release', 'nonexistent'])
        assert result.exit_code != 0
        assert 'Release not found' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_release_details')
    def test_describe_release_json(self, mock_details, mock_cluster, runner):
        mock_details.return_value = {'snapshot': 'snap-123', 'target': 'prod'}
        result = runner.invoke(cli, ['describe', '--json', 'release', 'rel-1'])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed['snapshot'] == 'snap-123'

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_release_details')
    def test_describe_release_in_progress(self, mock_details, mock_cluster, runner):
        mock_details.return_value = {
            'snapshot': 'snap-123',
            'release_plan': 'rp-prod',
            'type_label': 'push',
            'target': 'staging',
            'created_at': '2025-06-01',
            'is_progressing': True,
            'is_failed': False,
        }
        result = runner.invoke(cli, ['describe', 'release', 'rel-in-progress'])
        assert result.exit_code == 0
        assert 'in progress' in result.output.lower()

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_release_details')
    def test_describe_release_artifact_health(self, mock_details, mock_cluster, runner):
        mock_details.return_value = {
            'snapshot': 'snap-123',
            'release_plan': 'rp-prod',
            'type_label': 'push',
            'target': 'prod',
            'created_at': '2025-06-01',
            'is_failed': False,
            'artifact_health': {
                'comp-a': {
                    'healthy': True, 'missing': [],
                    'sig': {'exists': True}, 'src': {'exists': True},
                    'att': {'exists': True}, 'sbom': {'exists': True},
                },
                'comp-b': {
                    'healthy': False, 'missing': ['sbom'],
                    'sig': {'exists': True}, 'src': {'exists': True},
                    'att': {'exists': True}, 'sbom': {'exists': False},
                },
            },
        }
        result = runner.invoke(cli, ['describe', 'release', 'rel-with-artifacts', '--artifacts'])
        assert result.exit_code == 0
        assert 'Artifact Health' in result.output
        assert 'INCOMPLETE' in result.output


# ===================================================================
#  COMPONENTS GROUP
# ===================================================================

class TestComponentsGroup:
    """Tests for `ic components health`."""

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.sql_table')
    def test_components_health(self, mock_sql, mock_db, runner):
        result = runner.invoke(cli, ['components', 'health'])
        assert result.exit_code == 0
        assert 'Component Health' in result.output
        mock_sql.assert_called_once()

    @patch('cli.db.require_db', return_value=False)
    def test_components_health_no_db(self, mock_db, runner):
        result = runner.invoke(cli, ['components', 'health'])
        assert result.exit_code == 0


# ===================================================================
#  BACKWARD COMPAT ALIASES
# ===================================================================

class TestBackwardCompatAliases:
    """Tests for hidden backward-compat commands."""

    @patch('cli.main._bash_fallback')
    def test_number_shortcut(self, mock_fallback, runner):
        mock_fallback.side_effect = SystemExit(0)
        result = runner.invoke(cli, ['n', '3'])
        mock_fallback.assert_called_with(['3'])

    @patch('cli.main._bash_fallback')
    def test_build_compat(self, mock_fallback, runner):
        mock_fallback.side_effect = SystemExit(0)
        result = runner.invoke(cli, ['build'])
        mock_fallback.assert_called_with(['build'])

    @patch('cli.main._bash_fallback')
    def test_component_compat(self, mock_fallback, runner):
        mock_fallback.side_effect = SystemExit(0)
        result = runner.invoke(cli, ['component'])
        mock_fallback.assert_called_with(['component'])

    @patch('cli.main._bash_fallback')
    def test_scenarios_compat(self, mock_fallback, runner):
        mock_fallback.side_effect = SystemExit(0)
        result = runner.invoke(cli, ['scenarios'])
        mock_fallback.assert_called_with(['conforma', 'scenarios'])

    @patch('cli.main._bash_fallback')
    def test_alerts_compat(self, mock_fallback, runner):
        mock_fallback.side_effect = SystemExit(0)
        result = runner.invoke(cli, ['alerts'])
        mock_fallback.assert_called_with(['alerts'])

    @patch('cli.main._bash_fallback')
    def test_apps_compat(self, mock_fallback, runner):
        mock_fallback.side_effect = SystemExit(0)
        result = runner.invoke(cli, ['apps'])
        mock_fallback.assert_called_with(['get', 'apps'])

    @patch('cli.main._bash_fallback')
    def test_versions_compat(self, mock_fallback, runner):
        mock_fallback.side_effect = SystemExit(0)
        result = runner.invoke(cli, ['versions'])
        mock_fallback.assert_called_with(['get', 'apps'])


# ===================================================================
#  _bash_fallback
# ===================================================================

class TestBashFallback:
    """Tests for _bash_fallback helper."""

    @patch('subprocess.run')
    def test_fallback_success(self, mock_run, runner):
        mock_run.return_value = MagicMock(returncode=0)
        with pytest.raises(SystemExit) as exc_info:
            from cli.main import _bash_fallback
            _bash_fallback(['test', 'arg'])
        assert exc_info.value.code == 0

    @patch('subprocess.run')
    def test_fallback_nonzero(self, mock_run, runner):
        mock_run.return_value = MagicMock(returncode=42)
        with pytest.raises(SystemExit) as exc_info:
            from cli.main import _bash_fallback
            _bash_fallback(['failing'])
        assert exc_info.value.code == 42

    @patch('subprocess.run', side_effect=FileNotFoundError())
    def test_fallback_script_not_found(self, mock_run, runner):
        with pytest.raises(SystemExit) as exc_info:
            from cli.main import _bash_fallback
            _bash_fallback(['missing'])
        assert exc_info.value.code == 1

    @patch('subprocess.run', side_effect=KeyboardInterrupt())
    def test_fallback_keyboard_interrupt(self, mock_run, runner):
        with pytest.raises(SystemExit) as exc_info:
            from cli.main import _bash_fallback
            _bash_fallback(['interrupted'])
        assert exc_info.value.code == 130


# ===================================================================
#  _print_check_line helper
# ===================================================================

class TestPrintCheckLine:
    """Tests for _print_check_line helper."""

    def test_pass_status(self, capsys):
        from cli.main import _print_check_line
        _print_check_line('Test', 'PASS', 'All good', fix='Run fix')
        captured = capsys.readouterr()
        assert 'PASS' in captured.out
        assert 'Test' in captured.out
        # Fix not printed for PASS
        assert 'Fix:' not in captured.out

    def test_fail_status(self, capsys):
        from cli.main import _print_check_line
        _print_check_line('Test', 'FAIL', 'Not working', fix='Fix it')
        captured = capsys.readouterr()
        assert 'FAIL' in captured.out
        assert 'Fix: Fix it' in captured.out

    def test_warn_status(self, capsys):
        from cli.main import _print_check_line
        _print_check_line('Test', 'WARN', 'Risky', fix='Check logs')
        captured = capsys.readouterr()
        assert 'WARN' in captured.out
        assert 'Fix: Check logs' in captured.out

    def test_skip_status(self, capsys):
        from cli.main import _print_check_line
        _print_check_line('Test', 'SKIP', 'Skipped check')
        captured = capsys.readouterr()
        assert 'SKIP' in captured.out

    def test_unknown_status(self, capsys):
        from cli.main import _print_check_line
        _print_check_line('Test', 'UNKNOWN', 'What')
        captured = capsys.readouterr()
        assert '????' in captured.out


# ===================================================================
#  _compute_next_action
# ===================================================================

class TestComputeNextAction:
    """Tests for _compute_next_action helper."""

    def test_complete_with_action_items(self):
        from cli.main import _compute_next_action
        data = {
            'phase': 'complete',
            'report': {
                'action_items': [
                    {'action': 'Review PR #123', 'priority': 'HIGH'},
                ],
            },
        }
        result = _compute_next_action(data)
        assert result is not None
        assert 'Review PR' in result

    def test_complete_no_actions(self):
        from cli.main import _compute_next_action
        data = {
            'phase': 'complete',
            'report': {'action_items': []},
        }
        result = _compute_next_action(data)
        assert result is None

    def test_fix_component(self):
        from cli.main import _compute_next_action
        data = {
            'phase': 'in_progress',
            'analysis': {'fix_component': 'Update Dockerfile'},
        }
        result = _compute_next_action(data)
        assert result is not None
        assert 'Update Dockerfile' in result

    def test_stuck_steps(self):
        from cli.main import _compute_next_action
        data = {
            'phase': 'in_progress',
            'analysis': {},
            'bot_error_analysis': {
                'stuck_steps': {'step-3': 5, 'step-1': 2},
            },
        }
        result = _compute_next_action(data)
        assert result is not None
        assert 'step-3' in result

    def test_automation_in_progress_with_prs(self):
        from cli.main import _compute_next_action
        data = {
            'phase': 'in_progress',
            'analysis': {},
            'bot_error_analysis': {},
            'automation_steps': [
                {'status': 'done', 'label': 'step 1'},
                {'status': 'in_progress', 'label': 'step 2',
                 'pr_links': ['https://github.com/pr/1']},
            ],
        }
        result = _compute_next_action(data)
        assert result is not None
        assert 'Review/merge PR' in result

    def test_automation_pending(self):
        from cli.main import _compute_next_action
        data = {
            'phase': 'in_progress',
            'analysis': {},
            'bot_error_analysis': {},
            'automation_steps': [
                {'status': 'pending', 'label': 'Wait for build',
                 'verification': 'check dashboard'},
            ],
        }
        result = _compute_next_action(data)
        assert result is not None
        assert 'Wait for build' in result

    def test_step_blocked(self):
        from cli.main import _compute_next_action
        data = {
            'phase': 'in_progress',
            'analysis': {},
            'bot_error_analysis': {},
            'automation_steps': [],
            'steps': [
                {'status': 'done', 'label': 'step 1'},
                {'status': 'blocked', 'label': 'step 2', 'fix': 'Unblock it'},
            ],
        }
        result = _compute_next_action(data)
        assert result is not None
        assert 'step 2' in result

    def test_no_action_needed(self):
        from cli.main import _compute_next_action
        data = {
            'phase': 'in_progress',
            'analysis': {},
            'bot_error_analysis': {},
            'automation_steps': [],
            'steps': [
                {'status': 'done', 'label': 'step 1'},
            ],
        }
        result = _compute_next_action(data)
        assert result is None


# ===================================================================
#  SKILLS SUBCOMMANDS: add, remove, update, tag, doctor, output
# ===================================================================

class TestSkillsAdd:
    """Tests for `ic skills add`."""

    @patch('skills.db_registry.get_registry')
    @patch('skills.known_sources.resolve_source', return_value=('test-src', 'https://git.example.com', 'main'))
    @patch('skills.loader.clone_source', return_value=('/tmp/skills/test-src', 'abc123'))
    @patch('skills.loader.discover_skills', return_value=[])
    def test_add_no_skills_found(self, mock_discover, mock_clone, mock_resolve, mock_registry_fn, runner):
        mock_registry = MagicMock()
        mock_registry_fn.return_value = mock_registry
        result = runner.invoke(cli, ['skills', 'add', 'test-src'])
        assert result.exit_code == 0
        assert 'No SKILL.md' in result.output

    @patch('skills.db_registry.get_registry')
    @patch('os.path.isdir', return_value=True)
    @patch('skills.loader.use_local_source', return_value=('/tmp/local', 'abc123'))
    @patch('skills.loader.discover_skills')
    @patch('skills.validator.SkillValidator')
    def test_add_local_dir(self, mock_validator_cls, mock_discover, mock_local, mock_isdir,
                           mock_registry_fn, runner):
        mock_registry = MagicMock()
        mock_registry_fn.return_value = mock_registry

        mock_meta = MagicMock()
        mock_meta.name = 'local-skill'
        mock_meta.description = 'A local skill'
        mock_discover.return_value = [('/tmp/local/skill1', mock_meta)]

        mock_result = MagicMock()
        mock_result.passed = True
        mock_result.warning_count = 0
        mock_validator = MagicMock()
        mock_validator.validate.return_value = mock_result
        mock_validator_cls.return_value = mock_validator

        result = runner.invoke(cli, ['skills', 'add', '/tmp/local'])
        assert result.exit_code == 0
        assert 'Added 1 skill' in result.output


class TestSkillsRemove:
    """Tests for `ic skills remove`."""

    @patch('skills.db_registry.get_registry')
    def test_remove_source(self, mock_registry_fn, runner):
        mock_registry = MagicMock()
        mock_registry.remove_source.return_value = 3
        mock_registry_fn.return_value = mock_registry
        result = runner.invoke(cli, ['skills', 'remove', 'test-src', '--source'])
        assert result.exit_code == 0
        assert 'Removed source' in result.output

    @patch('skills.db_registry.get_registry')
    def test_remove_skill(self, mock_registry_fn, runner):
        mock_skill = MagicMock()
        mock_skill.qualified_name = 'src/my-skill'
        mock_registry = MagicMock()
        mock_registry.get_skill.return_value = mock_skill
        mock_registry_fn.return_value = mock_registry
        result = runner.invoke(cli, ['skills', 'remove', 'my-skill'])
        assert result.exit_code == 0
        assert 'Removed skill' in result.output

    @patch('skills.db_registry.get_registry')
    def test_remove_skill_not_found(self, mock_registry_fn, runner):
        mock_registry = MagicMock()
        mock_registry.get_skill.return_value = None
        mock_registry_fn.return_value = mock_registry
        result = runner.invoke(cli, ['skills', 'remove', 'nonexistent'])
        assert result.exit_code != 0
        assert 'not found' in result.output.lower()

    @patch('skills.db_registry.get_registry')
    def test_remove_skill_key_error(self, mock_registry_fn, runner):
        mock_registry = MagicMock()
        mock_registry.get_skill.side_effect = KeyError('Skill not found: nonexistent')
        mock_registry_fn.return_value = mock_registry
        result = runner.invoke(cli, ['skills', 'remove', 'nonexistent'])
        assert result.exit_code != 0


class TestSkillsUpdate:
    """Tests for `ic skills update`."""

    @patch('skills.db_registry.get_registry')
    @patch('skills.loader.clone_source', return_value=('/tmp/skills/src', 'def456'))
    @patch('skills.loader.discover_skills', return_value=[])
    def test_update_all_sources(self, mock_discover, mock_clone, mock_registry_fn, runner):
        mock_source = MagicMock()
        mock_source.name = 'test-src'
        mock_source.url = 'https://git.example.com'
        mock_source.branch = 'main'
        mock_registry = MagicMock()
        mock_registry.sources = {'test-src': mock_source}
        mock_registry_fn.return_value = mock_registry
        result = runner.invoke(cli, ['skills', 'update'])
        assert result.exit_code == 0

    @patch('skills.db_registry.get_registry')
    def test_update_no_sources(self, mock_registry_fn, runner):
        mock_registry = MagicMock()
        mock_registry.sources = {}
        mock_registry_fn.return_value = mock_registry
        result = runner.invoke(cli, ['skills', 'update'])
        assert result.exit_code == 0
        assert 'No sources registered' in result.output

    @patch('skills.db_registry.get_registry')
    def test_update_source_not_found(self, mock_registry_fn, runner):
        mock_registry = MagicMock()
        mock_registry.sources = {}
        mock_registry_fn.return_value = mock_registry
        result = runner.invoke(cli, ['skills', 'update', 'nonexistent'])
        assert result.exit_code != 0
        assert 'Source not found' in result.output


class TestSkillsTag:
    """Tests for `ic skills tag add/remove`."""

    @patch('skills.db_registry.get_registry')
    def test_tag_add(self, mock_registry_fn, runner):
        mock_registry = MagicMock()
        mock_registry.add_tag.return_value = True
        mock_registry_fn.return_value = mock_registry
        result = runner.invoke(cli, ['skills', 'tag', 'add', 'my-skill', 'fips'])
        assert result.exit_code == 0
        assert 'Added tag' in result.output

    @patch('skills.db_registry.get_registry')
    def test_tag_add_not_found(self, mock_registry_fn, runner):
        mock_registry = MagicMock()
        mock_registry.add_tag.return_value = False
        mock_registry_fn.return_value = mock_registry
        result = runner.invoke(cli, ['skills', 'tag', 'add', 'nonexistent', 'fips'])
        assert result.exit_code != 0
        assert 'not found' in result.output.lower()

    @patch('skills.db_registry.get_registry')
    def test_tag_remove(self, mock_registry_fn, runner):
        mock_registry = MagicMock()
        mock_registry.remove_tag.return_value = True
        mock_registry_fn.return_value = mock_registry
        result = runner.invoke(cli, ['skills', 'tag', 'remove', 'my-skill', 'fips'])
        assert result.exit_code == 0
        assert 'Removed tag' in result.output

    @patch('skills.db_registry.get_registry')
    def test_tag_remove_not_found(self, mock_registry_fn, runner):
        mock_registry = MagicMock()
        mock_registry.remove_tag.return_value = False
        mock_registry_fn.return_value = mock_registry
        result = runner.invoke(cli, ['skills', 'tag', 'remove', 'nonexistent', 'fips'])
        assert result.exit_code != 0


class TestSkillsDoctor:
    """Tests for `ic skills doctor`."""

    @patch('skills.db_registry.get_registry')
    @patch('skills.validator.check_prerequisites')
    def test_doctor_all_ok(self, mock_prereqs, mock_registry_fn, runner):
        mock_skill = MagicMock()
        mock_skill.qualified_name = 'src/my-skill'
        mock_skill.metadata = MagicMock()
        mock_registry = MagicMock()
        mock_registry.list_skills.return_value = [mock_skill]
        mock_registry_fn.return_value = mock_registry
        mock_prereqs.return_value = {
            'status': 'ok', 'tools': {'oc': True}, 'env': {'KUBECONFIG': True},
        }
        result = runner.invoke(cli, ['skills', 'doctor'])
        assert result.exit_code == 0
        assert 'OK' in result.output

    @patch('skills.db_registry.get_registry')
    @patch('skills.validator.check_prerequisites')
    def test_doctor_with_issues(self, mock_prereqs, mock_registry_fn, runner):
        mock_skill = MagicMock()
        mock_skill.qualified_name = 'src/my-skill'
        mock_skill.metadata = MagicMock()
        mock_registry = MagicMock()
        mock_registry.list_skills.return_value = [mock_skill]
        mock_registry_fn.return_value = mock_registry
        mock_prereqs.return_value = {
            'status': 'fail', 'tools': {'oc': False}, 'env': {'KUBECONFIG': False},
        }
        result = runner.invoke(cli, ['skills', 'doctor'])
        assert result.exit_code != 0
        assert 'MISSING' in result.output

    @patch('skills.db_registry.get_registry')
    def test_doctor_no_skills(self, mock_registry_fn, runner):
        mock_registry = MagicMock()
        mock_registry.list_skills.return_value = []
        mock_registry_fn.return_value = mock_registry
        result = runner.invoke(cli, ['skills', 'doctor'])
        assert result.exit_code == 0
        assert 'No skills to check' in result.output

    @patch('os.path.isdir', return_value=True)
    @patch('skills.loader.discover_skills', return_value=[])
    @patch('os.path.isfile', return_value=True)
    @patch('skills.loader.parse_skill_md')
    @patch('skills.validator.check_prerequisites')
    def test_doctor_local_path(self, mock_prereqs, mock_parse, mock_isfile,
                                mock_discover, mock_isdir, runner):
        mock_meta = MagicMock()
        mock_meta.name = 'local-skill'
        mock_parse.return_value = mock_meta
        mock_prereqs.return_value = {
            'status': 'ok', 'tools': {}, 'env': {},
        }
        result = runner.invoke(cli, ['skills', 'doctor', '/tmp/skill-dir'])
        assert result.exit_code == 0


class TestSkillsOutput:
    """Tests for `ic skills output`."""

    @patch('skills.db_registry.get_registry')
    @patch('cli.main._find_skill_outputs', return_value=[])
    def test_output_no_runs(self, mock_outputs, mock_registry_fn, runner):
        mock_skill = MagicMock()
        mock_skill.name = 'my-skill'
        mock_registry = MagicMock()
        mock_registry.get_skill.return_value = mock_skill
        mock_registry_fn.return_value = mock_registry
        result = runner.invoke(cli, ['skills', 'output', 'my-skill'])
        assert result.exit_code != 0
        assert 'No output found' in result.output

    @patch('skills.db_registry.get_registry')
    def test_output_skill_not_found(self, mock_registry_fn, runner):
        mock_registry = MagicMock()
        mock_registry.get_skill.side_effect = KeyError('not found')
        mock_registry_fn.return_value = mock_registry
        result = runner.invoke(cli, ['skills', 'output', 'nonexistent'])
        assert result.exit_code != 0

    @patch('skills.db_registry.get_registry')
    @patch('cli.main._find_skill_outputs')
    def test_output_list_runs(self, mock_outputs, mock_registry_fn, runner):
        mock_skill = MagicMock()
        mock_skill.name = 'my-skill'
        mock_registry = MagicMock()
        mock_registry.get_skill.return_value = mock_skill
        mock_registry_fn.return_value = mock_registry
        mock_outputs.return_value = [
            ('/tmp/work/20250601_100000', '20250601_100000',
             ['report.md', 'data.json']),
        ]
        with patch('os.path.getsize', return_value=1024):
            result = runner.invoke(cli, ['skills', 'output', 'my-skill', '--list'])
        assert result.exit_code == 0
        assert '#1' in result.output

    @patch('skills.db_registry.get_registry')
    @patch('cli.main._find_skill_outputs')
    def test_output_bad_run_index(self, mock_outputs, mock_registry_fn, runner):
        mock_skill = MagicMock()
        mock_skill.name = 'my-skill'
        mock_registry = MagicMock()
        mock_registry.get_skill.return_value = mock_skill
        mock_registry_fn.return_value = mock_registry
        mock_outputs.return_value = [
            ('/tmp/work/20250601_100000', '20250601_100000', ['report.md']),
        ]
        result = runner.invoke(cli, ['skills', 'output', 'my-skill', '--run', '5'])
        assert result.exit_code != 0
        assert 'not found' in result.output.lower()


# ===================================================================
#  ADDITIONAL GET COMMAND PATHS
# ===================================================================

class TestGetAlertsAdditionalPaths:
    """Additional paths in get alerts."""

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.db.check_db', return_value=False)
    @patch('cli.data.get_alerts', return_value={
        'build_failures': [],
        'conforma_violations': [
            {'component': 'comp-a', 'violations_count': 3,
             'scenario': 'enterprise-contract-stage-policy'},
        ],
        'nightly_warnings': [
            {'component_name': 'comp-n', 'message': 'nightly build stale'},
        ],
    })
    @patch('cli.data._triage_info_map_safe', return_value={})
    def test_alerts_with_nightly_warnings(self, mock_triage, mock_alerts, mock_db, mock_cluster, runner):
        result = runner.invoke(cli, ['get', 'alerts'])
        assert result.exit_code == 0
        assert 'nightly' in result.output.lower()

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.db.check_db', return_value=False)
    @patch('cli.data.get_alerts', return_value={
        'build_failures': [],
        'conforma_violations': [
            {'component': 'comp-a', 'violations_count': 3,
             'scenario': 'enterprise-contract-stage-policy',
             'exception_env_tag': 'v2.17',
             'exception_coverage_stage': 'partially_covered',
             'exception_coverage_prod': 'fully_covered',
             'uncovered_rules_stage': ['rule_1'],
             'uncovered_rules_prod': [],
             'policy_url_stage': 'https://stage.example.com',
             'policy_url_prod': 'https://prod.example.com'},
        ],
    })
    @patch('cli.data._triage_info_map_safe', return_value={})
    def test_alerts_with_exception_coverage(self, mock_triage, mock_alerts, mock_db, mock_cluster, runner):
        result = runner.invoke(cli, ['get', 'alerts'])
        assert result.exit_code == 0
        assert 'PARTIAL' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.db.check_db', return_value=False)
    @patch('cli.data.get_alerts', return_value={
        'build_failures': [],
        'conforma_violations': [
            {'component': 'comp-a', 'violations_count': 1,
             'scenario': 'stage-policy'},
        ],
    })
    @patch('cli.data._triage_info_map_safe', return_value={})
    @patch('conforma.policy_tools.extract_policy_from_scenario', return_value='stage')
    @patch('conforma.policy_tools.policy_env', return_value='stage')
    def test_alerts_with_stage_filter(self, mock_env, mock_extract, mock_triage,
                                       mock_alerts, mock_db, mock_cluster, runner):
        result = runner.invoke(cli, ['get', 'alerts', '--stage'])
        assert result.exit_code == 0

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.db.check_db', return_value=False)
    @patch('cli.data.get_alerts', return_value={
        'build_failures': [],
        'conforma_violations': [],
        'release_schedule': {
            'code_freeze': '2025-07-10',
            'code_freeze_days': 5,
            'initial_rc': '2025-07-12',
            'initial_rc_days': 7,
            'release_date': '2025-07-20',
            'release_date_days': 15,
        },
        'last_sync': '2025-06-20T10:00:00',
    })
    @patch('cli.data._triage_info_map_safe', return_value={})
    def test_alerts_with_schedule(self, mock_triage, mock_alerts, mock_db, mock_cluster, runner):
        result = runner.invoke(cli, ['get', 'alerts'])
        assert result.exit_code == 0
        assert 'Last sync' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.db.check_db', return_value=False)
    @patch('cli.data.get_alerts', return_value={
        'build_failures': [
            {'component': 'comp-a', 'error_type': 'compile', 'first_seen': '',
             'has_analysis': True},
        ],
        'conforma_violations': [],
    })
    @patch('cli.data._triage_info_map_safe', return_value={
        'comp-a': {'jira_key': 'RHODS-999', 'group_label': 'Go errors'},
    })
    def test_alerts_with_triage_info(self, mock_triage, mock_alerts, mock_db, mock_cluster, runner):
        result = runner.invoke(cli, ['get', 'alerts'])
        assert result.exit_code == 0
        assert 'RHODS-999' in result.output
        assert 'AI' in result.output


class TestGetConformaAdditionalPaths:
    """Additional paths in get conforma."""

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.db.check_db', return_value=False)
    @patch('cli.data.get_conforma_violations', return_value=[])
    def test_conforma_empty_with_tip(self, mock_violations, mock_db, mock_cluster, runner):
        result = runner.invoke(cli, ['get', 'conforma'])
        assert result.exit_code == 0
        assert 'No conforma violations' in result.output
        assert 'future' in result.output.lower()

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.db.check_db', return_value=False)
    @patch('cli.data.get_conforma_violations', return_value=[])
    def test_conforma_future_no_tip(self, mock_violations, mock_db, mock_cluster, runner):
        result = runner.invoke(cli, ['get', 'conforma', '--future'])
        assert result.exit_code == 0
        assert 'No conforma violations' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.db.check_db', return_value=False)
    @patch('cli.data.get_conforma_violations', return_value={
        'build_failures': [],
    })
    def test_conforma_json_output(self, mock_violations, mock_db, mock_cluster, runner):
        result = runner.invoke(cli, ['get', '--json', 'conforma'])
        assert result.exit_code == 0

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.db.check_db', return_value=False)
    @patch('cli.data.get_conforma_violations', return_value=['comp-a', 'comp-b'])
    def test_conforma_string_list(self, mock_violations, mock_db, mock_cluster, runner):
        result = runner.invoke(cli, ['get', 'conforma'])
        assert result.exit_code == 0
        assert 'comp-a' in result.output


class TestGetPolicyGap:
    """Tests for `ic get policy-gap`."""

    @patch('cli.main._bash_fallback')
    def test_policy_gap_fallback(self, mock_fallback, runner):
        mock_fallback.side_effect = SystemExit(0)
        result = runner.invoke(cli, ['get', 'policy-gap'])
        mock_fallback.assert_called()

    @patch('cli.main._bash_fallback')
    def test_policy_gap_json_fallback(self, mock_fallback, runner):
        mock_fallback.side_effect = SystemExit(0)
        result = runner.invoke(cli, ['get', '--json', 'policy-gap'])
        call_args = mock_fallback.call_args[0][0]
        assert '--json' in call_args


# ===================================================================
#  MAIN ENTRY POINT
# ===================================================================

class TestMainEntryPoint:
    """Tests for main() entry point."""

    @patch('cli.main._bash_fallback')
    def test_number_shortcut_via_main(self, mock_fallback):
        mock_fallback.side_effect = SystemExit(0)
        from cli.main import main
        with patch.object(sys, 'argv', ['ic', '5']):
            with pytest.raises(SystemExit):
                main()
        mock_fallback.assert_called_with(['5'])

    @patch('cli.main.cli')
    def test_pr_alias(self, mock_cli):
        from cli.main import main
        # main() rewrites sys.argv then calls cli()
        saved_argv = sys.argv[:]
        try:
            sys.argv = ['ic', 'pr', 'my-component']
            main()
            # cli() was called; main() rewrote sys.argv before calling it
            mock_cli.assert_called_once()
        finally:
            sys.argv = saved_argv

    @patch('cli.main.cli')
    def test_its_alias(self, mock_cli):
        from cli.main import main
        saved_argv = sys.argv[:]
        try:
            sys.argv = ['ic', 'its']
            main()
            mock_cli.assert_called_once()
        finally:
            sys.argv = saved_argv

    @patch('cli.main.cli', side_effect=SystemExit(0))
    def test_normal_command(self, mock_cli):
        from cli.main import main
        with patch.object(sys, 'argv', ['ic', 'get', 'alerts']):
            with pytest.raises(SystemExit):
                main()

    @patch('cli.main.cli')
    @patch('cli.main._bash_fallback')
    def test_usage_error_fallback(self, mock_fallback, mock_cli):
        import click
        mock_cli.side_effect = click.exceptions.UsageError('bad command')
        mock_fallback.side_effect = SystemExit(0)
        from cli.main import main
        with patch.object(sys, 'argv', ['ic', 'unknown-cmd']):
            with pytest.raises(SystemExit):
                main()
        mock_fallback.assert_called()


# ===================================================================
#  DB GROUP
# ===================================================================

class TestDbGroup:
    """Tests for `ic db status` and `ic db query`."""

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.sql', return_value='PostgreSQL 15.4')
    def test_db_status(self, mock_sql, mock_db, runner):
        result = runner.invoke(cli, ['db', 'status'])
        assert result.exit_code == 0
        assert 'Database connected' in result.output
        assert 'PostgreSQL' in result.output

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.sql', return_value=None)
    def test_db_status_no_version(self, mock_sql, mock_db, runner):
        result = runner.invoke(cli, ['db', 'status'])
        assert result.exit_code == 0
        assert 'Database connected' in result.output

    @patch('cli.db.require_db', return_value=False)
    def test_db_status_no_db(self, mock_db, runner):
        result = runner.invoke(cli, ['db', 'status'])
        assert result.exit_code == 0

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.sql_table')
    def test_db_query(self, mock_sql_table, mock_db, runner):
        result = runner.invoke(cli, ['db', 'query', 'SELECT 1'])
        assert result.exit_code == 0
        mock_sql_table.assert_called_once_with('SELECT 1')


# ===================================================================
#  ADDITIONAL DESCRIBE PATHS
# ===================================================================

class TestDescribeComponentAdditionalPaths:
    """Additional paths in describe component."""

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_failure_details')
    def test_describe_component_with_build_history(self, mock_details, mock_cluster, runner):
        mock_details.return_value = {
            'pipelinerun_name': 'plr-123',
            'component': 'comp-a',
            'repository_url': 'https://github.com/org/repo',
            'branch': 'main',
            'commit_sha': 'abc12345def67890',
            'commit_author': 'dev@example.com',
            'commit_message': 'Fix build',
            'commit_url': 'https://github.com/org/repo/commit/abc123',
            'failed_step': 'build',
            'error_type': 'compile',
            'error_message': 'go: cannot find module',
            'failed_task': 'build-container',
            'jira_key': 'RHODS-100',
            'konflux_url': 'https://konflux.example.com',
            'output_image': 'quay.io/redhat/comp-a:latest',
            'build_history': [
                {'status': 'Succeeded', 'date': '2025-06-02',
                 'commit': 'abc12345'},
                {'status': 'Failed', 'date': '2025-06-01',
                 'commit': 'def67890', 'failed_task_name': 'build'},
            ],
            'ai_analyzed': True,
        }
        result = runner.invoke(cli, ['describe', 'component', 'comp-a'])
        assert result.exit_code == 0
        assert 'Build History' in result.output
        assert 'RESOLVED' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_failure_details')
    @patch('cli.log_filter.filter_error_context')
    def test_describe_component_with_filtered_logs(self, mock_filter, mock_details, mock_cluster, runner):
        mock_details.return_value = {
            'pipelinerun_name': 'plr-123',
            'build_logs': 'line1\nline2\nerror: something\nline3',
        }
        mock_filter.return_value = ('error: something\ncontext', {
            'filtered': True, 'match_count': 1, 'total_lines': 4,
        })
        result = runner.invoke(cli, ['describe', 'component', 'comp-a'])
        assert result.exit_code == 0
        assert 'error: something' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_failure_details')
    def test_describe_component_full_log(self, mock_details, mock_cluster, runner):
        mock_details.return_value = {
            'pipelinerun_name': 'plr-123',
            'build_logs': 'full log content here',
        }
        result = runner.invoke(cli, ['describe', 'component', 'comp-a', '--log'])
        assert result.exit_code == 0
        assert 'Build Logs (Full)' in result.output
        assert 'full log content here' in result.output


class TestDescribeJiraAdditionalPaths:
    """Additional paths in describe jira."""

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_jira_issue', return_value={
        'status': 'In Progress',
        'comments': [
            {'author': {'displayName': 'Dev'}, 'body': 'Working on it'},
        ],
    })
    def test_describe_jira_status_string(self, mock_jira, mock_cluster, runner):
        result = runner.invoke(cli, ['describe', 'jira', 'RHODS-123'])
        assert result.exit_code == 0
        assert 'In Progress' in result.output


# ===================================================================
#  GET VULNERABILITIES
# ===================================================================

class TestGetVulnerabilities:
    """Tests for `ic get vulnerabilities`."""

    @patch('clients.konflux_client.KonfluxClient')
    @patch('clients.registry_client.RegistryClient')
    def test_vulnerabilities_no_snapshots(self, mock_rc_cls, mock_kc_cls, runner):
        mock_kc = MagicMock()
        mock_kc.get_snapshots.return_value = []
        mock_kc_cls.return_value = mock_kc
        result = runner.invoke(cli, ['get', 'vulnerabilities'])
        assert result.exit_code == 0
        assert 'No snapshots found' in result.output

    @patch('clients.konflux_client.KonfluxClient')
    @patch('clients.registry_client.RegistryClient')
    def test_vulnerabilities_with_data(self, mock_rc_cls, mock_kc_cls, runner):
        mock_kc = MagicMock()
        mock_kc.get_snapshots.return_value = [
            {'metadata': {'name': 'snap-1'},
             'spec': {'components': [
                 {'name': 'comp-a'},
                 {'name': 'comp-b'},
             ]}},
        ]
        mock_kc_cls.return_value = mock_kc

        mock_rc = MagicMock()
        mock_rc.fetch_sarif_batch.return_value = {
            'comp-a': [
                {'level': 'error', 'ruleId': 'CVE-2025-001'},
                {'level': 'warning', 'ruleId': 'CVE-2025-002'},
            ],
            'comp-b': [],
        }
        mock_rc_cls.return_value = mock_rc

        result = runner.invoke(cli, ['get', 'vulnerabilities'])
        assert result.exit_code == 0
        assert 'comp-a' in result.output
        assert 'CVE-2025-001' in result.output

    @patch('clients.konflux_client.KonfluxClient')
    @patch('clients.registry_client.RegistryClient')
    def test_vulnerabilities_no_results(self, mock_rc_cls, mock_kc_cls, runner):
        mock_kc = MagicMock()
        mock_kc.get_snapshots.return_value = [
            {'metadata': {'name': 'snap-1'},
             'spec': {'components': [{'name': 'comp-a'}]}},
        ]
        mock_kc_cls.return_value = mock_kc
        mock_rc = MagicMock()
        mock_rc.fetch_sarif_batch.return_value = {'comp-a': []}
        mock_rc_cls.return_value = mock_rc
        result = runner.invoke(cli, ['get', 'vulnerabilities'])
        assert result.exit_code == 0
        assert 'No vulnerabilities found' in result.output


# ===================================================================
#  ADDITIONAL CONFIG PATHS
# ===================================================================

class TestConfigPaths:
    """Additional config paths."""

    @patch('cli.ic_config.load', return_value={'cluster': {}})
    @patch('cli.ic_config.get_mode', return_value='local')
    @patch('cli.db.check_db', return_value=True)
    @patch('cli.db.get_repo')
    def test_config_local_mode_with_db(self, mock_repo_fn, mock_db, mock_mode, mock_load, runner):
        mock_repo = MagicMock()
        mock_repo.get_overview_stats.return_value = {'total': 100, 'components': 20}
        mock_repo_fn.return_value = mock_repo
        result = runner.invoke(cli, ['config'])
        assert result.exit_code == 0
        assert 'local' in result.output

    @patch('cli.ic_config.load', return_value={'cluster': {'api_url': 'https://api.example.com'}})
    @patch('cli.ic_config.get_mode', return_value='cluster')
    @patch('cli.ic_config.get_api_url', return_value='https://api.example.com')
    @patch('cli.ic_config.get_api_key', return_value='secret')
    def test_config_cluster_mode(self, mock_key, mock_url, mock_mode, mock_load, runner):
        result = runner.invoke(cli, ['config'])
        assert result.exit_code == 0
        assert 'cluster' in result.output


# ===================================================================
#  ONBOARD ADDITIONAL PATHS
# ===================================================================

class TestOnboardAdditionalPaths:
    """Additional paths in onboard commands."""

    @patch('cli.data.get_onboarding_status', return_value=None)
    def test_onboard_status_no_data(self, mock_status, runner):
        result = runner.invoke(cli, ['onboard', 'status'])
        assert result.exit_code == 0
        assert 'Could not fetch' in result.output

    @patch('cli.data.get_onboarding_status', return_value={'error': 'Not configured'})
    def test_onboard_status_error(self, mock_status, runner):
        result = runner.invoke(cli, ['onboard', 'status'])
        assert result.exit_code == 0
        assert 'Not configured' in result.output

    @patch('cli.data.get_onboarding_status', return_value={
        'application': 'rhoai-v2-17',
        'total': 3, 'complete': 1, 'partial': 1, 'incomplete': 1,
        'components': [
            {'component': 'comp-a', 'score': 100, 'overall': 'complete',
             'failing': [], 'warnings': [], 'checks': {}},
            {'component': 'comp-b', 'score': 60, 'overall': 'partial',
             'failing': ['repo'], 'warnings': ['labels'],
             'checks': {
                 'repo': {'detail': 'Missing repo', 'fix': 'Add repo'},
                 'labels': {'detail': 'Missing labels', 'fix': 'Add labels'},
             }},
            {'component': 'comp-c', 'score': 0, 'overall': 'incomplete',
             'failing': ['all'], 'warnings': [],
             'checks': {'all': {'detail': 'Nothing configured', 'fix': 'Start onboarding'}}},
        ],
    })
    def test_onboard_status_with_data(self, mock_status, runner):
        result = runner.invoke(cli, ['onboard', 'status'])
        assert result.exit_code == 0
        assert 'comp-b' in result.output
        assert 'comp-c' in result.output
        # comp-a hidden since it's complete and --all not passed
        assert '1 complete components hidden' in result.output

    @patch('cli.data.get_onboarding_status', return_value={
        'application': 'rhoai-v2-17',
        'total': 1, 'complete': 1, 'partial': 0, 'incomplete': 0,
        'components': [
            {'component': 'comp-a', 'score': 100, 'overall': 'complete',
             'failing': [], 'warnings': [], 'checks': {},
             'jira_key': 'RHODS-50', 'jira_status': 'Done'},
        ],
    })
    def test_onboard_status_all(self, mock_status, runner):
        result = runner.invoke(cli, ['onboard', 'status', '--all'])
        assert result.exit_code == 0
        assert 'comp-a' in result.output
        assert 'RHODS-50' in result.output

    @patch('cli.data.get_onboarding_describe', return_value=None)
    def test_onboard_describe_no_data(self, mock_describe, runner):
        result = runner.invoke(cli, ['onboard', 'describe', 'comp-x'])
        assert result.exit_code == 0
        assert 'Could not fetch' in result.output

    @patch('cli.data.get_onboarding_describe', return_value={'error': 'Not found'})
    def test_onboard_describe_error(self, mock_describe, runner):
        result = runner.invoke(cli, ['onboard', 'describe', 'comp-x'])
        assert result.exit_code == 0
        assert 'Not found' in result.output


# ===================================================================
#  _find_skill_outputs and _render_file helpers
# ===================================================================

class TestFindSkillOutputs:
    """Tests for _find_skill_outputs."""

    @patch('skills.models.resolve_working_dir', return_value='/tmp/skill')
    @patch('os.path.isdir', return_value=False)
    def test_no_work_dir(self, mock_isdir, mock_resolve):
        from cli.main import _find_skill_outputs
        skill = MagicMock()
        skill.path = '/tmp/skill'
        skill.metadata.working_dir = None
        result = _find_skill_outputs(skill)
        assert result == []


class TestRenderFile:
    """Tests for _render_file."""

    def test_render_plain_text(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("plain content")
        from cli.main import _render_file
        _render_file(str(f))

    def test_render_json_file(self, tmp_path):
        f = tmp_path / "test.json"
        f.write_text('{"key": "value"}')
        from cli.main import _render_file
        _render_file(str(f))


class TestPrintOutputTip:
    """Tests for _print_output_tip."""

    @patch('cli.main._find_skill_outputs', return_value=[])
    def test_no_tip_no_outputs(self, mock_outputs, capsys):
        from cli.main import _print_output_tip
        skill = MagicMock()
        skill.name = 'test'
        _print_output_tip(skill)
        captured = capsys.readouterr()
        assert 'Tip' not in captured.out

    @patch('cli.main._find_skill_outputs')
    def test_tip_with_md_files(self, mock_outputs, capsys):
        from cli.main import _print_output_tip
        mock_outputs.return_value = [
            ('/tmp/work/run1', '20250601', ['report.md', 'data.json']),
        ]
        skill = MagicMock()
        skill.name = 'test'
        _print_output_tip(skill)
        captured = capsys.readouterr()
        assert 'output' in captured.out.lower()
