"""Tests for Phase 2 watch daemon additions.

Covers: CLI config watch commands, WatcherConfig disable/enable,
daemon conditional task creation, and Jira polling loop.
"""

import asyncio
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from cli.main import cli
from config import ALL_WATCHERS, CollectorConfig, DatabaseConfig, KubernetesConfig, WatcherConfig
from watcher.daemon import WatchDaemon


def _make_config(apps=('test-app',), disabled=frozenset()):
    return CollectorConfig(
        db=DatabaseConfig(
            host='localhost', port=5432, user='postgres',
            password='test', database='testdb',
        ),
        k8s=KubernetesConfig(
            namespace='test-ns',
            application_name=apps[0] if apps else '',
        ),
        watcher=WatcherConfig(
            applications=apps,
            disabled=disabled,
            auto_analyze=False,
            reconcile_interval=3600,
            jira_poll_interval=600,
            max_retries=3,
        ),
    )


class TestWatcherConfigDisable(unittest.TestCase):

    def test_is_enabled_default(self):
        wc = WatcherConfig(applications=('app',))
        for name in ALL_WATCHERS:
            self.assertTrue(wc.is_enabled(name))

    def test_is_enabled_with_disabled(self):
        wc = WatcherConfig(applications=('app',), disabled=frozenset({'jira', 'conforma'}))
        self.assertTrue(wc.is_enabled('builds'))
        self.assertTrue(wc.is_enabled('tests'))
        self.assertTrue(wc.is_enabled('components'))
        self.assertFalse(wc.is_enabled('jira'))
        self.assertFalse(wc.is_enabled('conforma'))

    def test_from_env_parses_disable(self):
        with patch.dict('os.environ', {
            'WATCH_APPLICATIONS': 'app-1',
            'WATCH_DISABLE': 'jira conforma',
        }):
            wc = WatcherConfig.from_env()
            self.assertFalse(wc.is_enabled('jira'))
            self.assertFalse(wc.is_enabled('conforma'))
            self.assertTrue(wc.is_enabled('builds'))

    def test_from_env_empty_disable(self):
        with patch.dict('os.environ', {
            'WATCH_APPLICATIONS': 'app-1',
        }, clear=True):
            wc = WatcherConfig.from_env()
            self.assertEqual(wc.disabled, frozenset())

    def test_all_watchers_constant(self):
        self.assertEqual(ALL_WATCHERS, ('builds', 'tests', 'conforma', 'jira', 'components'))


class TestDaemonConditionalTasks(unittest.TestCase):

    def _get_task_names(self, daemon):
        captured = []

        async def run():
            start_task = asyncio.create_task(daemon.start())
            await asyncio.sleep(0.05)
            captured.extend(t.get_name() for t in daemon._tasks)
            await daemon.stop()
            try:
                await start_task
            except asyncio.CancelledError:
                pass

        loop = asyncio.new_event_loop()
        loop.run_until_complete(run())
        loop.close()
        return captured

    def test_all_enabled_creates_all_tasks(self):
        config = _make_config(apps=('app-1',))
        handler_factory = MagicMock(return_value=(MagicMock(), MagicMock()))
        daemon = WatchDaemon(config, handler_factory=handler_factory)
        task_names = self._get_task_names(daemon)
        self.assertIn('watch-build-app-1', task_names)
        self.assertIn('watch-test-app-1', task_names)
        self.assertIn('watch-components-app-1', task_names)
        self.assertIn('reconciliation', task_names)

    def test_disable_builds_skips_build_watch(self):
        config = _make_config(apps=('app-1',), disabled=frozenset({'builds'}))
        handler_factory = MagicMock(return_value=(MagicMock(), MagicMock()))
        daemon = WatchDaemon(config, handler_factory=handler_factory)
        task_names = self._get_task_names(daemon)
        self.assertNotIn('watch-build-app-1', task_names)
        self.assertIn('watch-test-app-1', task_names)
        self.assertIn('watch-components-app-1', task_names)

    def test_disable_tests_skips_test_watch(self):
        config = _make_config(apps=('app-1',), disabled=frozenset({'tests'}))
        handler_factory = MagicMock(return_value=(MagicMock(), MagicMock()))
        daemon = WatchDaemon(config, handler_factory=handler_factory)
        task_names = self._get_task_names(daemon)
        self.assertIn('watch-build-app-1', task_names)
        self.assertNotIn('watch-test-app-1', task_names)

    def test_disable_components_skips_component_watch(self):
        config = _make_config(apps=('app-1',), disabled=frozenset({'components'}))
        handler_factory = MagicMock(return_value=(MagicMock(), MagicMock()))
        daemon = WatchDaemon(config, handler_factory=handler_factory)
        task_names = self._get_task_names(daemon)
        self.assertNotIn('watch-components-app-1', task_names)
        self.assertIn('watch-build-app-1', task_names)

    def test_disable_jira_skips_jira_poller(self):
        config = _make_config(apps=('app-1',), disabled=frozenset({'jira'}))
        handler_factory = MagicMock(return_value=(MagicMock(), MagicMock()))
        daemon = WatchDaemon(config, handler_factory=handler_factory)
        task_names = self._get_task_names(daemon)
        self.assertNotIn('jira-poller', task_names)

    def test_jira_enabled_creates_poller(self):
        config = _make_config(apps=('app-1',))
        handler_factory = MagicMock(return_value=(MagicMock(), MagicMock()))
        daemon = WatchDaemon(config, handler_factory=handler_factory)
        task_names = self._get_task_names(daemon)
        self.assertIn('jira-poller', task_names)

    def test_disable_all_leaves_only_reconciliation(self):
        config = _make_config(
            apps=('app-1',),
            disabled=frozenset({'builds', 'tests', 'components', 'jira'}),
        )
        handler_factory = MagicMock(return_value=(MagicMock(), MagicMock()))
        daemon = WatchDaemon(config, handler_factory=handler_factory)
        task_names = self._get_task_names(daemon)
        self.assertEqual(task_names, ['reconciliation'])

    def test_disabled_watchers_logged(self):
        config = _make_config(apps=('app-1',), disabled=frozenset({'jira', 'conforma'}))
        handler_factory = MagicMock(return_value=(MagicMock(), MagicMock()))
        daemon = WatchDaemon(config, handler_factory=handler_factory)

        async def run():
            with patch('watcher.daemon.logger') as mock_logger:
                start_task = asyncio.create_task(daemon.start())
                await asyncio.sleep(0.05)
                calls = [str(c) for c in mock_logger.info.call_args_list]
                await daemon.stop()
                try:
                    await start_task
                except asyncio.CancelledError:
                    pass
                return any('Disabled' in c for c in calls)

        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(run())
        loop.close()
        self.assertTrue(result)


class TestJiraPollLoop(unittest.TestCase):

    def test_jira_poll_skips_when_not_configured(self):
        config = _make_config()
        daemon = WatchDaemon(config)
        daemon._run_jira_poll()

    def test_jira_poll_calls_poller(self):
        from config import JiraConfig, LLMConfig
        from dataclasses import replace
        config = _make_config()
        config = replace(
            config,
            jira=JiraConfig(base_url='https://jira.test', email='a@b', token='t', project='TEST'),
            llm=LLMConfig(provider='anthropic', model='test', api_key='test-key'),
        )
        daemon = WatchDaemon(config)

        mock_poller_cls = MagicMock()
        mock_poller_cls.return_value.run.return_value = {
            'tickets': 5, 'new_comments': 2, 'drafted': 1,
        }

        with patch('poll_jira_comments.JiraCommentPoller', mock_poller_cls):
            daemon._run_jira_poll()
            mock_poller_cls.assert_called_once_with(config)
            mock_poller_cls.return_value.run.assert_called_once()


class TestCLIWatchList(unittest.TestCase):

    def setUp(self):
        os.environ['IC_MODE'] = 'local'

    def tearDown(self):
        os.environ.pop('IC_MODE', None)

    def test_list_shows_applications(self):
        runner = CliRunner()
        with patch.dict(os.environ, {
            'WATCH_APPLICATIONS': 'app-ea1 app-ea2',
        }):
            result = runner.invoke(cli, ['config', 'watch', 'list'])
            self.assertEqual(result.exit_code, 0)
            self.assertIn('app-ea1', result.output)
            self.assertIn('app-ea2', result.output)

    def test_list_shows_watcher_status(self):
        runner = CliRunner()
        with patch.dict(os.environ, {
            'WATCH_APPLICATIONS': 'app-1',
            'WATCH_DISABLE': 'jira conforma',
        }):
            result = runner.invoke(cli, ['config', 'watch', 'list'])
            self.assertEqual(result.exit_code, 0)
            self.assertIn('disabled', result.output)
            self.assertIn('enabled', result.output)

    def test_list_no_apps(self):
        runner = CliRunner()
        with patch.dict(os.environ, {}, clear=True):
            result = runner.invoke(cli, ['config', 'watch', 'list'])
            self.assertEqual(result.exit_code, 0)
            self.assertIn('none', result.output)

    def test_list_falls_back_to_application_name(self):
        runner = CliRunner()
        with patch.dict(os.environ, {'APPLICATION_NAME': 'fallback-app', 'IC_MODE': 'local'},
                        clear=True):
            result = runner.invoke(cli, ['config', 'watch', 'list'])
            self.assertEqual(result.exit_code, 0)
            self.assertIn('fallback-app', result.output)


class TestCLIWatchAddRemove(unittest.TestCase):

    def setUp(self):
        os.environ['IC_MODE'] = 'local'
        self.tmpdir = tempfile.mkdtemp()
        self.env_file = os.path.join(self.tmpdir, '.env')
        with open(self.env_file, 'w') as f:
            f.write('WATCH_APPLICATIONS=app-1\n')
            f.write('OTHER_VAR=keep\n')

    def test_add_new_app(self):
        runner = CliRunner()
        with patch.dict(os.environ, {'WATCH_APPLICATIONS': 'app-1'}), \
             patch('cli.main.cfg.PROJECT_DIR', self.tmpdir):
            result = runner.invoke(cli, ['config', 'watch', 'add', 'app-2', '--force'])
            self.assertEqual(result.exit_code, 0)
            self.assertIn('Added', result.output)

        with open(self.env_file) as f:
            content = f.read()
        self.assertIn('WATCH_APPLICATIONS="app-1 app-2"', content)
        self.assertIn('OTHER_VAR=keep', content)

    def test_add_duplicate(self):
        runner = CliRunner()
        with patch.dict(os.environ, {'WATCH_APPLICATIONS': 'app-1'}), \
             patch('cli.main.cfg.PROJECT_DIR', self.tmpdir):
            result = runner.invoke(cli, ['config', 'watch', 'add', 'app-1', '--force'])
            self.assertEqual(result.exit_code, 0)
            self.assertIn('Already', result.output)

    def test_remove_app(self):
        runner = CliRunner()
        with patch.dict(os.environ, {'WATCH_APPLICATIONS': 'app-1 app-2'}), \
             patch('cli.main.cfg.PROJECT_DIR', self.tmpdir):
            result = runner.invoke(cli, ['config', 'watch', 'remove', 'app-1'])
            self.assertEqual(result.exit_code, 0)
            self.assertIn('Removed', result.output)

    def test_remove_nonexistent(self):
        runner = CliRunner()
        with patch.dict(os.environ, {'WATCH_APPLICATIONS': 'app-1'}):
            result = runner.invoke(cli, ['config', 'watch', 'remove', 'nope'])
            self.assertEqual(result.exit_code, 1)
            self.assertIn('Not in watch list', result.output)


class TestCLIWatchEnableDisable(unittest.TestCase):

    def setUp(self):
        os.environ['IC_MODE'] = 'local'
        self.tmpdir = tempfile.mkdtemp()
        self.env_file = os.path.join(self.tmpdir, '.env')
        with open(self.env_file, 'w') as f:
            f.write('WATCH_DISABLE=jira conforma\n')

    def test_enable_watcher(self):
        runner = CliRunner()
        with patch.dict(os.environ, {'WATCH_DISABLE': 'jira conforma'}), \
             patch('cli.main.cfg.PROJECT_DIR', self.tmpdir):
            result = runner.invoke(cli, ['config', 'watch', 'enable', 'jira'])
            self.assertEqual(result.exit_code, 0)
            self.assertIn('Enabled', result.output)

        with open(self.env_file) as f:
            content = f.read()
        self.assertIn('WATCH_DISABLE=conforma', content)
        self.assertNotIn('jira', content)

    def test_enable_already_enabled(self):
        runner = CliRunner()
        with patch.dict(os.environ, {'WATCH_DISABLE': 'jira'}):
            result = runner.invoke(cli, ['config', 'watch', 'enable', 'builds'])
            self.assertEqual(result.exit_code, 0)
            self.assertIn('Already enabled', result.output)

    def test_disable_watcher(self):
        runner = CliRunner()
        with patch.dict(os.environ, {'WATCH_DISABLE': ''}), \
             patch('cli.main.cfg.PROJECT_DIR', self.tmpdir):
            result = runner.invoke(cli, ['config', 'watch', 'disable', 'builds'])
            self.assertEqual(result.exit_code, 0)
            self.assertIn('Disabled', result.output)

    def test_disable_already_disabled(self):
        runner = CliRunner()
        with patch.dict(os.environ, {'WATCH_DISABLE': 'jira'}):
            result = runner.invoke(cli, ['config', 'watch', 'disable', 'jira'])
            self.assertEqual(result.exit_code, 0)
            self.assertIn('Already disabled', result.output)

    def test_enable_unknown_watcher(self):
        runner = CliRunner()
        result = runner.invoke(cli, ['config', 'watch', 'enable', 'bogus'])
        self.assertEqual(result.exit_code, 1)
        self.assertIn('Unknown watcher', result.output)
        self.assertIn('Available', result.output)

    def test_disable_unknown_watcher(self):
        runner = CliRunner()
        result = runner.invoke(cli, ['config', 'watch', 'disable', 'bogus'])
        self.assertEqual(result.exit_code, 1)
        self.assertIn('Unknown watcher', result.output)


class TestUpdateEnvVar(unittest.TestCase):

    def test_updates_existing_var(self):
        tmpdir = tempfile.mkdtemp()
        env_file = os.path.join(tmpdir, '.env')
        with open(env_file, 'w') as f:
            f.write('FOO=old\nBAR=keep\n')

        with patch('cli.main.cfg.PROJECT_DIR', tmpdir):
            from cli.main import _update_env_var
            _update_env_var('FOO', 'new')

        with open(env_file) as f:
            content = f.read()
        self.assertIn('FOO=new', content)
        self.assertIn('BAR=keep', content)
        self.assertNotIn('FOO=old', content)
        self.assertEqual(os.environ['FOO'], 'new')

    def test_appends_new_var(self):
        tmpdir = tempfile.mkdtemp()
        env_file = os.path.join(tmpdir, '.env')
        with open(env_file, 'w') as f:
            f.write('EXISTING=yes\n')

        with patch('cli.main.cfg.PROJECT_DIR', tmpdir):
            from cli.main import _update_env_var
            _update_env_var('NEW_VAR', 'hello')

        with open(env_file) as f:
            content = f.read()
        self.assertIn('EXISTING=yes', content)
        self.assertIn('NEW_VAR=hello', content)


    def test_creates_env_file_if_missing(self):
        tmpdir = tempfile.mkdtemp()
        env_file = os.path.join(tmpdir, '.env')

        with patch('cli.main.cfg.PROJECT_DIR', tmpdir):
            from cli.main import _update_env_var
            _update_env_var('BRAND_NEW', 'value')

        self.assertTrue(os.path.exists(env_file))
        with open(env_file) as f:
            content = f.read()
        self.assertEqual(content, 'BRAND_NEW=value\n')


class TestSeenUidsCap(unittest.TestCase):

    def test_seen_uids_clears_at_cap(self):
        from watcher.handlers import BuildEventHandler
        build_repo = MagicMock()
        build_repo.upsert_failure.return_value = True
        handler = BuildEventHandler(
            application='test-app',
            namespace='test-ns',
            build_repo=build_repo,
        )

        for i in range(BuildEventHandler._MAX_SEEN_UIDS):
            pr = {
                'metadata': {
                    'name': 'pr-{}'.format(i),
                    'uid': 'uid-{}'.format(i),
                    'labels': {'appstudio.openshift.io/component': 'comp'},
                },
                'spec': {'params': []},
                'status': {
                    'conditions': [{'type': 'Succeeded', 'status': 'False', 'reason': 'Failed'}],
                },
            }
            handler.handle({'type': 'MODIFIED', 'object': pr})

        self.assertEqual(len(handler._seen_uids), BuildEventHandler._MAX_SEEN_UIDS)

        pr_overflow = {
            'metadata': {
                'name': 'pr-overflow',
                'uid': 'uid-overflow',
                'labels': {'appstudio.openshift.io/component': 'comp'},
            },
            'spec': {'params': []},
            'status': {
                'conditions': [{'type': 'Succeeded', 'status': 'False', 'reason': 'Failed'}],
            },
        }
        handler.handle({'type': 'MODIFIED', 'object': pr_overflow})
        self.assertEqual(len(handler._seen_uids), 1)
        self.assertIn('uid-overflow', handler._seen_uids)

        build_repo.upsert_failure.reset_mock()
        handler.handle({'type': 'MODIFIED', 'object': pr_overflow})
        build_repo.upsert_failure.assert_not_called()


if __name__ == '__main__':
    unittest.main()
