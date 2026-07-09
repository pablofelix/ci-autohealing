"""Comprehensive tests for CLI commands in cli/main.py — first half.

Covers: get alerts, get apps, get conforma, get releases, get exceptions,
get bindings, get pipelineruns, get jira, get component, get fixes,
describe component, describe conforma, describe pipelinerun, describe jira,
stats daily, stats (overview), components health, and helper functions.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from cli.main import cli

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def runner():
    return CliRunner()


# All imports in cli/main.py are LOCAL (inside function bodies), so we must
# patch the SOURCE module (e.g. cli.data.get_alerts, cli.mode.ensure_cluster)
# rather than cli.main.<name>.

# ---------------------------------------------------------------------------
# Helper: _format_since
# ---------------------------------------------------------------------------

class TestFormatSince:
    def test_iso_datetime_with_fractional(self):
        from cli.main import _format_since
        result = _format_since('2024-06-30T08:04:12.123456')
        assert '30 Jun' in result
        assert '08:04' in result

    def test_iso_datetime_without_fractional(self):
        from cli.main import _format_since
        result = _format_since('2024-06-30T08:04:12')
        assert '30 Jun' in result

    def test_date_only(self):
        from cli.main import _format_since
        result = _format_since('2024-06-30')
        assert '30 Jun' in result

    def test_empty_string(self):
        from cli.main import _format_since
        assert _format_since('') == ''

    def test_none(self):
        from cli.main import _format_since
        assert _format_since(None) == ''

    def test_short_garbage(self):
        from cli.main import _format_since
        result = _format_since('abc')
        assert result == 'abc'

    def test_long_garbage_truncated(self):
        from cli.main import _format_since
        result = _format_since('x' * 20)
        assert len(result) <= 12

    def test_space_separated_datetime(self):
        from cli.main import _format_since
        result = _format_since('2024-06-30 08:04:12')
        assert '30 Jun' in result


# ---------------------------------------------------------------------------
# Helper: _policy_filter_to_include_future
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

    def test_none(self):
        from cli.main import _policy_filter_to_include_future
        assert _policy_filter_to_include_future(None) is None


# ===================================================================
# get alerts
# ===================================================================

class TestGetAlerts:
    """Tests for `ic get alerts`."""

    @patch('cli.data._triage_info_map_safe', return_value={})
    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.db.check_db', return_value=False)
    @patch('cli.data.get_alerts')
    def test_happy_path_text(self, mock_alerts, mock_db, mock_cluster, mock_triage, runner):
        mock_alerts.return_value = {
            'build_failures': [
                {'component': 'comp-a', 'error_type': 'build-error',
                 'first_seen': '2024-06-30T10:00:00', 'jira_key': 'RHOAI-123'},
            ],
            'conforma_violations': [
                {'component': 'comp-b', 'violations_count': 3, 'warnings_count': 1,
                 'first_seen': '2024-06-29', 'scenario': 'test-scenario'},
            ],
            'nightly_warnings': [],
            'last_sync': '2024-06-30T12:00:00',
        }
        result = runner.invoke(cli, ['get', 'alerts'])
        assert result.exit_code == 0
        assert 'comp-a' in result.output
        assert 'comp-b' in result.output
        assert 'Build Failures' in result.output
        assert 'Conforma Failures' in result.output

    @patch('cli.data._triage_info_map_safe', return_value={})
    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.db.check_db', return_value=False)
    @patch('cli.data.get_alerts')
    def test_json_output(self, mock_alerts, mock_db, mock_cluster, mock_triage, runner):
        data = {'build_failures': [], 'conforma_violations': []}
        mock_alerts.return_value = data
        result = runner.invoke(cli, ['--json', 'get', 'alerts'])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert 'build_failures' in parsed

    @patch('cli.data._triage_info_map_safe', return_value={})
    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.db.check_db', return_value=False)
    @patch('cli.data.get_alerts')
    def test_empty_data(self, mock_alerts, mock_db, mock_cluster, mock_triage, runner):
        mock_alerts.return_value = {
            'build_failures': [],
            'conforma_violations': [],
            'nightly_warnings': [],
        }
        result = runner.invoke(cli, ['get', 'alerts'])
        assert result.exit_code == 0
        assert 'No build failures' in result.output
        assert 'No conforma violations' in result.output

    @patch('cli.data._triage_info_map_safe', return_value={})
    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.db.check_db', return_value=False)
    @patch('cli.data.get_alerts')
    def test_nightly_warnings_displayed(self, mock_alerts, mock_db, mock_cluster, mock_triage, runner):
        mock_alerts.return_value = {
            'build_failures': [],
            'conforma_violations': [],
            'nightly_warnings': [
                {'component_name': 'nightly-comp', 'message': 'stale build'},
            ],
        }
        result = runner.invoke(cli, ['get', 'alerts'])
        assert result.exit_code == 0
        assert 'nightly warning' in result.output
        assert 'nightly-comp' in result.output

    @patch('cli.data._triage_info_map_safe', return_value={})
    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.db.check_db', return_value=False)
    @patch('cli.data.get_alerts')
    def test_release_schedule_display(self, mock_alerts, mock_db, mock_cluster, mock_triage, runner):
        mock_alerts.return_value = {
            'build_failures': [],
            'conforma_violations': [],
            'nightly_warnings': [],
            'release_schedule': {
                'code_freeze': '2024-07-15', 'code_freeze_days': 5,
                'initial_rc': '2024-07-20', 'initial_rc_days': 10,
                'release_date': '2024-08-01', 'release_date_days': 22,
            },
        }
        result = runner.invoke(cli, ['get', 'alerts'])
        assert result.exit_code == 0
        assert 'Release:' in result.output

    @patch('cli.data._triage_info_map_safe', return_value={
        'comp-x': {'jira_key': 'RHOAI-999', 'group_label': 'SA missing'},
    })
    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.db.check_db', return_value=False)
    @patch('cli.data.get_alerts')
    def test_triage_info_enrichment(self, mock_alerts, mock_db, mock_cluster, mock_triage, runner):
        mock_alerts.return_value = {
            'build_failures': [
                {'component': 'comp-x', 'first_seen': '2024-06-30'},
            ],
            'conforma_violations': [
                {'component': 'comp-x', 'violations_count': 1, 'warnings_count': 0,
                 'first_seen': '2024-06-30', 'scenario': ''},
            ],
            'nightly_warnings': [],
        }
        result = runner.invoke(cli, ['get', 'alerts'])
        assert result.exit_code == 0
        assert 'RHOAI-999' in result.output

    @patch('cli.mode.ensure_cluster', return_value=False)
    @patch('cli.db.check_db', return_value=False)
    @patch('cli.main._bash_fallback')
    def test_fallback_to_bash(self, mock_bash, mock_db, mock_cluster, runner):
        result = runner.invoke(cli, ['get', 'alerts'])
        mock_bash.assert_called_once()
        args = mock_bash.call_args[0][0]
        assert 'get' in args
        assert 'alerts' in args

    @patch('cli.data._triage_info_map_safe', return_value={})
    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.db.check_db', return_value=False)
    @patch('cli.data.get_alerts')
    @patch('conforma.policy_tools.policy_env', return_value='stage')
    @patch('conforma.policy_tools.extract_policy_from_scenario', return_value='rhoai-stage-policy')
    def test_stage_filter(self, mock_extract, mock_env, mock_alerts, mock_db, mock_cluster, mock_triage, runner):
        mock_alerts.return_value = {
            'build_failures': [],
            'conforma_violations': [
                {'component': 'comp-a', 'violations_count': 2, 'warnings_count': 0,
                 'first_seen': '2024-06-30', 'scenario': 'rhoai-stage-policy'},
            ],
            'nightly_warnings': [],
        }
        result = runner.invoke(cli, ['get', 'alerts', '--stage'])
        assert result.exit_code == 0

    @patch('cli.data._triage_info_map_safe', return_value={})
    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.db.check_db', return_value=False)
    @patch('cli.data.get_alerts')
    def test_exception_coverage_display(self, mock_alerts, mock_db, mock_cluster, mock_triage, runner):
        mock_alerts.return_value = {
            'build_failures': [],
            'conforma_violations': [
                {'component': 'comp-exc', 'violations_count': 1, 'warnings_count': 0,
                 'first_seen': '2024-06-30', 'scenario': 'some-scenario',
                 'exception_env_tag': 'S+P',
                 'exception_coverage_stage': 'fully_covered',
                 'exception_coverage_prod': 'fully_covered'},
            ],
            'nightly_warnings': [],
        }
        result = runner.invoke(cli, ['get', 'alerts'])
        assert result.exit_code == 0
        assert 'EXC' in result.output or 'comp-exc' in result.output

    @patch('cli.data._triage_info_map_safe', return_value={})
    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.db.check_db', return_value=False)
    @patch('cli.data.get_alerts')
    def test_partial_exception_coverage(self, mock_alerts, mock_db, mock_cluster, mock_triage, runner):
        mock_alerts.return_value = {
            'build_failures': [],
            'conforma_violations': [
                {'component': 'comp-partial', 'violations_count': 1, 'warnings_count': 0,
                 'first_seen': '2024-06-30', 'scenario': 'some-scenario',
                 'exception_env_tag': 'S',
                 'exception_coverage_stage': 'partially_covered',
                 'exception_coverage_prod': 'not_covered',
                 'uncovered_rules_stage': ['rule-1'],
                 'uncovered_rules_prod': ['rule-2']},
            ],
            'nightly_warnings': [],
        }
        result = runner.invoke(cli, ['get', 'alerts'])
        assert result.exit_code == 0
        assert 'PARTIAL' in result.output

    @patch('cli.data._triage_info_map_safe', return_value={})
    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.db.check_db', return_value=False)
    @patch('cli.data.get_alerts')
    def test_last_sync_displayed(self, mock_alerts, mock_db, mock_cluster, mock_triage, runner):
        mock_alerts.return_value = {
            'build_failures': [],
            'conforma_violations': [],
            'nightly_warnings': [],
            'last_sync': '2024-06-30T12:00:00.000000',
        }
        result = runner.invoke(cli, ['get', 'alerts'])
        assert result.exit_code == 0
        assert 'Last sync' in result.output

    @patch('cli.mode.ensure_cluster', return_value=False)
    @patch('cli.db.check_db', return_value=False)
    @patch('cli.main._bash_fallback')
    def test_fallback_with_options(self, mock_bash, mock_db, mock_cluster, runner):
        result = runner.invoke(cli, ['get', 'alerts', '--group', '--all',
                                     '--date', '2024-06-30'])
        mock_bash.assert_called_once()
        args = mock_bash.call_args[0][0]
        assert '--group' in args
        assert '--all' in args
        assert '--date' in args
        assert '2024-06-30' in args


# ===================================================================
# get apps
# ===================================================================

class TestGetApps:
    """Tests for `ic get apps`."""

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_applications')
    def test_happy_path_text(self, mock_apps, mock_cluster, runner):
        mock_apps.return_value = [
            {'name': 'rhoai-v2-16', 'failure_count': 5},
            {'name': 'rhoai-v2-17', 'failure_count': 0},
        ]
        with patch('cli.main.cfg') as mock_cfg:
            mock_cfg.APPLICATION_NAME = 'rhoai-v2-16'
            result = runner.invoke(cli, ['get', 'apps'])
        assert result.exit_code == 0
        assert 'rhoai-v2-16' in result.output
        assert 'rhoai-v2-17' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_applications')
    def test_json_output(self, mock_apps, mock_cluster, runner):
        mock_apps.return_value = [
            {'name': 'app-1', 'failure_count': 3},
        ]
        with patch('cli.main.cfg') as mock_cfg:
            mock_cfg.APPLICATION_NAME = 'app-1'
            result = runner.invoke(cli, ['--json', 'get', 'apps'])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert 'applications' in parsed
        assert parsed['current'] == 'app-1'

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_applications')
    def test_empty_apps(self, mock_apps, mock_cluster, runner):
        mock_apps.return_value = []
        with patch('cli.main.cfg') as mock_cfg:
            mock_cfg.APPLICATION_NAME = 'test'
            result = runner.invoke(cli, ['get', 'apps'])
        assert result.exit_code == 0
        assert 'No applications found' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_applications')
    def test_current_app_highlighted(self, mock_apps, mock_cluster, runner):
        mock_apps.return_value = [
            {'name': 'current-app', 'failure_count': 2},
            {'name': 'other-app', 'failure_count': 0},
        ]
        with patch('cli.main.cfg') as mock_cfg:
            mock_cfg.APPLICATION_NAME = 'current-app'
            result = runner.invoke(cli, ['get', 'apps'])
        assert result.exit_code == 0
        assert 'current' in result.output.lower()

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_applications')
    def test_empty_json(self, mock_apps, mock_cluster, runner):
        mock_apps.return_value = []
        with patch('cli.main.cfg') as mock_cfg:
            mock_cfg.APPLICATION_NAME = 'test'
            result = runner.invoke(cli, ['--json', 'get', 'apps'])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed['applications'] == []


# ===================================================================
# get conforma
# ===================================================================

class TestGetConforma:
    """Tests for `ic get conforma`."""

    @patch('cli.data._triage_jira_map_safe', return_value={})
    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.db.check_db', return_value=False)
    @patch('cli.data.get_conforma_violations')
    def test_happy_path_text(self, mock_violations, mock_db, mock_cluster, mock_jira, runner):
        mock_violations.return_value = [
            {'component': 'comp-a', 'violations_count': 5, 'unique_violations': 3,
             'warnings_count': 1, 'successes_count': 10, 'scenario': 'test-scenario',
             'first_detected_at': '2024-06-30'},
        ]
        result = runner.invoke(cli, ['get', 'conforma'])
        assert result.exit_code == 0
        assert 'comp-a' in result.output
        assert 'Conforma' in result.output

    @patch('cli.data._triage_jira_map_safe', return_value={})
    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.db.check_db', return_value=False)
    @patch('cli.data.get_conforma_violations')
    def test_json_output(self, mock_violations, mock_db, mock_cluster, mock_jira, runner):
        data = [{'component': 'comp-a', 'violations_count': 2}]
        mock_violations.return_value = data
        result = runner.invoke(cli, ['--json', 'get', 'conforma'])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)

    @patch('cli.data._triage_jira_map_safe', return_value={})
    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.db.check_db', return_value=False)
    @patch('cli.data.get_conforma_violations')
    def test_empty_violations(self, mock_violations, mock_db, mock_cluster, mock_jira, runner):
        mock_violations.return_value = []
        result = runner.invoke(cli, ['get', 'conforma'])
        assert result.exit_code == 0
        assert 'No conforma violations' in result.output

    @patch('cli.data._triage_jira_map_safe', return_value={})
    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.db.check_db', return_value=False)
    @patch('cli.data.get_conforma_violations')
    def test_future_policy_filter(self, mock_violations, mock_db, mock_cluster, mock_jira, runner):
        mock_violations.return_value = []
        result = runner.invoke(cli, ['get', 'conforma', '--future'])
        assert result.exit_code == 0
        assert 'Future Policy' in result.output

    @patch('cli.data._triage_jira_map_safe', return_value={})
    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.db.check_db', return_value=False)
    @patch('cli.data.get_conforma_violations')
    def test_all_policy_filter(self, mock_violations, mock_db, mock_cluster, mock_jira, runner):
        mock_violations.return_value = []
        result = runner.invoke(cli, ['get', 'conforma', '--all'])
        assert result.exit_code == 0
        assert 'Current + Future' in result.output

    @patch('cli.mode.ensure_cluster', return_value=False)
    @patch('cli.db.check_db', return_value=False)
    @patch('cli.main._bash_fallback')
    def test_fallback_to_bash(self, mock_bash, mock_db, mock_cluster, runner):
        result = runner.invoke(cli, ['get', 'conforma'])
        mock_bash.assert_called_once()

    @patch('cli.data._triage_jira_map_safe', return_value={'comp-j': 'RHOAI-456'})
    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.db.check_db', return_value=False)
    @patch('cli.data.get_conforma_violations')
    def test_jira_enrichment(self, mock_violations, mock_db, mock_cluster, mock_jira, runner):
        mock_violations.return_value = [
            {'component': 'comp-j', 'violations_count': 1, 'unique_violations': 1,
             'warnings_count': 0, 'successes_count': 5, 'scenario': 'test',
             'first_detected_at': '2024-06-30'},
        ]
        result = runner.invoke(cli, ['get', 'conforma'])
        assert result.exit_code == 0
        assert 'RHOAI-456' in result.output

    @patch('cli.data._triage_jira_map_safe', return_value={})
    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.db.check_db', return_value=False)
    @patch('cli.data.get_conforma_violations')
    def test_string_list_data(self, mock_violations, mock_db, mock_cluster, mock_jira, runner):
        """When data is a list of strings (old format)."""
        mock_violations.return_value = ['comp-a: 3 violations', 'comp-b: 1 violation']
        result = runner.invoke(cli, ['get', 'conforma'])
        assert result.exit_code == 0
        assert 'comp-a' in result.output

    @patch('cli.data._triage_jira_map_safe', return_value={})
    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.db.check_db', return_value=False)
    @patch('cli.data.get_conforma_violations')
    def test_app_option(self, mock_violations, mock_db, mock_cluster, mock_jira, runner):
        mock_violations.return_value = []
        result = runner.invoke(cli, ['get', 'conforma', '--app', 'custom-app'])
        assert result.exit_code == 0
        assert 'custom-app' in result.output

    @patch('cli.data._triage_jira_map_safe', return_value={})
    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.db.check_db', return_value=False)
    @patch('cli.data.get_conforma_violations')
    def test_current_policy_label(self, mock_violations, mock_db, mock_cluster, mock_jira, runner):
        mock_violations.return_value = []
        result = runner.invoke(cli, ['get', 'conforma', '--current'])
        assert result.exit_code == 0
        assert 'Blocking' in result.output or 'Current Policy' in result.output

    @patch('cli.mode.ensure_cluster', return_value=False)
    @patch('cli.db.check_db', return_value=False)
    @patch('cli.main._bash_fallback')
    def test_fallback_with_future_flag(self, mock_bash, mock_db, mock_cluster, runner):
        result = runner.invoke(cli, ['get', 'conforma', '--future'])
        mock_bash.assert_called_once()
        args = mock_bash.call_args[0][0]
        assert '--future' in args


# ===================================================================
# get releases
# ===================================================================

class TestGetReleases:
    """Tests for `ic get releases`."""

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_schedule')
    def test_happy_path(self, mock_schedule, mock_cluster, runner):
        mock_schedule.return_value = {
            'code_freeze': '2024-07-15', 'code_freeze_days': 10,
            'initial_rc': '2024-07-20', 'initial_rc_days': 15,
            'release_date': '2024-08-01', 'release_date_days': 27,
        }
        result = runner.invoke(cli, ['get', 'releases'])
        assert result.exit_code == 0
        assert 'Release Schedule' in result.output
        assert 'code_freeze' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_schedule')
    def test_json_output(self, mock_schedule, mock_cluster, runner):
        data = {'code_freeze': '2024-07-15', 'code_freeze_days': 10}
        mock_schedule.return_value = data
        result = runner.invoke(cli, ['--json', 'get', 'releases'])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert 'code_freeze' in parsed

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_schedule')
    def test_empty_schedule(self, mock_schedule, mock_cluster, runner):
        mock_schedule.return_value = {}
        result = runner.invoke(cli, ['get', 'releases'])
        assert result.exit_code == 0
        assert 'No release schedule' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_schedule')
    def test_none_schedule(self, mock_schedule, mock_cluster, runner):
        mock_schedule.return_value = None
        result = runner.invoke(cli, ['get', 'releases'])
        assert result.exit_code == 0

    @patch('cli.mode.ensure_cluster', return_value=False)
    @patch('cli.main._bash_fallback')
    def test_fallback_with_name(self, mock_bash, mock_cluster, runner):
        result = runner.invoke(cli, ['get', 'releases', 'v2.16'])
        mock_bash.assert_called_once()
        args = mock_bash.call_args[0][0]
        assert 'v2.16' in args

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_schedule')
    def test_close_deadline_color(self, mock_schedule, mock_cluster, runner):
        mock_schedule.return_value = {
            'code_freeze': '2024-07-02', 'code_freeze_days': 1,
        }
        result = runner.invoke(cli, ['get', 'releases'])
        assert result.exit_code == 0
        assert '1d' in result.output

    @patch('cli.mode.ensure_cluster', return_value=False)
    @patch('cli.main._bash_fallback')
    def test_fallback_with_stage(self, mock_bash, mock_cluster, runner):
        result = runner.invoke(cli, ['get', 'releases', '--stage'])
        mock_bash.assert_called_once()
        args = mock_bash.call_args[0][0]
        assert '--stage' in args


# ===================================================================
# get exceptions
# ===================================================================

class TestGetExceptions:
    """Tests for `ic get exceptions`."""

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_policy_exceptions')
    def test_happy_path(self, mock_exceptions, mock_cluster, runner):
        mock_exceptions.return_value = {
            'exceptions': [
                {'value': 'cve-2024-1234', 'source_policy': 'rhoai-stage-policy',
                 'days_left': 15, 'permanent': False},
                {'value': 'rpm-sig-check', 'source_policy': 'rhoai-prod-policy',
                 'days_left': 3, 'permanent': False},
            ]
        }
        result = runner.invoke(cli, ['get', 'exceptions'])
        assert result.exit_code == 0
        assert 'Policy Exceptions' in result.output
        assert 'cve-2024-1234' in result.output
        assert 'rpm-sig-check' in result.output
        assert 'Total: 2 exceptions' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_policy_exceptions')
    def test_json_output(self, mock_exceptions, mock_cluster, runner):
        data = {'exceptions': [{'value': 'test', 'days_left': 5}]}
        mock_exceptions.return_value = data
        result = runner.invoke(cli, ['--json', 'get', 'exceptions'])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert 'exceptions' in parsed

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_policy_exceptions')
    def test_no_exceptions(self, mock_exceptions, mock_cluster, runner):
        mock_exceptions.return_value = {'exceptions': []}
        result = runner.invoke(cli, ['get', 'exceptions'])
        assert result.exit_code == 0
        assert 'No exceptions found' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_policy_exceptions')
    def test_none_data(self, mock_exceptions, mock_cluster, runner):
        mock_exceptions.return_value = None
        result = runner.invoke(cli, ['get', 'exceptions'])
        assert result.exit_code == 0
        assert 'No exceptions found' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_policy_exceptions')
    def test_permanent_exception(self, mock_exceptions, mock_cluster, runner):
        mock_exceptions.return_value = {
            'exceptions': [
                {'value': 'permanent-rule', 'source_policy': 'test-policy',
                 'permanent': True, 'days_left': None},
            ]
        }
        result = runner.invoke(cli, ['get', 'exceptions'])
        assert result.exit_code == 0
        assert 'permanent' in result.output.lower()

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_policy_exceptions')
    def test_expiring_soon(self, mock_exceptions, mock_cluster, runner):
        mock_exceptions.return_value = {
            'exceptions': [
                {'value': 'expiring-rule', 'source_policy': 'test-policy',
                 'permanent': False, 'days_left': 2},
            ]
        }
        result = runner.invoke(cli, ['get', 'exceptions'])
        assert result.exit_code == 0
        assert '2d' in result.output

    @patch('cli.mode.ensure_cluster', return_value=False)
    @patch('cli.main._bash_fallback')
    def test_fallback(self, mock_bash, mock_cluster, runner):
        result = runner.invoke(cli, ['get', 'exceptions'])
        mock_bash.assert_called_once()

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_policy_exceptions')
    def test_long_value_truncated(self, mock_exceptions, mock_cluster, runner):
        mock_exceptions.return_value = {
            'exceptions': [
                {'value': 'x' * 60, 'source_policy': 'test-policy',
                 'permanent': False, 'days_left': 10},
            ]
        }
        result = runner.invoke(cli, ['get', 'exceptions'])
        assert result.exit_code == 0
        assert '...' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_policy_exceptions')
    def test_no_days_left(self, mock_exceptions, mock_cluster, runner):
        mock_exceptions.return_value = {
            'exceptions': [
                {'value': 'some-rule', 'source_policy': 'test-policy',
                 'permanent': False, 'days_left': None},
            ]
        }
        result = runner.invoke(cli, ['get', 'exceptions'])
        assert result.exit_code == 0


# ===================================================================
# get bindings
# ===================================================================

class TestGetBindings:
    """Tests for `ic get bindings`."""

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_policy_bindings')
    def test_happy_path(self, mock_bindings, mock_cluster, runner):
        mock_bindings.return_value = {
            'bindings': [
                {'name': 'rpa-rhoai-stage', 'policy': 'https://example.com/stage-policy'},
                {'name': 'rpa-rhoai-prod', 'policy': 'https://example.com/prod-policy'},
            ]
        }
        result = runner.invoke(cli, ['get', 'bindings'])
        assert result.exit_code == 0
        assert 'rpa-rhoai-stage' in result.output
        assert 'Policy Bindings' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_policy_bindings')
    def test_json_output(self, mock_bindings, mock_cluster, runner):
        data = {'bindings': [{'name': 'test', 'policy': 'pol'}]}
        mock_bindings.return_value = data
        result = runner.invoke(cli, ['--json', 'get', 'bindings'])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert 'bindings' in parsed

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_policy_bindings')
    def test_empty_bindings(self, mock_bindings, mock_cluster, runner):
        mock_bindings.return_value = {'bindings': []}
        result = runner.invoke(cli, ['get', 'bindings'])
        assert result.exit_code == 0
        assert 'No bindings found' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_policy_bindings')
    def test_none_data(self, mock_bindings, mock_cluster, runner):
        mock_bindings.return_value = None
        result = runner.invoke(cli, ['get', 'bindings'])
        assert result.exit_code == 0
        assert 'No bindings found' in result.output

    @patch('cli.mode.ensure_cluster', return_value=False)
    @patch('cli.main._bash_fallback')
    def test_fallback(self, mock_bash, mock_cluster, runner):
        result = runner.invoke(cli, ['get', 'bindings'])
        mock_bash.assert_called_once()


# ===================================================================
# get pipelineruns
# ===================================================================

class TestGetPipelineruns:
    """Tests for `ic get pipelineruns`."""

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_failures')
    def test_happy_path(self, mock_failures, mock_cluster, runner):
        mock_failures.return_value = [
            {'component_name': 'comp-a', 'status': 'Failed',
             'error_type': 'build-error'},
            {'component_name': 'comp-b', 'status': 'Failed',
             'error_message': 'timeout exceeded'},
        ]
        result = runner.invoke(cli, ['get', 'pipelineruns'])
        assert result.exit_code == 0
        assert 'comp-a' in result.output
        assert 'comp-b' in result.output
        assert 'Build Failures' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_failures')
    def test_json_output(self, mock_failures, mock_cluster, runner):
        data = [{'component': 'test', 'status': 'Failed'}]
        mock_failures.return_value = data
        result = runner.invoke(cli, ['--json', 'get', 'pipelineruns'])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_failures')
    def test_empty_data(self, mock_failures, mock_cluster, runner):
        mock_failures.return_value = []
        result = runner.invoke(cli, ['get', 'pipelineruns'])
        assert result.exit_code == 0

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_failures')
    def test_limit_option(self, mock_failures, mock_cluster, runner):
        mock_failures.return_value = [
            {'component_name': 'c{}'.format(i), 'status': 'Failed'}
            for i in range(100)
        ]
        result = runner.invoke(cli, ['get', 'pipelineruns', '--limit', '5'])
        assert result.exit_code == 0

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_failures')
    def test_dict_fallback(self, mock_failures, mock_cluster, runner):
        """When data is a dict instead of a list."""
        mock_failures.return_value = {'error': 'unexpected format'}
        result = runner.invoke(cli, ['get', 'pipelineruns'])
        assert result.exit_code == 0

    @patch('cli.mode.ensure_cluster', return_value=False)
    @patch('cli.main._bash_fallback')
    def test_fallback(self, mock_bash, mock_cluster, runner):
        result = runner.invoke(cli, ['get', 'pipelineruns'])
        mock_bash.assert_called_once()

    @patch('cli.mode.ensure_cluster', return_value=False)
    @patch('cli.main._bash_fallback')
    def test_fallback_with_limit(self, mock_bash, mock_cluster, runner):
        result = runner.invoke(cli, ['get', 'pipelineruns', '--limit', '10'])
        mock_bash.assert_called_once()
        args = mock_bash.call_args[0][0]
        assert '--limit' in args
        assert '10' in args


# ===================================================================
# get pipelinerun (single)
# ===================================================================

class TestGetPipelinerun:
    """Tests for `ic get pipelinerun <name>`."""

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_failure_details')
    def test_happy_path(self, mock_details, mock_cluster, runner):
        mock_details.return_value = {
            'component': 'comp-a', 'status': 'Failed',
            'error_type': 'build-error',
        }
        result = runner.invoke(cli, ['get', 'pipelinerun', 'comp-a'])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed['component'] == 'comp-a'

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_failure_details')
    def test_not_found(self, mock_details, mock_cluster, runner):
        mock_details.return_value = None
        result = runner.invoke(cli, ['get', 'pipelinerun', 'nonexistent'])
        assert result.exit_code == 0
        assert 'not found' in result.output

    def test_missing_name_argument(self, runner):
        result = runner.invoke(cli, ['get', 'pipelinerun'])
        assert result.exit_code != 0

    @patch('cli.mode.ensure_cluster', return_value=False)
    @patch('cli.main._bash_fallback')
    def test_fallback(self, mock_bash, mock_cluster, runner):
        result = runner.invoke(cli, ['get', 'pipelinerun', 'test-name'])
        mock_bash.assert_called_once()
        args = mock_bash.call_args[0][0]
        assert 'test-name' in args


# ===================================================================
# get jira
# ===================================================================

class TestGetJira:
    """Tests for `ic get jira`."""

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_alerts')
    def test_happy_path(self, mock_alerts, mock_cluster, runner):
        mock_alerts.return_value = {
            'build_failures': [
                {'component': 'comp-a', 'jira_key': 'RHOAI-111'},
            ],
            'conforma_violations': [
                {'component': 'comp-b', 'jira_key': 'RHOAI-222'},
            ],
        }
        result = runner.invoke(cli, ['get', 'jira'])
        assert result.exit_code == 0
        assert 'RHOAI-111' in result.output
        assert 'RHOAI-222' in result.output
        assert 'Jira Links' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_alerts')
    def test_json_output(self, mock_alerts, mock_cluster, runner):
        mock_alerts.return_value = {
            'build_failures': [
                {'component': 'comp-a', 'jira_key': 'RHOAI-111'},
            ],
            'conforma_violations': [],
        }
        result = runner.invoke(cli, ['--json', 'get', 'jira'])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)
        assert parsed[0]['jira_key'] == 'RHOAI-111'

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_alerts')
    def test_no_jira_links(self, mock_alerts, mock_cluster, runner):
        mock_alerts.return_value = {
            'build_failures': [{'component': 'comp-a'}],
            'conforma_violations': [],
        }
        result = runner.invoke(cli, ['get', 'jira'])
        assert result.exit_code == 0
        assert 'No Jira tickets linked' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_alerts')
    def test_empty_failures_and_violations(self, mock_alerts, mock_cluster, runner):
        mock_alerts.return_value = {
            'build_failures': [],
            'conforma_violations': [],
        }
        result = runner.invoke(cli, ['get', 'jira'])
        assert result.exit_code == 0
        assert 'No Jira tickets linked' in result.output

    @patch('cli.mode.ensure_cluster', return_value=False)
    @patch('cli.main._bash_fallback')
    def test_fallback(self, mock_bash, mock_cluster, runner):
        result = runner.invoke(cli, ['get', 'jira'])
        mock_bash.assert_called_once()

    @patch('cli.mode.ensure_cluster', return_value=False)
    @patch('cli.main._bash_fallback')
    def test_fallback_with_component(self, mock_bash, mock_cluster, runner):
        result = runner.invoke(cli, ['get', 'jira', 'my-comp'])
        mock_bash.assert_called_once()
        args = mock_bash.call_args[0][0]
        assert 'my-comp' in args


# ===================================================================
# get component (single)
# ===================================================================

class TestGetComponent:
    """Tests for `ic get component <name>`."""

    @patch('cli.data.get_component_summary')
    def test_happy_path(self, mock_summary, runner):
        mock_summary.return_value = {
            'total': 5, 'with_logs': 3,
            'first_seen': '2024-06-25', 'last_seen': '2024-06-30',
        }
        result = runner.invoke(cli, ['get', 'component', 'my-comp'])
        assert result.exit_code == 0
        assert 'my-comp' in result.output
        assert 'Total failures: 5' in result.output
        assert 'With logs: 3' in result.output

    @patch('cli.data.get_component_summary')
    def test_json_output(self, mock_summary, runner):
        mock_summary.return_value = {
            'total': 2, 'with_logs': 1,
            'first_seen': '2024-06-25', 'last_seen': '2024-06-30',
        }
        result = runner.invoke(cli, ['--json', 'get', 'component', 'my-comp'])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed['component'] == 'my-comp'
        assert parsed['total'] == 2

    @patch('cli.data.get_alerts', return_value={'build_failures': [], 'conforma_violations': []})
    @patch('cli.data.get_component_summary')
    def test_not_found(self, mock_summary, mock_alerts, runner):
        mock_summary.return_value = None
        result = runner.invoke(cli, ['get', 'component', 'nonexistent'])
        assert result.exit_code == 1
        assert 'not found' in result.output.lower()

    @patch('cli.data.get_alerts', return_value={'build_failures': [], 'conforma_violations': []})
    @patch('cli.data.get_component_summary')
    def test_not_found_json(self, mock_summary, mock_alerts, runner):
        mock_summary.return_value = None
        result = runner.invoke(cli, ['--json', 'get', 'component', 'bad-name'])
        assert result.exit_code == 1
        parsed = json.loads(result.output)
        assert 'error' in parsed

    def test_missing_name_argument(self, runner):
        result = runner.invoke(cli, ['get', 'component'])
        assert result.exit_code != 0


# ===================================================================
# get fixes
# ===================================================================

class TestGetFixes:
    """Tests for `ic get fixes`."""

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_fixes')
    def test_happy_path(self, mock_fixes, mock_cluster, runner):
        mock_fixes.return_value = [
            {'component_name': 'comp-a', 'status': 'success', 'pr_url': 'https://github.com/pr/1'},
            {'component_name': 'comp-b', 'status': 'failed', 'pr_url': ''},
        ]
        result = runner.invoke(cli, ['get', 'fixes'])
        assert result.exit_code == 0
        assert 'comp-a' in result.output
        assert 'Fix Attempts' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_fixes')
    def test_json_output(self, mock_fixes, mock_cluster, runner):
        data = [{'component_name': 'comp-a', 'status': 'success'}]
        mock_fixes.return_value = data
        result = runner.invoke(cli, ['--json', 'get', 'fixes'])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_fixes')
    def test_empty(self, mock_fixes, mock_cluster, runner):
        mock_fixes.return_value = []
        result = runner.invoke(cli, ['get', 'fixes'])
        assert result.exit_code == 0
        assert 'No fix attempts' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_fixes')
    def test_dict_data_treated_as_empty(self, mock_fixes, mock_cluster, runner):
        mock_fixes.return_value = {'error': 'not a list'}
        result = runner.invoke(cli, ['get', 'fixes'])
        assert result.exit_code == 0
        assert 'No fix attempts' in result.output

    @patch('cli.mode.ensure_cluster', return_value=False)
    @patch('cli.main._bash_fallback')
    def test_fallback(self, mock_bash, mock_cluster, runner):
        result = runner.invoke(cli, ['get', 'fixes'])
        mock_bash.assert_called_once()

    @patch('cli.mode.ensure_cluster', return_value=False)
    @patch('cli.main._bash_fallback')
    def test_fallback_with_all(self, mock_bash, mock_cluster, runner):
        result = runner.invoke(cli, ['get', 'fixes', '--all'])
        mock_bash.assert_called_once()
        args = mock_bash.call_args[0][0]
        assert '--all' in args


# ===================================================================
# describe component
# ===================================================================

class TestDescribeComponent:
    """Tests for `ic describe component <name>`."""

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_failure_details')
    def test_happy_path(self, mock_details, mock_cluster, runner):
        mock_details.return_value = {
            'pipelinerun_name': 'pr-12345',
            'repository_url': 'https://github.com/org/repo',
            'branch': 'main',
            'commit_sha': 'abc12345def67890',
            'commit_author': 'developer',
            'commit_message': 'Fix something important',
            'commit_url': 'https://github.com/org/repo/commit/abc',
            'failed_step': 'build-step',
            'error_type': 'compilation-error',
            'error_message': 'cannot find module',
        }
        result = runner.invoke(cli, ['describe', 'component', 'my-comp'])
        assert result.exit_code == 0
        assert 'my-comp' in result.output
        assert 'pr-12345' in result.output
        assert 'Failed' in result.output
        assert 'build-step' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_failure_details')
    def test_json_output(self, mock_details, mock_cluster, runner):
        data = {'pipelinerun_name': 'pr-123', 'component': 'comp-a'}
        mock_details.return_value = data
        result = runner.invoke(cli, ['--json', 'describe', 'component', 'my-comp'])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed['pipelinerun_name'] == 'pr-123'

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_failure_details')
    def test_not_found(self, mock_details, mock_cluster, runner):
        mock_details.return_value = None
        result = runner.invoke(cli, ['describe', 'component', 'nonexistent'])
        assert result.exit_code == 1
        assert 'not found' in result.output.lower()

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_failure_details')
    def test_with_build_logs(self, mock_details, mock_cluster, runner):
        mock_details.return_value = {
            'pipelinerun_name': 'pr-123',
            'build_logs': 'ERROR: something went wrong\nline2\nline3',
            'failed_step': 'build',
        }
        with patch('cli.log_filter.filter_error_context',
                   return_value=('ERROR: something went wrong', {
                       'filtered': True, 'match_count': 1,
                       'total_lines': 3,
                   })):
            result = runner.invoke(cli, ['describe', 'component', 'my-comp'])
        assert result.exit_code == 0
        assert 'Build Logs' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_failure_details')
    def test_with_full_logs_flag(self, mock_details, mock_cluster, runner):
        mock_details.return_value = {
            'pipelinerun_name': 'pr-123',
            'build_logs': 'Full log content here\nline2\nline3',
        }
        result = runner.invoke(cli, ['describe', 'component', 'my-comp', '--log'])
        assert result.exit_code == 0
        assert 'Full log content' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_failure_details')
    def test_with_build_history(self, mock_details, mock_cluster, runner):
        mock_details.return_value = {
            'pipelinerun_name': 'pr-123',
            'build_history': [
                {'status': 'Succeeded', 'date': '2024-06-30', 'commit': 'abc123'},
                {'status': 'Failed', 'date': '2024-06-29', 'commit': 'def456',
                 'failed_task_name': 'build'},
            ],
        }
        result = runner.invoke(cli, ['describe', 'component', 'my-comp'])
        assert result.exit_code == 0
        assert 'Build History' in result.output
        assert 'RESOLVED' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_failure_details')
    def test_unresolved_build_history(self, mock_details, mock_cluster, runner):
        mock_details.return_value = {
            'pipelinerun_name': 'pr-123',
            'build_history': [
                {'status': 'Failed', 'date': '2024-06-30', 'commit': 'abc123',
                 'failed_task_name': 'build'},
            ],
        }
        result = runner.invoke(cli, ['describe', 'component', 'my-comp'])
        assert result.exit_code == 0
        assert 'Build History' in result.output
        assert 'RESOLVED' not in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_failure_details')
    def test_ai_analysis_available(self, mock_details, mock_cluster, runner):
        mock_details.return_value = {
            'pipelinerun_name': 'pr-123',
            'ai_analyzed': True,
        }
        result = runner.invoke(cli, ['describe', 'component', 'my-comp'])
        assert result.exit_code == 0
        assert 'AI analysis available' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_failure_details')
    def test_ai_analysis_not_available(self, mock_details, mock_cluster, runner):
        mock_details.return_value = {
            'pipelinerun_name': 'pr-123',
            'ai_analyzed': False,
        }
        result = runner.invoke(cli, ['describe', 'component', 'my-comp'])
        assert result.exit_code == 0
        assert 'AI analysis not available' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_failure_details')
    def test_jira_and_konflux_links(self, mock_details, mock_cluster, runner):
        mock_details.return_value = {
            'pipelinerun_name': 'pr-123',
            'jira_key': 'RHOAI-999',
            'konflux_url': 'https://konflux.example.com/pr/123',
            'output_image': 'quay.io/rhoai/comp:latest',
        }
        result = runner.invoke(cli, ['describe', 'component', 'my-comp'])
        assert result.exit_code == 0
        assert 'RHOAI-999' in result.output

    def test_missing_name_argument(self, runner):
        result = runner.invoke(cli, ['describe', 'component'])
        assert result.exit_code != 0

    @patch('cli.mode.ensure_cluster', return_value=False)
    @patch('cli.main._bash_fallback')
    def test_fallback(self, mock_bash, mock_cluster, runner):
        result = runner.invoke(cli, ['describe', 'component', 'my-comp'])
        mock_bash.assert_called_once()
        args = mock_bash.call_args[0][0]
        assert 'my-comp' in args

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_failure_details')
    def test_minimal_data(self, mock_details, mock_cluster, runner):
        """Component with only pipelinerun_name and nothing else."""
        mock_details.return_value = {
            'pipelinerun_name': 'pr-minimal',
        }
        result = runner.invoke(cli, ['describe', 'component', 'minimal-comp'])
        assert result.exit_code == 0
        assert 'pr-minimal' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_failure_details')
    def test_build_logs_no_filter_match(self, mock_details, mock_cluster, runner):
        """Logs with no error matches — still shows something."""
        mock_details.return_value = {
            'pipelinerun_name': 'pr-123',
            'build_logs': 'just some info\nno errors here\n',
            'failed_step': None,
        }
        with patch('cli.log_filter.filter_error_context',
                   return_value=('just some info\nno errors here\n', {
                       'filtered': False, 'match_count': 0,
                       'total_lines': 2,
                   })):
            result = runner.invoke(cli, ['describe', 'component', 'my-comp'])
        assert result.exit_code == 0
        assert 'Build Logs' in result.output


# ===================================================================
# describe conforma
# ===================================================================

class TestDescribeConforma:
    """Tests for `ic describe conforma <name>`."""

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_conforma_details')
    def test_happy_path(self, mock_details, mock_cluster, runner):
        mock_details.return_value = {
            'scenario': 'rhoai-stage-policy',
            'violations_count': 5,
            'warnings_count': 2,
            'successes_count': 100,
            'pipelinerun_name': 'pr-conforma-1',
            'snapshot_name': 'snap-1',
            'repository_url': 'https://github.com/org/repo',
            'commit_sha': 'abc123',
            'first_detected_at': '2024-06-25',
            'last_updated_at': '2024-06-30',
        }
        result = runner.invoke(cli, ['describe', 'conforma', 'comp-a'])
        assert result.exit_code == 0
        assert 'comp-a' in result.output
        assert 'rhoai-stage-policy' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_conforma_details')
    def test_json_output(self, mock_details, mock_cluster, runner):
        data = {'scenario': 'test', 'violations_count': 1}
        mock_details.return_value = data
        result = runner.invoke(cli, ['--json', 'describe', 'conforma', 'comp-a'])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed['scenario'] == 'test'

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_conforma_details')
    def test_not_found(self, mock_details, mock_cluster, runner):
        mock_details.return_value = None
        result = runner.invoke(cli, ['describe', 'conforma', 'nonexistent'])
        assert result.exit_code == 1
        assert 'not found' in result.output.lower()

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_conforma_details')
    def test_with_violation_summary(self, mock_details, mock_cluster, runner):
        mock_details.return_value = {
            'scenario': 'test',
            'violations_count': 2,
            'violation_summary': 'Missing signature on image',
        }
        result = runner.invoke(cli, ['describe', 'conforma', 'comp-a'])
        assert result.exit_code == 0
        assert 'Missing signature' in result.output

    def test_missing_name_argument(self, runner):
        result = runner.invoke(cli, ['describe', 'conforma'])
        assert result.exit_code != 0

    @patch('cli.mode.ensure_cluster', return_value=False)
    @patch('cli.main._bash_fallback')
    def test_fallback(self, mock_bash, mock_cluster, runner):
        result = runner.invoke(cli, ['describe', 'conforma', 'my-comp'])
        mock_bash.assert_called_once()

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_conforma_details')
    def test_displays_all_fields(self, mock_details, mock_cluster, runner):
        mock_details.return_value = {
            'scenario': 'test-policy',
            'violations_count': 3,
            'warnings_count': 1,
            'successes_count': 50,
            'pipelinerun_name': 'pr-test',
            'snapshot_name': 'snap-test',
            'repository_url': 'https://github.com/test',
            'commit_sha': 'aaa111',
            'first_detected_at': '2024-06-20',
            'last_updated_at': '2024-06-30',
            'jira_key': 'RHOAI-888',
            'ai_analyzed': True,
        }
        result = runner.invoke(cli, ['describe', 'conforma', 'comp-full'])
        assert result.exit_code == 0
        assert 'RHOAI-888' in result.output
        assert 'snap-test' in result.output


# ===================================================================
# describe pipelinerun
# ===================================================================

class TestDescribePipelinerun:
    """Tests for `ic describe pipelinerun <name>`."""

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_failure_details')
    def test_happy_path(self, mock_details, mock_cluster, runner):
        mock_details.return_value = {
            'component': 'comp-a',
            'status': 'Failed',
            'error_type': 'timeout',
            'pipelinerun_name': 'pr-12345',
        }
        result = runner.invoke(cli, ['describe', 'pipelinerun', 'pr-12345'])
        assert result.exit_code == 0
        assert 'PipelineRun' in result.output
        assert 'comp-a' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_failure_details')
    def test_json_output(self, mock_details, mock_cluster, runner):
        data = {'component': 'comp-a', 'status': 'Failed'}
        mock_details.return_value = data
        result = runner.invoke(cli, ['--json', 'describe', 'pipelinerun', 'pr-123'])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed['component'] == 'comp-a'

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_failure_details')
    def test_not_found(self, mock_details, mock_cluster, runner):
        mock_details.return_value = None
        result = runner.invoke(cli, ['describe', 'pipelinerun', 'nonexistent'])
        assert result.exit_code == 0
        assert 'null' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_failure_details')
    def test_skips_large_fields(self, mock_details, mock_cluster, runner):
        """build_logs and raw_pipelinerun_yaml should be excluded from text output."""
        mock_details.return_value = {
            'component': 'comp-a',
            'status': 'Failed',
            'build_logs': 'very long log data...',
            'raw_pipelinerun_yaml': 'apiVersion: ...',
            'blob_refs': ['ref1'],
        }
        result = runner.invoke(cli, ['describe', 'pipelinerun', 'pr-123'])
        assert result.exit_code == 0
        assert 'very long log data' not in result.output

    def test_missing_name_argument(self, runner):
        result = runner.invoke(cli, ['describe', 'pipelinerun'])
        assert result.exit_code != 0

    @patch('cli.mode.ensure_cluster', return_value=False)
    @patch('cli.main._bash_fallback')
    def test_fallback(self, mock_bash, mock_cluster, runner):
        result = runner.invoke(cli, ['describe', 'pipelinerun', 'pr-name'])
        mock_bash.assert_called_once()


# ===================================================================
# describe jira
# ===================================================================

class TestDescribeJira:
    """Tests for `ic describe jira <key>`."""

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_jira_issue')
    def test_happy_path(self, mock_jira, mock_cluster, runner):
        mock_jira.return_value = {
            'status': {'key': 'RHOAI-123', 'summary': 'Fix build',
                       'status': 'In Progress', 'assignee': 'dev@example.com'},
            'comments': [
                {'author': {'displayName': 'Bot'}, 'body': 'Auto-comment from CI'},
            ],
        }
        result = runner.invoke(cli, ['describe', 'jira', 'RHOAI-123'])
        assert result.exit_code == 0
        assert 'RHOAI-123' in result.output
        assert 'Bot' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_jira_issue')
    def test_json_output(self, mock_jira, mock_cluster, runner):
        data = {'status': 'Open', 'comments': []}
        mock_jira.return_value = data
        result = runner.invoke(cli, ['--json', 'describe', 'jira', 'RHOAI-123'])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert 'status' in parsed

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_jira_issue')
    def test_not_found(self, mock_jira, mock_cluster, runner):
        mock_jira.return_value = None
        result = runner.invoke(cli, ['describe', 'jira', 'RHOAI-999'])
        assert result.exit_code == 0

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_jira_issue')
    def test_no_comments(self, mock_jira, mock_cluster, runner):
        mock_jira.return_value = {
            'status': {'key': 'RHOAI-123', 'summary': 'Test'},
            'comments': [],
        }
        result = runner.invoke(cli, ['describe', 'jira', 'RHOAI-123'])
        assert result.exit_code == 0

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_jira_issue')
    def test_status_as_string(self, mock_jira, mock_cluster, runner):
        """When status is a plain string instead of dict."""
        mock_jira.return_value = {
            'status': 'In Progress',
            'comments': [],
        }
        result = runner.invoke(cli, ['describe', 'jira', 'RHOAI-123'])
        assert result.exit_code == 0
        assert 'In Progress' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_jira_issue')
    def test_many_comments_shows_last_5(self, mock_jira, mock_cluster, runner):
        mock_jira.return_value = {
            'status': {'key': 'RHOAI-123'},
            'comments': [
                {'author': {'displayName': 'User{}'.format(i)}, 'body': 'Comment {}'.format(i)}
                for i in range(10)
            ],
        }
        result = runner.invoke(cli, ['describe', 'jira', 'RHOAI-123'])
        assert result.exit_code == 0
        assert '10 comment(s)' in result.output
        # Last 5 users shown: User5..User9
        assert 'User9' in result.output

    def test_missing_key_argument(self, runner):
        result = runner.invoke(cli, ['describe', 'jira'])
        assert result.exit_code != 0

    @patch('cli.mode.ensure_cluster', return_value=False)
    @patch('cli.main._bash_fallback')
    def test_fallback(self, mock_bash, mock_cluster, runner):
        result = runner.invoke(cli, ['describe', 'jira', 'RHOAI-123'])
        mock_bash.assert_called_once()


# ===================================================================
# stats daily
# ===================================================================

class TestStatsDaily:
    """Tests for `ic stats daily`."""

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    def test_happy_path(self, mock_get_repo, mock_db, runner):
        mock_repo = MagicMock()
        mock_repo.get_daily_stats.return_value = [
            {'date': '2024-06-28', 'count': 5},
            {'date': '2024-06-29', 'count': 3},
            {'date': '2024-06-30', 'count': 8},
        ]
        mock_get_repo.return_value = mock_repo
        with patch('cli.db.print_table'):
            result = runner.invoke(cli, ['stats', 'daily'])
        assert result.exit_code == 0
        assert 'Failures by Day' in result.output

    @patch('cli.db.require_db', return_value=False)
    def test_no_db(self, mock_db, runner):
        result = runner.invoke(cli, ['stats', 'daily'])
        assert result.exit_code == 0

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    def test_empty_data(self, mock_get_repo, mock_db, runner):
        mock_repo = MagicMock()
        mock_repo.get_daily_stats.return_value = []
        mock_get_repo.return_value = mock_repo
        with patch('cli.db.print_table'):
            result = runner.invoke(cli, ['stats', 'daily'])
        assert result.exit_code == 0


# ===================================================================
# stats (overview)
# ===================================================================

class TestStatsOverview:
    """Tests for `ic stats` (no subcommand, overview)."""

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_overview_stats')
    def test_cluster_mode(self, mock_stats, mock_cluster, runner):
        mock_stats.return_value = {
            'application': 'rhoai-v2-16',
            'total_failures': 15,
            'components_failing': 5,
        }
        result = runner.invoke(cli, ['stats'])
        assert result.exit_code == 0
        assert 'Statistics' in result.output
        assert 'total_failures' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_overview_stats')
    def test_cluster_mode_non_dict(self, mock_stats, mock_cluster, runner):
        mock_stats.return_value = 'unexpected'
        result = runner.invoke(cli, ['stats'])
        assert result.exit_code == 0


# ===================================================================
# components health
# ===================================================================

class TestComponentsHealth:
    """Tests for `ic components health`."""

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.sql_table')
    def test_happy_path(self, mock_sql, mock_db, runner):
        result = runner.invoke(cli, ['components', 'health'])
        assert result.exit_code == 0
        assert 'Component Health' in result.output
        mock_sql.assert_called_once()

    @patch('cli.db.require_db', return_value=False)
    def test_no_db(self, mock_db, runner):
        result = runner.invoke(cli, ['components', 'health'])
        assert result.exit_code == 0


# ===================================================================
# stats resolved
# ===================================================================

class TestStatsResolved:
    """Tests for `ic stats resolved`."""

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    def test_happy_path(self, mock_get_repo, mock_db, runner):
        mock_repo = MagicMock()
        mock_repo.get_resolved_stats.return_value = {
            'total': 50, 'resolved': 30, 'unresolved': 20, 'successes': 25,
        }
        mock_get_repo.return_value = mock_repo
        with patch('cli.db.print_table'):
            result = runner.invoke(cli, ['stats', 'resolved'])
        assert result.exit_code == 0
        assert 'Resolved' in result.output

    @patch('cli.db.require_db', return_value=False)
    def test_no_db(self, mock_db, runner):
        result = runner.invoke(cli, ['stats', 'resolved'])
        assert result.exit_code == 0


# ===================================================================
# CLI group / top-level
# ===================================================================

class TestCliGroup:
    """Tests for CLI group behavior."""

    def test_help_flag(self, runner):
        result = runner.invoke(cli, ['--help'])
        assert result.exit_code == 0
        assert 'Usage' in result.output

    def test_get_help(self, runner):
        result = runner.invoke(cli, ['get', '--help'])
        assert result.exit_code == 0
        assert 'alerts' in result.output
        assert 'conforma' in result.output

    def test_describe_help(self, runner):
        result = runner.invoke(cli, ['describe', '--help'])
        assert result.exit_code == 0
        assert 'component' in result.output
        assert 'conforma' in result.output

    def test_stats_help(self, runner):
        result = runner.invoke(cli, ['stats', '--help'])
        assert result.exit_code == 0
        assert 'daily' in result.output

    def test_components_help(self, runner):
        result = runner.invoke(cli, ['components', '--help'])
        assert result.exit_code == 0
        assert 'health' in result.output

    def test_get_json_flag_propagates(self, runner):
        """JSON flag on get group should propagate to subcommands."""
        with patch('cli.mode.ensure_cluster', return_value=True), \
             patch('cli.data.get_schedule', return_value={'key': 'val'}):
            result = runner.invoke(cli, ['get', '--json', 'releases'])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, dict)

    def test_top_level_json_flag(self, runner):
        """JSON flag on cli group should propagate to get subcommands."""
        with patch('cli.mode.ensure_cluster', return_value=True), \
             patch('cli.data.get_schedule', return_value={'data': 123}):
            result = runner.invoke(cli, ['--json', 'get', 'releases'])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed['data'] == 123


# ===================================================================
# get components
# ===================================================================

class TestGetComponents:
    """Tests for `ic get components`."""

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_failures')
    def test_happy_path(self, mock_failures, mock_cluster, runner):
        mock_failures.return_value = [
            {'component_name': 'comp-a', 'error_type': 'build-error'},
            {'component_name': 'comp-b', 'error_type': ''},
        ]
        result = runner.invoke(cli, ['get', 'components'])
        assert result.exit_code == 0
        assert 'Failing Components' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_failures')
    def test_no_failures(self, mock_failures, mock_cluster, runner):
        mock_failures.return_value = []
        result = runner.invoke(cli, ['get', 'components'])
        assert result.exit_code == 0
        assert 'No failing components' in result.output

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_failures')
    def test_json_output(self, mock_failures, mock_cluster, runner):
        data = [{'component': 'comp-a'}]
        mock_failures.return_value = data
        result = runner.invoke(cli, ['--json', 'get', 'components'])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, list)

    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.data.get_failures')
    def test_dict_data(self, mock_failures, mock_cluster, runner):
        """When data is not a list."""
        mock_failures.return_value = {'error': 'something'}
        result = runner.invoke(cli, ['get', 'components'])
        assert result.exit_code == 0
        assert 'No failing components' in result.output

    @patch('cli.mode.ensure_cluster', return_value=False)
    @patch('cli.main._bash_fallback')
    def test_fallback(self, mock_bash, mock_cluster, runner):
        result = runner.invoke(cli, ['get', 'components'])
        mock_bash.assert_called_once()

    @patch('cli.mode.ensure_cluster', return_value=False)
    @patch('cli.main._bash_fallback')
    def test_fallback_with_refresh(self, mock_bash, mock_cluster, runner):
        result = runner.invoke(cli, ['get', 'components', '--refresh'])
        mock_bash.assert_called_once()
        args = mock_bash.call_args[0][0]
        assert '--refresh' in args


# ===================================================================
# _print_conforma_table (helper)
# ===================================================================

class TestPrintConformaTable:
    """Tests for the _print_conforma_table helper."""

    def test_basic_table(self, runner, capsys):
        from cli.main import _print_conforma_table
        data = [
            {'component': 'comp-a', 'violations_count': 5, 'unique_violations': 3,
             'warnings_count': 1, 'successes_count': 10, 'scenario': 'test-policy',
             'first_detected_at': '2024-06-30'},
            {'component': 'comp-b', 'violations_count': 2, 'unique_violations': 1,
             'warnings_count': 0, 'successes_count': 20, 'scenario': 'test-policy',
             'first_detected_at': '2024-06-29'},
        ]
        _print_conforma_table(data, 'test-app', 'current')
        captured = capsys.readouterr()
        assert 'comp-a' in captured.out
        assert 'comp-b' in captured.out
        assert 'Summary' in captured.out

    def test_empty_table(self, runner, capsys):
        from cli.main import _print_conforma_table
        _print_conforma_table([], 'test-app', 'current')
        captured = capsys.readouterr()
        assert '0 components failing' in captured.out

    def test_exception_coverage_fully(self, runner, capsys):
        from cli.main import _print_conforma_table
        data = [
            {'component': 'comp-a', 'violations_count': 3, 'unique_violations': 2,
             'warnings_count': 0, 'successes_count': 5, 'scenario': 'test',
             'exception_coverage': 'fully_covered'},
        ]
        _print_conforma_table(data, 'test-app', 'current')
        captured = capsys.readouterr()
        assert 'YES' in captured.out or 'covered' in captured.out.lower()

    def test_exception_not_covered(self, runner, capsys):
        from cli.main import _print_conforma_table
        data = [
            {'component': 'comp-a', 'violations_count': 3, 'unique_violations': 2,
             'warnings_count': 0, 'successes_count': 5, 'scenario': 'test',
             'exception_coverage': 'not_covered'},
        ]
        _print_conforma_table(data, 'test-app', 'current')
        captured = capsys.readouterr()
        assert 'NO' in captured.out or 'uncovered' in captured.out.lower()

    def test_long_component_name_truncated(self, runner, capsys):
        from cli.main import _print_conforma_table
        long_name = 'a' * 60
        data = [
            {'component': long_name, 'violations_count': 1, 'unique_violations': 1,
             'warnings_count': 0, 'successes_count': 0, 'scenario': 'test'},
        ]
        _print_conforma_table(data, 'test-app', 'current')
        captured = capsys.readouterr()
        assert '...' in captured.out

    def test_with_jira_key(self, runner, capsys):
        from cli.main import _print_conforma_table
        data = [
            {'component': 'comp-jira', 'violations_count': 1, 'unique_violations': 1,
             'warnings_count': 0, 'successes_count': 0, 'scenario': 'test',
             'jira_key': 'RHOAI-555'},
        ]
        _print_conforma_table(data, 'test-app', 'current')
        captured = capsys.readouterr()
        assert 'RHOAI-555' in captured.out

    def test_exception_env_tag_partial(self, runner, capsys):
        from cli.main import _print_conforma_table
        data = [
            {'component': 'comp-x', 'violations_count': 2, 'unique_violations': 1,
             'warnings_count': 0, 'successes_count': 5, 'scenario': 'test',
             'exception_env_tag': 'S',
             'exception_coverage_stage': 'partially_covered',
             'exception_coverage_prod': 'fully_covered'},
        ]
        _print_conforma_table(data, 'test-app', 'current')
        captured = capsys.readouterr()
        assert 'PARTIAL' in captured.out

    def test_exception_env_tag_full(self, runner, capsys):
        from cli.main import _print_conforma_table
        data = [
            {'component': 'comp-x', 'violations_count': 2, 'unique_violations': 1,
             'warnings_count': 0, 'successes_count': 5, 'scenario': 'test',
             'exception_env_tag': 'S+P',
             'exception_coverage_stage': 'fully_covered',
             'exception_coverage_prod': 'fully_covered'},
        ]
        _print_conforma_table(data, 'test-app', 'current')
        captured = capsys.readouterr()
        assert 'EXC' in captured.out

    def test_rows_sorted_by_violations_desc(self, runner, capsys):
        from cli.main import _print_conforma_table
        data = [
            {'component': 'low-comp', 'violations_count': 1, 'unique_violations': 1,
             'warnings_count': 0, 'successes_count': 0, 'scenario': 'test'},
            {'component': 'high-comp', 'violations_count': 10, 'unique_violations': 5,
             'warnings_count': 0, 'successes_count': 0, 'scenario': 'test'},
        ]
        _print_conforma_table(data, 'test-app', 'current')
        captured = capsys.readouterr()
        high_pos = captured.out.find('high-comp')
        low_pos = captured.out.find('low-comp')
        assert high_pos < low_pos, 'high-comp should appear before low-comp'


# ===================================================================
# Edge cases
# ===================================================================

class TestEdgeCases:
    """Various edge cases across commands."""

    @patch('cli.data._triage_info_map_safe', return_value={})
    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.db.check_db', return_value=False)
    @patch('cli.data.get_alerts')
    def test_alerts_long_component_name(self, mock_alerts, mock_db, mock_cluster, mock_triage, runner):
        long_name = 'x' * 60
        mock_alerts.return_value = {
            'build_failures': [
                {'component': long_name, 'first_seen': '2024-06-30'},
            ],
            'conforma_violations': [],
            'nightly_warnings': [],
        }
        result = runner.invoke(cli, ['get', 'alerts'])
        assert result.exit_code == 0
        assert '...' in result.output

    @patch('cli.data._triage_info_map_safe', return_value={})
    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.db.check_db', return_value=False)
    @patch('cli.data.get_alerts')
    def test_alerts_long_error_type(self, mock_alerts, mock_db, mock_cluster, mock_triage, runner):
        long_err = 'error-' * 10
        mock_alerts.return_value = {
            'build_failures': [
                {'component': 'comp', 'error_type': long_err, 'first_seen': '2024-06-30'},
            ],
            'conforma_violations': [],
            'nightly_warnings': [],
        }
        result = runner.invoke(cli, ['get', 'alerts'])
        assert result.exit_code == 0
        assert '...' in result.output

    @patch('cli.data._triage_info_map_safe', return_value={})
    @patch('cli.mode.ensure_cluster', return_value=True)
    @patch('cli.db.check_db', return_value=False)
    @patch('cli.data.get_alerts')
    def test_alerts_multiple_policies(self, mock_alerts, mock_db, mock_cluster, mock_triage, runner):
        """Multiple policy scenarios should show policy count."""
        mock_alerts.return_value = {
            'build_failures': [],
            'conforma_violations': [
                {'component': 'c1', 'violations_count': 1, 'warnings_count': 0,
                 'first_seen': '2024-06-30', 'scenario': 'rhoai-v2-16-stage-policy-check'},
                {'component': 'c2', 'violations_count': 1, 'warnings_count': 0,
                 'first_seen': '2024-06-30', 'scenario': 'rhoai-v2-16-prod-policy-check'},
            ],
            'nightly_warnings': [],
        }
        result = runner.invoke(cli, ['get', 'alerts'])
        assert result.exit_code == 0
