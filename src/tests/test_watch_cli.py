"""Tests for the ic watch CLI command group."""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from cli.main import cli


class TestWatchGroup:
    def test_watch_no_subcommand_shows_usage(self):
        runner = CliRunner()
        result = runner.invoke(cli, ['watch'])
        assert result.exit_code == 0
        assert 'ic watch start' in result.output

    def test_watch_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ['watch', '--help'])
        assert result.exit_code == 0
        assert 'start' in result.output


class TestWatchStart:
    def test_no_apps_configured_exits_with_error(self):
        runner = CliRunner()
        env = {'WATCH_APPLICATIONS': '', 'APPLICATION_NAME': ''}
        result = runner.invoke(cli, ['watch', 'start'], env=env)
        assert result.exit_code == 1
        assert 'No applications configured' in result.output

    @patch('watcher.daemon.WatchDaemon')
    def test_start_creates_daemon_and_runs(self, mock_daemon_cls):
        mock_daemon = MagicMock()
        mock_daemon_cls.return_value = mock_daemon

        async def noop():
            pass

        mock_daemon.stop.return_value = noop()

        runner = CliRunner()
        env = {'WATCH_APPLICATIONS': 'test-app', 'APPLICATION_NAME': 'test-app',
               'NAMESPACE': 'test-ns'}

        with patch('asyncio.new_event_loop') as mock_loop_fn:
            mock_loop = MagicMock()
            mock_loop.run_until_complete = MagicMock(side_effect=KeyboardInterrupt)
            mock_loop_fn.return_value = mock_loop
            result = runner.invoke(cli, ['watch', 'start'], env=env, catch_exceptions=False)

        assert 'Watch Daemon' in result.output
        assert 'test-app' in result.output
        mock_daemon_cls.assert_called_once()

    @patch('watcher.daemon.WatchDaemon')
    def test_start_shows_config_summary(self, mock_daemon_cls):
        mock_daemon = MagicMock()
        mock_daemon_cls.return_value = mock_daemon

        async def noop():
            pass

        mock_daemon.stop.return_value = noop()

        runner = CliRunner()
        env = {'WATCH_APPLICATIONS': 'app-v1 app-v2', 'APPLICATION_NAME': 'app-v1',
               'NAMESPACE': 'test-ns', 'WATCH_DISABLE': 'jira conforma'}

        with patch('asyncio.new_event_loop') as mock_loop_fn:
            mock_loop = MagicMock()
            mock_loop.run_until_complete = MagicMock(side_effect=KeyboardInterrupt)
            mock_loop_fn.return_value = mock_loop
            result = runner.invoke(cli, ['watch', 'start'], env=env, catch_exceptions=False)

        assert 'app-v1' in result.output
        assert 'app-v2' in result.output
        assert 'jira' in result.output or 'conforma' in result.output
