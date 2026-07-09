import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

"""Tests for cli/commands/triage.py — Click CLI triage management commands."""

import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from cli.commands.triage import (
    _check_jira_statuses,
    _error,
    _tracking_str,
    triage,
)

# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------

class TestTrackingStr:
    """Tests for _tracking_str helper."""

    def test_jira_only(self):
        item = {'jira_key': 'RHOAIENG-1234', 'slack_thread_urls': []}
        assert _tracking_str(item) == 'RHOAIENG-1234'

    def test_single_slack_only(self):
        item = {'slack_thread_urls': ['https://slack.com/t/1']}
        assert _tracking_str(item) == 'Slack'

    def test_multiple_slack(self):
        item = {'slack_thread_urls': ['https://slack.com/t/1', 'https://slack.com/t/2']}
        assert _tracking_str(item) == 'Slack (2)'

    def test_jira_and_slack(self):
        item = {'jira_key': 'RHOAIENG-100', 'slack_thread_urls': ['https://slack.com/t/1']}
        assert _tracking_str(item) == 'RHOAIENG-100, Slack'

    def test_no_tracking(self):
        item = {}
        assert _tracking_str(item) == '—'

    def test_no_tracking_explicit_none(self):
        item = {'jira_key': None, 'slack_thread_urls': None}
        assert _tracking_str(item) == '—'

    def test_empty_slack_list(self):
        item = {'jira_key': 'KEY-1', 'slack_thread_urls': []}
        assert _tracking_str(item) == 'KEY-1'


class TestError:
    """Tests for _error helper."""

    def test_json_output(self, capsys):
        _error('something broke', is_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data == {'error': 'something broke'}

    def test_text_output(self, capsys):
        _error('something broke', is_json=False)
        captured = capsys.readouterr()
        assert 'something broke' in captured.out


class TestCheckJiraStatuses:
    """Tests for _check_jira_statuses helper."""

    @patch('cli.commands.triage._get_jira_client')
    def test_empty_list(self, mock_get_jira):
        result = _check_jira_statuses([])
        assert result == []
        mock_get_jira.assert_not_called()

    @patch('cli.commands.triage._get_jira_client')
    def test_no_jira_client(self, mock_get_jira):
        mock_get_jira.return_value = None
        result = _check_jira_statuses([{'jira_key': 'KEY-1'}])
        assert result == []

    @patch('cli.commands.triage._get_jira_client')
    def test_open_jira_detected(self, mock_get_jira):
        mock_jira = MagicMock()
        mock_jira.get_issue_status.return_value = 'in_progress'
        mock_get_jira.return_value = mock_jira

        items = [{'jira_key': 'KEY-1', 'id': 1}]
        result = _check_jira_statuses(items)
        assert len(result) == 1
        assert result[0][0] == 'KEY-1'

    @patch('cli.commands.triage._get_jira_client')
    def test_done_jira_not_included(self, mock_get_jira):
        mock_jira = MagicMock()
        mock_jira.get_issue_status.return_value = 'done'
        mock_get_jira.return_value = mock_jira

        items = [{'jira_key': 'KEY-1', 'id': 1}]
        result = _check_jira_statuses(items)
        assert result == []

    @patch('cli.commands.triage._get_jira_client')
    def test_exception_silenced(self, mock_get_jira):
        mock_jira = MagicMock()
        mock_jira.get_issue_status.side_effect = Exception('API error')
        mock_get_jira.return_value = mock_jira

        items = [{'jira_key': 'KEY-1', 'id': 1}]
        result = _check_jira_statuses(items)
        assert result == []

    @patch('cli.commands.triage._get_jira_client')
    def test_mixed_statuses(self, mock_get_jira):
        mock_jira = MagicMock()
        mock_jira.get_issue_status.side_effect = ['in_progress', 'done', 'open']
        mock_get_jira.return_value = mock_jira

        items = [
            {'jira_key': 'KEY-1', 'id': 1},
            {'jira_key': 'KEY-2', 'id': 2},
            {'jira_key': 'KEY-3', 'id': 3},
        ]
        result = _check_jira_statuses(items)
        assert len(result) == 2
        keys = [r[0] for r in result]
        assert 'KEY-1' in keys
        assert 'KEY-3' in keys


# ---------------------------------------------------------------------------
# _get_jira_client tests
# ---------------------------------------------------------------------------

class TestGetJiraClient:
    """Tests for _get_jira_client helper."""

    @patch.dict(os.environ, {'JIRA_EMAIL': '', 'JIRA_TOKEN': ''}, clear=False)
    @patch('cli.commands.triage.JiraClient', create=True)
    def test_missing_credentials_returns_none(self, mock_cls):
        from cli.commands.triage import _get_jira_client
        result = _get_jira_client()
        assert result is None

    @patch.dict(os.environ, {
        'JIRA_EMAIL': 'user@example.com',
        'JIRA_TOKEN': 'tok123',
        'JIRA_BASE_URL': 'https://jira.test',
        'JIRA_PROJECT': 'TESTPROJ',
    }, clear=False)
    def test_creates_client_with_env_vars(self):
        with patch('clients.jira_client.JiraClient') as mock_cls:
            from cli.commands.triage import _get_jira_client
            client = _get_jira_client()
            mock_cls.assert_called_once_with(
                'https://jira.test', 'user@example.com', 'tok123', 'TESTPROJ')
            assert client is not None


# ---------------------------------------------------------------------------
# Triage group tests
# ---------------------------------------------------------------------------

class TestTriageGroup:
    """Tests for the triage Click group command."""

    def test_no_subcommand_invokes_show(self):
        """Without subcommand, triage invokes triage_show."""
        runner = CliRunner()
        with patch('cli.ic_config.get_mode', return_value='cluster'):
            with patch('cli.data.require_data', return_value=False):
                result = runner.invoke(triage, [], obj={})
        # Should not crash — exits after require_data returns False
        assert result.exit_code == 0 or result.exit_code is None

    def test_json_flag_sets_context(self):
        """--json flag should propagate to ctx.obj."""
        runner = CliRunner()
        with patch('cli.ic_config.get_mode', return_value='cluster'):
            with patch('cli.data.require_data', return_value=False):
                result = runner.invoke(triage, ['--json', 'show'], obj={})
        assert result.exit_code == 0 or result.exit_code is None


# ---------------------------------------------------------------------------
# triage show tests
# ---------------------------------------------------------------------------

class TestTriageShow:
    """Tests for triage show subcommand."""

    @patch('cli.ic_config.get_mode', return_value='cluster')
    @patch('cli.data.require_data', return_value=False)
    def test_cluster_mode_require_data_fails(self, mock_rd, mock_mode):
        runner = CliRunner()
        result = runner.invoke(triage, ['show'], obj={})
        assert result.exit_code == 0

    @patch('cli.ic_config.get_mode', return_value='cluster')
    @patch('cli.data.require_data', return_value=True)
    @patch('cli.data.get_triage_items')
    def test_cluster_mode_no_items(self, mock_get, mock_rd, mock_mode):
        mock_get.return_value = []
        runner = CliRunner()
        result = runner.invoke(triage, ['show'], obj={})
        assert result.exit_code == 0
        assert 'No triage items' in result.output

    @patch('cli.ic_config.get_mode', return_value='cluster')
    @patch('cli.data.require_data', return_value=True)
    @patch('cli.data.get_triage_items')
    def test_cluster_mode_json_output(self, mock_get, mock_rd, mock_mode):
        mock_get.return_value = [
            {'id': 1, 'status': 'active', 'components': ['comp-a'],
             'group_label': 'test', 'jira_key': None, 'root_cause': None}
        ]
        runner = CliRunner()
        result = runner.invoke(triage, ['--json', 'show'], obj={})
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert data[0]['id'] == 1

    @patch('cli.ic_config.get_mode', return_value='cluster')
    @patch('cli.data.require_data', return_value=True)
    @patch('cli.data.get_triage_items')
    def test_cluster_mode_with_items(self, mock_get, mock_rd, mock_mode):
        mock_get.return_value = [
            {'id': 1, 'status': 'active', 'components': ['comp-a'],
             'group_label': 'Go upgrade', 'jira_key': 'RHOAIENG-100',
             'root_cause': 'Go 1.26 mismatch'},
        ]
        runner = CliRunner()
        result = runner.invoke(triage, ['show'], obj={})
        assert result.exit_code == 0
        assert '#1' in result.output
        assert 'comp-a' in result.output

    @patch('cli.ic_config.get_mode', return_value='cluster')
    @patch('cli.data.require_data', return_value=True)
    @patch('cli.data.get_triage_items')
    def test_cluster_mode_dict_with_items_key(self, mock_get, mock_rd, mock_mode):
        mock_get.return_value = {'items': [
            {'id': 5, 'status': 'resolved', 'components': ['comp-b'],
             'group_label': 'Fixed', 'jira_key': None, 'root_cause': None},
        ]}
        runner = CliRunner()
        result = runner.invoke(triage, ['show'], obj={})
        assert result.exit_code == 0
        assert '#5' in result.output

    @patch('cli.ic_config.get_mode', return_value='local')
    @patch('cli.db.require_db', return_value=False)
    def test_local_mode_db_unavailable(self, mock_rdb, mock_mode):
        runner = CliRunner()
        result = runner.invoke(triage, ['show'], obj={})
        assert result.exit_code == 0

    @patch('cli.ic_config.get_mode', return_value='local')
    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    @patch('cli.commands.triage.cfg')
    @patch('cli.commands.triage._get_jira_client', return_value=None)
    def test_local_mode_with_items_text(self, mock_jira, mock_cfg,
                                        mock_get_repo, mock_rdb, mock_mode):
        mock_cfg.APPLICATION_NAME = 'test-app'
        triage_repo = MagicMock()
        build_repo = MagicMock()

        triage_repo.get_active.return_value = [
            {'id': 1, 'status': 'active', 'components': ['comp-a'],
             'group_label': 'Go issue', 'root_cause': 'Go 1.26',
             'jira_key': 'KEY-1', 'slack_thread_urls': [],
             'notes': None},
        ]
        triage_repo.get_summary.return_value = {
            'active': 1, 'monitoring': 0, 'resolved': 0,
        }
        build_repo.get_triage_summary.return_value = {
            'failing': 3, 'working': 10, 'failing_components': [],
        }

        def repo_dispatch(cls):
            name = cls.__name__
            if name == 'TriageRepository':
                return triage_repo
            return build_repo

        mock_get_repo.side_effect = repo_dispatch

        runner = CliRunner()
        result = runner.invoke(triage, ['show'], obj={})
        assert result.exit_code == 0
        assert 'Triage Dashboard' in result.output
        assert 'comp-a' in result.output

    @patch('cli.ic_config.get_mode', return_value='local')
    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    @patch('cli.commands.triage.cfg')
    @patch('cli.commands.triage._get_jira_client', return_value=None)
    def test_local_mode_json_output(self, mock_jira, mock_cfg,
                                     mock_get_repo, mock_rdb, mock_mode):
        mock_cfg.APPLICATION_NAME = 'test-app'
        triage_repo = MagicMock()
        build_repo = MagicMock()

        triage_repo.get_active.return_value = []
        triage_repo.get_summary.return_value = {
            'active': 0, 'monitoring': 0, 'resolved': 0,
        }
        build_repo.get_triage_summary.return_value = {
            'failing': 0, 'working': 5, 'failing_components': [],
        }

        def repo_dispatch(cls):
            name = cls.__name__
            if name == 'TriageRepository':
                return triage_repo
            return build_repo

        mock_get_repo.side_effect = repo_dispatch

        runner = CliRunner()
        result = runner.invoke(triage, ['--json', 'show'], obj={})
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data['application'] == 'test-app'
        assert 'summary' in data

    @patch('cli.ic_config.get_mode', return_value='local')
    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    @patch('cli.commands.triage.cfg')
    @patch('cli.commands.triage._get_jira_client', return_value=None)
    def test_local_mode_untracked_failures(self, mock_jira, mock_cfg,
                                            mock_get_repo, mock_rdb, mock_mode):
        mock_cfg.APPLICATION_NAME = 'test-app'
        triage_repo = MagicMock()
        build_repo = MagicMock()

        triage_repo.get_active.return_value = []
        triage_repo.get_summary.return_value = {
            'active': 0, 'monitoring': 0, 'resolved': 0,
        }
        build_repo.get_triage_summary.return_value = {
            'failing': 2, 'working': 5,
            'failing_components': [
                {'component': 'orphan-comp'},
                {'component': 'orphan-comp-2'},
            ],
        }

        def repo_dispatch(cls):
            name = cls.__name__
            if name == 'TriageRepository':
                return triage_repo
            return build_repo

        mock_get_repo.side_effect = repo_dispatch

        runner = CliRunner()
        result = runner.invoke(triage, ['show'], obj={})
        assert result.exit_code == 0
        assert 'Untracked Failures' in result.output
        assert 'orphan-comp' in result.output

    @patch('cli.ic_config.get_mode', return_value='local')
    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    @patch('cli.commands.triage.cfg')
    @patch('cli.commands.triage._get_jira_client', return_value=None)
    def test_local_mode_no_items_no_failures(self, mock_jira, mock_cfg,
                                              mock_get_repo, mock_rdb, mock_mode):
        mock_cfg.APPLICATION_NAME = 'test-app'
        triage_repo = MagicMock()
        build_repo = MagicMock()

        triage_repo.get_active.return_value = []
        triage_repo.get_summary.return_value = {
            'active': 0, 'monitoring': 0, 'resolved': 0,
        }
        build_repo.get_triage_summary.return_value = {
            'failing': 0, 'working': 5, 'failing_components': [],
        }

        def repo_dispatch(cls):
            name = cls.__name__
            if name == 'TriageRepository':
                return triage_repo
            return build_repo

        mock_get_repo.side_effect = repo_dispatch

        runner = CliRunner()
        result = runner.invoke(triage, ['show'], obj={})
        assert result.exit_code == 0
        assert 'No active triage items' in result.output


# ---------------------------------------------------------------------------
# triage track tests
# ---------------------------------------------------------------------------

class TestTriageTrack:
    """Tests for triage track subcommand."""

    @patch('cli.db.require_db', return_value=False)
    def test_db_unavailable(self, mock_rdb):
        runner = CliRunner()
        result = runner.invoke(triage, ['track', 'comp-a'], obj={})
        assert result.exit_code == 0

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    @patch('cli.commands.triage.cfg')
    def test_create_new_item(self, mock_cfg, mock_get_repo, mock_rdb):
        mock_cfg.APPLICATION_NAME = 'test-app'
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.find_by_component.return_value = None
        mock_repo.create_item.return_value = 42

        runner = CliRunner()
        result = runner.invoke(
            triage, ['track', 'my-component', '--group', 'Go mismatch'], obj={})
        assert result.exit_code == 0
        assert '42' in result.output
        assert 'Created' in result.output
        mock_repo.create_item.assert_called_once()

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    @patch('cli.commands.triage.cfg')
    def test_create_new_item_json(self, mock_cfg, mock_get_repo, mock_rdb):
        mock_cfg.APPLICATION_NAME = 'test-app'
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.find_by_component.return_value = None
        mock_repo.create_item.return_value = 7

        runner = CliRunner()
        result = runner.invoke(
            triage, ['--json', 'track', 'comp-x'], obj={})
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data['action'] == 'created'
        assert data['item_id'] == 7

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    @patch('cli.commands.triage.cfg')
    def test_duplicate_component_warns(self, mock_cfg, mock_get_repo, mock_rdb):
        mock_cfg.APPLICATION_NAME = 'test-app'
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.find_by_component.return_value = {
            'id': 10, 'group_label': 'Existing group',
        }

        runner = CliRunner()
        result = runner.invoke(triage, ['track', 'dup-comp'], obj={})
        assert result.exit_code == 0
        assert 'Already tracked' in result.output
        assert '#10' in result.output

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    @patch('cli.commands.triage.cfg')
    def test_duplicate_component_json(self, mock_cfg, mock_get_repo, mock_rdb):
        mock_cfg.APPLICATION_NAME = 'test-app'
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.find_by_component.return_value = {
            'id': 10, 'group_label': 'Existing',
        }

        runner = CliRunner()
        result = runner.invoke(triage, ['--json', 'track', 'dup-comp'], obj={})
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data['action'] == 'exists'

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    @patch('cli.commands.triage.cfg')
    def test_add_to_existing_item(self, mock_cfg, mock_get_repo, mock_rdb):
        mock_cfg.APPLICATION_NAME = 'test-app'
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_by_id.return_value = {
            'id': 5, 'components': ['comp-a'],
        }

        runner = CliRunner()
        result = runner.invoke(
            triage, ['track', 'comp-b', '--add-to', '5'], obj={})
        assert result.exit_code == 0
        assert 'Added' in result.output
        mock_repo.update_item.assert_called_once()
        call_kwargs = mock_repo.update_item.call_args
        assert 'comp-b' in call_kwargs[1]['components']

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    @patch('cli.commands.triage.cfg')
    def test_add_to_existing_item_json(self, mock_cfg, mock_get_repo, mock_rdb):
        mock_cfg.APPLICATION_NAME = 'test-app'
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_by_id.return_value = {
            'id': 5, 'components': ['comp-a'],
        }

        runner = CliRunner()
        result = runner.invoke(
            triage, ['--json', 'track', 'comp-b', '--add-to', '5'], obj={})
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data['action'] == 'added'
        assert data['item_id'] == 5

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    @patch('cli.commands.triage.cfg')
    def test_add_to_nonexistent_item(self, mock_cfg, mock_get_repo, mock_rdb):
        mock_cfg.APPLICATION_NAME = 'test-app'
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_by_id.return_value = None

        runner = CliRunner()
        result = runner.invoke(triage, ['track', 'comp-x', '--add-to', '999'], obj={})
        assert result.exit_code == 1
        assert 'not found' in result.output

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    @patch('cli.commands.triage.cfg')
    def test_create_with_all_options(self, mock_cfg, mock_get_repo, mock_rdb):
        mock_cfg.APPLICATION_NAME = 'test-app'
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.find_by_component.return_value = None
        mock_repo.create_item.return_value = 99

        runner = CliRunner()
        result = runner.invoke(triage, [
            'track', 'comp-z',
            '--group', 'FIPS failure',
            '--slack', 'https://slack.com/t/abc',
            '--jira', 'RHOAIENG-555',
            '--step', 'build-images',
            '--cause', 'Missing FIPS module',
            '--notes', 'Needs upstream fix',
        ], obj={})
        assert result.exit_code == 0
        assert '99' in result.output
        assert 'Slack' in result.output
        assert 'Jira' in result.output
        mock_repo.create_item.assert_called_once_with(
            application='test-app',
            components=['comp-z'],
            group_label='FIPS failure',
            root_cause='Missing FIPS module',
            failed_step='build-images',
            slack_thread_urls=['https://slack.com/t/abc'],
            jira_key='RHOAIENG-555',
            notes='Needs upstream fix',
        )

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    @patch('cli.commands.triage.cfg')
    def test_add_to_with_slack_and_jira(self, mock_cfg, mock_get_repo, mock_rdb):
        mock_cfg.APPLICATION_NAME = 'test-app'
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_by_id.return_value = {
            'id': 3, 'components': ['comp-a'],
        }

        runner = CliRunner()
        result = runner.invoke(triage, [
            'track', 'comp-b', '--add-to', '3',
            '--slack', 'https://slack.com/t/x',
            '--jira', 'KEY-99',
            '--notes', 'extra info',
        ], obj={})
        assert result.exit_code == 0
        mock_repo.add_slack_url.assert_called_once_with(3, 'https://slack.com/t/x')
        call_kwargs = mock_repo.update_item.call_args[1]
        assert call_kwargs['jira_key'] == 'KEY-99'
        assert call_kwargs['notes'] == 'extra info'


# ---------------------------------------------------------------------------
# triage update tests
# ---------------------------------------------------------------------------

class TestTriageUpdate:
    """Tests for triage update subcommand."""

    @patch('cli.db.require_db', return_value=False)
    def test_db_unavailable(self, mock_rdb):
        runner = CliRunner()
        result = runner.invoke(triage, ['update', '1', '--notes', 'x'], obj={})
        assert result.exit_code == 0

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    def test_item_not_found(self, mock_get_repo, mock_rdb):
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_by_id.return_value = None

        runner = CliRunner()
        result = runner.invoke(triage, ['update', '999', '--notes', 'x'], obj={})
        assert result.exit_code == 1
        assert 'not found' in result.output

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    def test_no_updates_error(self, mock_get_repo, mock_rdb):
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_by_id.return_value = {'id': 1}

        runner = CliRunner()
        result = runner.invoke(triage, ['update', '1'], obj={})
        assert result.exit_code == 1
        assert 'No updates provided' in result.output

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    def test_update_jira_and_notes(self, mock_get_repo, mock_rdb):
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_by_id.return_value = {'id': 1}

        runner = CliRunner()
        result = runner.invoke(
            triage, ['update', '1', '--jira', 'KEY-100', '--notes', 'Updated notes'], obj={})
        assert result.exit_code == 0
        assert 'Updated' in result.output
        mock_repo.update_item.assert_called_once_with(
            1, jira_key='KEY-100', notes='Updated notes')

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    def test_update_status_to_resolved(self, mock_get_repo, mock_rdb):
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_by_id.return_value = {'id': 2}

        runner = CliRunner()
        result = runner.invoke(
            triage, ['update', '2', '--status', 'resolved'], obj={})
        assert result.exit_code == 0
        assert 'Resolved' in result.output
        mock_repo.resolve_item.assert_called_once_with(2)

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    def test_update_status_resolved_json(self, mock_get_repo, mock_rdb):
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_by_id.return_value = {'id': 2}

        runner = CliRunner()
        result = runner.invoke(
            triage, ['--json', 'update', '2', '--status', 'resolved'], obj={})
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data['action'] == 'resolved'

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    def test_update_slack_url(self, mock_get_repo, mock_rdb):
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_by_id.return_value = {'id': 3}

        runner = CliRunner()
        result = runner.invoke(
            triage, ['update', '3', '--slack', 'https://slack.com/t/new'], obj={})
        assert result.exit_code == 0
        mock_repo.add_slack_url.assert_called_once_with(3, 'https://slack.com/t/new')

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    def test_update_multiple_fields(self, mock_get_repo, mock_rdb):
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_by_id.return_value = {'id': 4}

        runner = CliRunner()
        result = runner.invoke(triage, [
            'update', '4',
            '--cause', 'New root cause',
            '--group', 'New label',
            '--status', 'monitoring',
        ], obj={})
        assert result.exit_code == 0
        mock_repo.update_item.assert_called_once_with(
            4, root_cause='New root cause', group_label='New label', status='monitoring')

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    def test_update_json_output(self, mock_get_repo, mock_rdb):
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_by_id.return_value = {'id': 1}

        runner = CliRunner()
        result = runner.invoke(
            triage, ['--json', 'update', '1', '--jira', 'KEY-1'], obj={})
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data['action'] == 'updated'
        assert 'jira_key' in data['updates']

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    def test_update_only_slack(self, mock_get_repo, mock_rdb):
        """Slack-only update should succeed (no field updates, just slack)."""
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_by_id.return_value = {'id': 5}

        runner = CliRunner()
        result = runner.invoke(
            triage, ['update', '5', '--slack', 'https://slack.com/t/only'], obj={})
        assert result.exit_code == 0
        mock_repo.update_item.assert_not_called()
        mock_repo.add_slack_url.assert_called_once()


# ---------------------------------------------------------------------------
# triage resolve tests
# ---------------------------------------------------------------------------

class TestTriageResolve:
    """Tests for triage resolve subcommand."""

    @patch('cli.db.require_db', return_value=False)
    def test_db_unavailable(self, mock_rdb):
        runner = CliRunner()
        result = runner.invoke(triage, ['resolve', '1'], obj={})
        assert result.exit_code == 0

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    @patch('cli.commands.triage.cfg')
    def test_resolve_by_id(self, mock_cfg, mock_get_repo, mock_rdb):
        mock_cfg.APPLICATION_NAME = 'test-app'
        mock_repo = MagicMock()
        mock_get_repo.side_effect = lambda cls: mock_repo
        mock_repo.get_by_id.return_value = {
            'id': 10, 'components': ['comp-a'], 'group_label': 'Test',
            'jira_key': None, 'status': 'active',
        }

        runner = CliRunner()
        result = runner.invoke(
            triage, ['resolve', '10', '--resolution', 'Fixed upstream'], obj={})
        assert result.exit_code == 0
        assert 'Resolved' in result.output
        assert 'Fixed upstream' in result.output
        mock_repo.resolve_item.assert_called_once_with(
            10, resolution='Fixed upstream', pr_url=None)

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    @patch('cli.commands.triage.cfg')
    def test_resolve_by_component_name(self, mock_cfg, mock_get_repo, mock_rdb):
        mock_cfg.APPLICATION_NAME = 'test-app'
        mock_repo = MagicMock()
        mock_get_repo.side_effect = lambda cls: mock_repo
        mock_repo.find_by_component.return_value = {
            'id': 20, 'components': ['my-comp'], 'group_label': 'Found',
            'jira_key': None,
        }

        runner = CliRunner()
        result = runner.invoke(triage, ['resolve', 'my-comp'], obj={})
        assert result.exit_code == 0
        assert 'Resolved' in result.output
        mock_repo.resolve_item.assert_called_once_with(
            20, resolution=None, pr_url=None)

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    @patch('cli.commands.triage.cfg')
    def test_resolve_by_jira_key(self, mock_cfg, mock_get_repo, mock_rdb):
        mock_cfg.APPLICATION_NAME = 'test-app'
        mock_repo = MagicMock()
        mock_get_repo.side_effect = lambda cls: mock_repo
        mock_repo.find_by_component.return_value = None
        mock_repo.find_by_jira_key.return_value = {
            'id': 30, 'components': ['jira-comp'], 'group_label': 'Jira match',
            'jira_key': 'KEY-999',
        }

        runner = CliRunner()
        result = runner.invoke(
            triage, ['resolve', 'KEY-999', '--no-jira'], obj={})
        assert result.exit_code == 0
        mock_repo.resolve_item.assert_called_once_with(
            30, resolution=None, pr_url=None)

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    @patch('cli.commands.triage.cfg')
    def test_resolve_not_found(self, mock_cfg, mock_get_repo, mock_rdb):
        mock_cfg.APPLICATION_NAME = 'test-app'
        mock_repo = MagicMock()
        mock_get_repo.side_effect = lambda cls: mock_repo
        mock_repo.find_by_component.return_value = None
        mock_repo.find_by_jira_key.return_value = None

        runner = CliRunner()
        result = runner.invoke(triage, ['resolve', 'nonexistent'], obj={})
        assert result.exit_code == 1
        assert 'not found' in result.output

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    @patch('cli.commands.triage.cfg')
    def test_resolve_json_output(self, mock_cfg, mock_get_repo, mock_rdb):
        mock_cfg.APPLICATION_NAME = 'test-app'
        mock_repo = MagicMock()
        mock_get_repo.side_effect = lambda cls: mock_repo
        mock_repo.get_by_id.return_value = {
            'id': 50, 'components': ['comp-j'], 'group_label': 'JSON',
            'jira_key': None,
        }

        runner = CliRunner()
        result = runner.invoke(
            triage, ['--json', 'resolve', '50'], obj={})
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data['action'] == 'resolved'
        assert data['item_id'] == 50

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    @patch('cli.commands.triage.cfg')
    def test_resolve_with_pr_url(self, mock_cfg, mock_get_repo, mock_rdb):
        mock_cfg.APPLICATION_NAME = 'test-app'
        mock_repo = MagicMock()
        mock_get_repo.side_effect = lambda cls: mock_repo
        mock_repo.get_by_id.return_value = {
            'id': 11, 'components': ['comp-pr'], 'group_label': 'PR test',
            'jira_key': None,
        }

        runner = CliRunner()
        result = runner.invoke(
            triage, ['resolve', '11', '--pr', 'https://github.com/pr/123'], obj={})
        assert result.exit_code == 0
        assert 'https://github.com/pr/123' in result.output
        mock_repo.resolve_item.assert_called_once_with(
            11, resolution=None, pr_url='https://github.com/pr/123')

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    @patch('cli.commands.triage.cfg')
    def test_resolve_with_verdict(self, mock_cfg, mock_get_repo, mock_rdb):
        mock_cfg.APPLICATION_NAME = 'test-app'
        mock_repo = MagicMock()
        ai_repo = MagicMock()

        def repo_dispatch(cls):
            name = cls.__name__
            if name == 'AIAnalysisRepository':
                return ai_repo
            return mock_repo

        mock_get_repo.side_effect = repo_dispatch
        mock_repo.get_by_id.return_value = {
            'id': 12, 'components': ['comp-v'], 'group_label': 'Verdict',
            'jira_key': None,
        }

        runner = CliRunner()
        result = runner.invoke(
            triage, ['resolve', '12', '--verdict', 'correct'], obj={})
        assert result.exit_code == 0
        ai_repo.record_verdict.assert_called_once_with(
            'comp-v', 'test-app', 'correct', verdict_by='cli')

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    @patch('cli.commands.triage.cfg')
    def test_resolve_verdict_skip_not_recorded(self, mock_cfg, mock_get_repo, mock_rdb):
        mock_cfg.APPLICATION_NAME = 'test-app'
        mock_repo = MagicMock()
        ai_repo = MagicMock()

        def repo_dispatch(cls):
            name = cls.__name__
            if name == 'AIAnalysisRepository':
                return ai_repo
            return mock_repo

        mock_get_repo.side_effect = repo_dispatch
        mock_repo.get_by_id.return_value = {
            'id': 13, 'components': ['comp-s'], 'group_label': 'Skip',
            'jira_key': None,
        }

        runner = CliRunner()
        result = runner.invoke(
            triage, ['resolve', '13', '--verdict', 'skip'], obj={})
        assert result.exit_code == 0
        ai_repo.record_verdict.assert_not_called()


# ---------------------------------------------------------------------------
# triage report tests
# ---------------------------------------------------------------------------

class TestTriageReport:
    """Tests for triage report subcommand."""

    @patch('cli.db.require_db', return_value=False)
    def test_db_unavailable(self, mock_rdb):
        runner = CliRunner()
        result = runner.invoke(triage, ['report'], obj={})
        assert result.exit_code == 0

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    @patch('cli.commands.triage.cfg')
    def test_report_no_items(self, mock_cfg, mock_get_repo, mock_rdb):
        mock_cfg.APPLICATION_NAME = 'test-app'
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_report.return_value = []
        mock_repo.get_summary.return_value = {
            'active': 0, 'monitoring': 0, 'resolved': 0,
        }

        runner = CliRunner()
        result = runner.invoke(triage, ['report'], obj={})
        assert result.exit_code == 0
        assert 'No triage items found' in result.output

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    @patch('cli.commands.triage.cfg')
    def test_report_with_active_and_resolved(self, mock_cfg, mock_get_repo, mock_rdb):
        mock_cfg.APPLICATION_NAME = 'test-app'
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_report.return_value = [
            {'id': 1, 'status': 'active', 'components': ['comp-a'],
             'group_label': 'Active issue', 'root_cause': 'Broken dep',
             'jira_key': 'KEY-1', 'slack_thread_urls': [],
             'resolution': None, 'resolved_at': None},
            {'id': 2, 'status': 'resolved', 'components': ['comp-b', 'comp-c'],
             'group_label': 'Fixed issue', 'root_cause': 'Config error',
             'jira_key': None, 'slack_thread_urls': [],
             'resolution': 'Updated config', 'resolved_at': '2026-07-01T10:00:00'},
        ]
        mock_repo.get_summary.return_value = {
            'active': 1, 'monitoring': 0, 'resolved': 1,
        }

        runner = CliRunner()
        result = runner.invoke(triage, ['report'], obj={})
        assert result.exit_code == 0
        assert 'Active' in result.output
        assert 'Resolved' in result.output
        assert 'comp-a' in result.output

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    @patch('cli.commands.triage.cfg')
    def test_report_json_output(self, mock_cfg, mock_get_repo, mock_rdb):
        mock_cfg.APPLICATION_NAME = 'test-app'
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_report.return_value = [
            {'id': 1, 'status': 'active', 'components': ['comp-a'],
             'group_label': 'Test', 'root_cause': None,
             'resolution': None, 'resolved_at': None},
        ]
        mock_repo.get_summary.return_value = {
            'active': 1, 'monitoring': 0, 'resolved': 0,
        }

        runner = CliRunner()
        result = runner.invoke(triage, ['--json', 'report'], obj={})
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data['application'] == 'test-app'
        assert len(data['items']) == 1

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    @patch('cli.commands.triage.cfg')
    def test_report_with_date_filter(self, mock_cfg, mock_get_repo, mock_rdb):
        mock_cfg.APPLICATION_NAME = 'test-app'
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_report.return_value = []
        mock_repo.get_summary.return_value = {
            'active': 0, 'monitoring': 0, 'resolved': 0,
        }

        runner = CliRunner()
        result = runner.invoke(
            triage, ['report', '--date', '2026-07-01'], obj={})
        assert result.exit_code == 0
        mock_repo.get_report.assert_called_once_with('test-app', date='2026-07-01')
        assert '2026-07-01' in result.output

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    @patch('cli.commands.triage.cfg')
    def test_report_many_components_truncated(self, mock_cfg, mock_get_repo, mock_rdb):
        mock_cfg.APPLICATION_NAME = 'test-app'
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_report.return_value = [
            {'id': 1, 'status': 'active',
             'components': ['c1', 'c2', 'c3', 'c4', 'c5'],
             'group_label': 'Many comps', 'root_cause': None,
             'jira_key': None, 'slack_thread_urls': [],
             'resolution': None, 'resolved_at': None},
        ]
        mock_repo.get_summary.return_value = {
            'active': 1, 'monitoring': 0, 'resolved': 0,
        }

        runner = CliRunner()
        result = runner.invoke(triage, ['report'], obj={})
        assert result.exit_code == 0
        assert '+2' in result.output

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    @patch('cli.commands.triage.cfg')
    def test_report_json_with_date(self, mock_cfg, mock_get_repo, mock_rdb):
        mock_cfg.APPLICATION_NAME = 'test-app'
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_report.return_value = []
        mock_repo.get_summary.return_value = {
            'active': 0, 'monitoring': 0, 'resolved': 0,
        }

        runner = CliRunner()
        result = runner.invoke(
            triage, ['--json', 'report', '--date', '2026-06-15'], obj={})
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data['date'] == '2026-06-15'


# ---------------------------------------------------------------------------
# triage history tests
# ---------------------------------------------------------------------------

class TestTriageHistory:
    """Tests for triage history subcommand."""

    @patch('cli.db.require_db', return_value=False)
    def test_db_unavailable(self, mock_rdb):
        runner = CliRunner()
        result = runner.invoke(triage, ['history'], obj={})
        assert result.exit_code == 0

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    @patch('cli.commands.triage.cfg')
    def test_history_empty(self, mock_cfg, mock_get_repo, mock_rdb):
        mock_cfg.APPLICATION_NAME = 'test-app'
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_all.return_value = []

        runner = CliRunner()
        result = runner.invoke(triage, ['history'], obj={})
        assert result.exit_code == 0
        assert 'No resolved items' in result.output

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    @patch('cli.commands.triage.cfg')
    def test_history_with_resolved_items(self, mock_cfg, mock_get_repo, mock_rdb):
        mock_cfg.APPLICATION_NAME = 'test-app'
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_all.return_value = [
            {'id': 1, 'status': 'resolved', 'components': ['comp-a'],
             'group_label': 'Fixed', 'root_cause': 'Bad config',
             'resolution': 'Updated yaml', 'resolved_at': '2026-07-01T12:00:00'},
            {'id': 2, 'status': 'active', 'components': ['comp-b'],
             'group_label': 'Still open', 'root_cause': None,
             'resolution': None, 'resolved_at': None},
        ]

        runner = CliRunner()
        result = runner.invoke(triage, ['history'], obj={})
        assert result.exit_code == 0
        assert '1 resolved item' in result.output
        assert 'comp-a' in result.output
        # Active item should not appear in resolved output table
        # (filtered by status == 'resolved')

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    @patch('cli.commands.triage.cfg')
    def test_history_custom_days(self, mock_cfg, mock_get_repo, mock_rdb):
        mock_cfg.APPLICATION_NAME = 'test-app'
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_all.return_value = []

        runner = CliRunner()
        result = runner.invoke(triage, ['history', '--days', '30'], obj={})
        assert result.exit_code == 0
        mock_repo.get_all.assert_called_once_with('test-app', days=30)
        assert '30 days' in result.output

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    @patch('cli.commands.triage.cfg')
    def test_history_json_output(self, mock_cfg, mock_get_repo, mock_rdb):
        mock_cfg.APPLICATION_NAME = 'test-app'
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_all.return_value = [
            {'id': 5, 'status': 'resolved', 'components': ['comp-r'],
             'group_label': 'Done', 'root_cause': 'Fixed',
             'resolution': 'PR merged', 'resolved_at': '2026-06-30'},
        ]

        runner = CliRunner()
        result = runner.invoke(triage, ['--json', 'history'], obj={})
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data['application'] == 'test-app'
        assert data['days'] == 7
        assert len(data['items']) == 1
        assert data['items'][0]['status'] == 'resolved'

    @patch('cli.db.require_db', return_value=True)
    @patch('cli.db.get_repo')
    @patch('cli.commands.triage.cfg')
    def test_history_many_components_truncated(self, mock_cfg, mock_get_repo, mock_rdb):
        mock_cfg.APPLICATION_NAME = 'test-app'
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_all.return_value = [
            {'id': 1, 'status': 'resolved',
             'components': ['a', 'b', 'c', 'd'],
             'group_label': None, 'root_cause': 'Multi-comp fix',
             'resolution': 'Batch fix', 'resolved_at': '2026-07-01'},
        ]

        runner = CliRunner()
        result = runner.invoke(triage, ['history'], obj={})
        assert result.exit_code == 0
        assert '+1' in result.output


# ---------------------------------------------------------------------------
# _print_item_row tests
# ---------------------------------------------------------------------------

class TestPrintItemRow:
    """Tests for _print_item_row helper."""

    def test_active_status(self, capsys):
        from cli.commands.triage import _print_item_row
        item = {
            'id': 1, 'status': 'active', 'components': ['comp-a'],
            'group_label': 'Test group', 'root_cause': 'Something broke',
            'jira_key': 'KEY-1', 'slack_thread_urls': ['https://slack.com/t/1'],
            'notes': 'Some notes here',
        }
        _print_item_row(item)
        captured = capsys.readouterr()
        assert '#1' in captured.out
        assert 'comp-a' in captured.out
        assert 'ACTIVE' in captured.out

    def test_monitoring_status(self, capsys):
        from cli.commands.triage import _print_item_row
        item = {
            'id': 2, 'status': 'monitoring', 'components': ['comp-b'],
            'group_label': None, 'root_cause': None,
            'jira_key': None, 'slack_thread_urls': [],
            'notes': None,
        }
        _print_item_row(item)
        captured = capsys.readouterr()
        assert 'MONITORING' in captured.out
        assert 'comp-b' in captured.out

    def test_resolved_status(self, capsys):
        from cli.commands.triage import _print_item_row
        item = {
            'id': 3, 'status': 'resolved', 'components': ['comp-c', 'comp-d'],
            'group_label': 'Resolved group', 'root_cause': None,
            'jira_key': None, 'slack_thread_urls': [],
            'notes': None,
        }
        _print_item_row(item)
        captured = capsys.readouterr()
        assert 'RESOLVED' in captured.out

    def test_no_group_label_uses_components(self, capsys):
        from cli.commands.triage import _print_item_row
        item = {
            'id': 4, 'status': 'active', 'components': ['first', 'second'],
            'group_label': None, 'root_cause': None,
            'jira_key': None, 'slack_thread_urls': [],
            'notes': None,
        }
        _print_item_row(item)
        captured = capsys.readouterr()
        assert 'first, second' in captured.out

    def test_long_root_cause_truncated(self, capsys):
        from cli.commands.triage import _print_item_row
        long_cause = 'A' * 200
        item = {
            'id': 5, 'status': 'active', 'components': ['comp'],
            'group_label': 'Test', 'root_cause': long_cause,
            'jira_key': None, 'slack_thread_urls': [],
            'notes': None,
        }
        _print_item_row(item)
        captured = capsys.readouterr()
        # Root cause should be truncated to 80 chars
        assert 'A' * 80 in captured.out
        assert 'A' * 81 not in captured.out


# ---------------------------------------------------------------------------
# _build_jira_comment tests
# ---------------------------------------------------------------------------

class TestBuildJiraComment:
    """Tests for _build_jira_comment helper."""

    @patch('cli.db.get_repo')
    @patch('cli.commands.triage.cfg')
    def test_basic_comment_no_resolution(self, mock_cfg, mock_get_repo):
        from cli.commands.triage import _build_jira_comment
        mock_cfg.APPLICATION_NAME = 'test-app'
        mock_build_repo = MagicMock()
        mock_get_repo.return_value = mock_build_repo

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchone.return_value = None
        mock_conn.cursor.return_value = mock_cursor
        mock_build_repo.db.connection.return_value = mock_conn

        item = {'components': ['comp-a'], 'application': 'test-app'}
        result = _build_jira_comment(item, None)
        assert 'Build failure resolved.' in result

    @patch('cli.db.get_repo')
    @patch('cli.commands.triage.cfg')
    def test_comment_with_resolution(self, mock_cfg, mock_get_repo):
        from cli.commands.triage import _build_jira_comment
        mock_cfg.APPLICATION_NAME = 'test-app'
        mock_build_repo = MagicMock()
        mock_get_repo.return_value = mock_build_repo

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchone.return_value = None
        mock_conn.cursor.return_value = mock_cursor
        mock_build_repo.db.connection.return_value = mock_conn

        item = {'components': ['comp-a'], 'application': 'test-app'}
        result = _build_jira_comment(item, 'Updated Go version')
        assert 'Resolution: Updated Go version' in result

    @patch('cli.db.get_repo')
    @patch('cli.commands.triage.cfg')
    def test_comment_db_exception_silenced(self, mock_cfg, mock_get_repo):
        from cli.commands.triage import _build_jira_comment
        mock_cfg.APPLICATION_NAME = 'test-app'
        mock_build_repo = MagicMock()
        mock_get_repo.return_value = mock_build_repo
        mock_build_repo.db.connection.side_effect = Exception('DB error')

        item = {'components': ['comp-a'], 'application': 'test-app'}
        result = _build_jira_comment(item, 'Fixed')
        # Should not crash, just return basic comment
        assert 'Build failure resolved.' in result


# ---------------------------------------------------------------------------
# _find_resolution_build tests
# ---------------------------------------------------------------------------

class TestFindResolutionBuild:
    """Tests for _find_resolution_build helper."""

    @patch('clients.pipelinerun_query.query_pipelineruns')
    @patch('cli.commands.triage.cfg')
    def test_no_pipelineruns(self, mock_cfg, mock_query):
        from cli.commands.triage import _find_resolution_build
        mock_cfg.NAMESPACE = 'test-ns'
        mock_query.return_value = []

        sha, name = _find_resolution_build('comp-a', '2026-07-01T00:00:00')
        assert sha is None
        assert name is None

    @patch('clients.pipelinerun_query.query_pipelineruns')
    @patch('cli.commands.triage.cfg')
    def test_finds_successful_push_build(self, mock_cfg, mock_query):
        from cli.commands.triage import _find_resolution_build
        mock_cfg.NAMESPACE = 'test-ns'
        mock_query.return_value = [
            {
                'metadata': {
                    'name': 'build-123',
                    'creationTimestamp': '2026-07-02T10:00:00Z',
                    'labels': {
                        'pipelinesascode.tekton.dev/event-type': 'push',
                    },
                    'annotations': {
                        'build.appstudio.redhat.com/commit_sha': 'abc123def',
                    },
                },
                'status': {
                    'conditions': [{'status': 'True'}],
                },
            },
        ]

        sha, name = _find_resolution_build('comp-a', '2026-07-01T00:00:00')
        assert sha == 'abc123def'
        assert name == 'build-123'

    @patch('clients.pipelinerun_query.query_pipelineruns')
    @patch('cli.commands.triage.cfg')
    def test_exception_returns_none(self, mock_cfg, mock_query):
        from cli.commands.triage import _find_resolution_build
        mock_cfg.NAMESPACE = 'test-ns'
        mock_query.side_effect = Exception('API timeout')

        sha, name = _find_resolution_build('comp-a', '2026-07-01T00:00:00')
        assert sha is None
        assert name is None
