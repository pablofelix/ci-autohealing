"""Tests for the WatchDaemon orchestrator."""

import asyncio
import unittest
from unittest.mock import MagicMock, patch

from config import (
    CollectorConfig, DatabaseConfig, KubernetesConfig, WatcherConfig,
)
from watcher.daemon import WatchDaemon, _GoneError


def _make_config(apps=('test-app',)):
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
            auto_analyze=False,
            reconcile_interval=3600,
            jira_poll_interval=600,
            max_retries=3,
        ),
    )


class TestWatchDaemonInit(unittest.TestCase):

    def test_creates_with_config(self):
        config = _make_config()
        daemon = WatchDaemon(config)
        self.assertEqual(daemon.namespace, 'test-ns')
        self.assertEqual(daemon.watcher_config.applications, ('test-app',))

    def test_no_apps_logs_error(self):
        config = _make_config(apps=())
        daemon = WatchDaemon(config)
        loop = asyncio.new_event_loop()
        loop.run_until_complete(daemon.start())
        loop.close()
        self.assertEqual(len(daemon._tasks), 0)


class TestWatchDaemonMultiApp(unittest.TestCase):

    def test_creates_tasks_per_app(self):
        config = _make_config(apps=('app-ea1', 'app-ea2'))
        handler_factory = MagicMock(return_value=(MagicMock(), MagicMock()))
        daemon = WatchDaemon(config, handler_factory=handler_factory)

        async def start_and_stop():
            start_task = asyncio.create_task(daemon.start())
            await asyncio.sleep(0.05)
            await daemon.stop()
            try:
                await start_task
            except asyncio.CancelledError:
                pass

        loop = asyncio.new_event_loop()
        loop.run_until_complete(start_and_stop())
        loop.close()

        self.assertEqual(handler_factory.call_count, 2)
        handler_factory.assert_any_call('app-ea1')
        handler_factory.assert_any_call('app-ea2')


class TestWatchStream(unittest.TestCase):

    def test_watch_stream_processes_events(self):
        config = _make_config()
        daemon = WatchDaemon(config)
        daemon._running = True

        handler = MagicMock()
        events = [
            {
                'type': 'MODIFIED',
                'object': {
                    'metadata': {'resourceVersion': '100', 'name': 'pr-1'},
                    'status': {},
                },
            },
            {
                'type': 'MODIFIED',
                'object': {
                    'metadata': {'resourceVersion': '101', 'name': 'pr-2'},
                    'status': {},
                },
            },
        ]

        mock_watch = MagicMock()
        mock_watch.stream.return_value = iter(events)

        with patch('watcher.daemon.watch.Watch', return_value=mock_watch), \
             patch('watcher.daemon._ensure_k8s_config'), \
             patch('watcher.daemon.client.CustomObjectsApi'):
            daemon._watch_stream(
                'tekton.dev', 'v1', 'pipelineruns',
                'app=test', handler, 'test-app/build', None,
            )

        self.assertEqual(handler.handle.call_count, 2)
        self.assertEqual(daemon._resource_versions['test-app/build'], '101')

    def test_watch_stream_raises_on_410(self):
        config = _make_config()
        daemon = WatchDaemon(config)
        daemon._running = True

        events = [
            {
                'type': 'ERROR',
                'object': {'code': 410, 'message': 'Gone'},
            },
        ]

        mock_watch = MagicMock()
        mock_watch.stream.return_value = iter(events)

        with patch('watcher.daemon.watch.Watch', return_value=mock_watch), \
             patch('watcher.daemon._ensure_k8s_config'), \
             patch('watcher.daemon.client.CustomObjectsApi'):
            with self.assertRaises(_GoneError):
                daemon._watch_stream(
                    'tekton.dev', 'v1', 'pipelineruns',
                    'app=test', MagicMock(), 'test-app/build', None,
                )

    def test_handler_error_doesnt_crash_stream(self):
        config = _make_config()
        daemon = WatchDaemon(config)
        daemon._running = True

        handler = MagicMock()
        handler.handle.side_effect = [ValueError("boom"), None]

        events = [
            {'type': 'MODIFIED', 'object': {'metadata': {'resourceVersion': '1', 'name': 'pr-1'}}},
            {'type': 'MODIFIED', 'object': {'metadata': {'resourceVersion': '2', 'name': 'pr-2'}}},
        ]

        mock_watch = MagicMock()
        mock_watch.stream.return_value = iter(events)

        with patch('watcher.daemon.watch.Watch', return_value=mock_watch), \
             patch('watcher.daemon._ensure_k8s_config'), \
             patch('watcher.daemon.client.CustomObjectsApi'):
            daemon._watch_stream(
                'tekton.dev', 'v1', 'pipelineruns',
                'app=test', handler, 'test-app/build', None,
            )

        self.assertEqual(handler.handle.call_count, 2)


class TestWatcherConfig(unittest.TestCase):

    def test_from_env_with_applications(self):
        with patch.dict('os.environ', {
            'WATCH_APPLICATIONS': 'app-ea1 app-ea2',
            'WATCH_AUTO_ANALYZE': 'false',
            'WATCH_RECONCILE_INTERVAL': '900',
        }):
            wc = WatcherConfig.from_env()
            self.assertEqual(wc.applications, ('app-ea1', 'app-ea2'))
            self.assertFalse(wc.auto_analyze)
            self.assertEqual(wc.reconcile_interval, 900)

    def test_from_env_falls_back_to_application_name(self):
        with patch.dict('os.environ', {
            'APPLICATION_NAME': 'my-app',
        }, clear=True):
            wc = WatcherConfig.from_env()
            self.assertEqual(wc.applications, ('my-app',))

    def test_from_env_empty(self):
        with patch.dict('os.environ', {}, clear=True):
            wc = WatcherConfig.from_env()
            self.assertEqual(wc.applications, ())

    def test_defaults(self):
        wc = WatcherConfig(applications=('app',))
        self.assertTrue(wc.auto_analyze)
        self.assertEqual(wc.reconcile_interval, 1800)
        self.assertEqual(wc.jira_poll_interval, 600)
        self.assertEqual(wc.max_retries, 5)


if __name__ == '__main__':
    unittest.main()
