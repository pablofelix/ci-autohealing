"""Tests for the worker pipeline."""

import json
import time
import threading
import unittest
from unittest.mock import patch
from urllib.request import urlopen

from worker.pipeline import PipelineStep, WorkerPipeline
from worker.health import start_health_server


class TestPipelineStep(unittest.TestCase):

    def test_is_due_initially(self):
        step = PipelineStep('test', lambda: None, interval_seconds=60)
        self.assertTrue(step.is_due())

    def test_not_due_after_run(self):
        step = PipelineStep('test', lambda: None, interval_seconds=60)
        step.execute()
        self.assertFalse(step.is_due())

    def test_due_after_interval(self):
        step = PipelineStep('test', lambda: None, interval_seconds=0)
        step.execute()
        self.assertTrue(step.is_due())

    def test_execute_tracks_stats(self):
        step = PipelineStep('test', lambda: {'ok': True})
        result = step.execute()
        self.assertEqual(result, {'ok': True})
        self.assertEqual(step.run_count, 1)
        self.assertEqual(step.error_count, 0)
        self.assertIsNone(step.last_error)
        self.assertGreater(step.last_run, 0)

    def test_execute_tracks_errors(self):
        def failing():
            raise RuntimeError("boom")

        step = PipelineStep('test', failing)
        with self.assertRaises(RuntimeError):
            step.execute()
        self.assertEqual(step.run_count, 0)
        self.assertEqual(step.error_count, 1)
        self.assertEqual(step.last_error, 'boom')

    @patch.dict('os.environ', {'MY_TOKEN': 'secret'})
    def test_env_satisfied_when_present(self):
        step = PipelineStep('test', lambda: None, requires_env=['MY_TOKEN'])
        self.assertTrue(step.env_satisfied())

    @patch.dict('os.environ', {}, clear=True)
    def test_env_not_satisfied_when_missing(self):
        step = PipelineStep('test', lambda: None, requires_env=['MISSING_VAR'])
        self.assertFalse(step.env_satisfied())

    @patch.dict('os.environ', {'AUTONOMOUS_MODE': 'true'})
    def test_autonomous_mode_true(self):
        step = PipelineStep('test', lambda: None, requires_env=['AUTONOMOUS_MODE'])
        self.assertTrue(step.env_satisfied())

    @patch.dict('os.environ', {'AUTONOMOUS_MODE': 'false'})
    def test_autonomous_mode_false(self):
        step = PipelineStep('test', lambda: None, requires_env=['AUTONOMOUS_MODE'])
        self.assertFalse(step.env_satisfied())

    def test_status_dict(self):
        step = PipelineStep('collect', lambda: None, interval_seconds=600)
        status = step.status()
        self.assertEqual(status['name'], 'collect')
        self.assertEqual(status['interval_seconds'], 600)
        self.assertEqual(status['run_count'], 0)


class TestWorkerPipeline(unittest.TestCase):

    def test_add_step(self):
        pipeline = WorkerPipeline()
        pipeline.add_step(PipelineStep('a', lambda: None))
        pipeline.add_step(PipelineStep('b', lambda: None))
        self.assertEqual(len(pipeline.steps), 2)

    def test_run_executes_due_steps(self):
        calls = []

        def step_fn():
            calls.append(1)

        pipeline = WorkerPipeline(check_interval=0)
        pipeline.add_step(PipelineStep('test', step_fn, interval_seconds=9999))

        def stop_after_one():
            time.sleep(0.05)
            pipeline.stop()

        threading.Thread(target=stop_after_one, daemon=True).start()
        pipeline.run()

        self.assertEqual(len(calls), 1)

    def test_non_critical_failure_continues(self):
        calls = []

        def good_step():
            calls.append('good')

        def bad_step():
            raise RuntimeError("non-critical")

        pipeline = WorkerPipeline(check_interval=0)
        pipeline.add_step(PipelineStep('bad', bad_step, interval_seconds=9999, critical=False))
        pipeline.add_step(PipelineStep('good', good_step, interval_seconds=9999))

        def stop_soon():
            time.sleep(0.1)
            pipeline.stop()

        threading.Thread(target=stop_soon, daemon=True).start()
        pipeline.run()

        self.assertIn('good', calls)

    def test_critical_failure_stops(self):
        def failing():
            raise RuntimeError("critical failure")

        pipeline = WorkerPipeline(check_interval=0)
        pipeline.add_step(PipelineStep('critical', failing, interval_seconds=0, critical=True))

        with self.assertRaises(RuntimeError):
            pipeline.run()

        self.assertFalse(pipeline.running)

    @patch.dict('os.environ', {}, clear=True)
    def test_skips_unconfigured_steps(self):
        calls = []

        def gated():
            calls.append('gated')

        pipeline = WorkerPipeline(check_interval=0)
        pipeline.add_step(PipelineStep('gated', gated, interval_seconds=0,
                                        requires_env=['MISSING_TOKEN']))

        def stop_soon():
            time.sleep(0.05)
            pipeline.stop()

        threading.Thread(target=stop_soon, daemon=True).start()
        pipeline.run()

        self.assertEqual(calls, [])

    def test_status(self):
        pipeline = WorkerPipeline()
        pipeline.add_step(PipelineStep('a', lambda: None))
        status = pipeline.status()
        self.assertFalse(status['running'])
        self.assertEqual(len(status['steps']), 1)

    def test_stop_sets_running_false(self):
        pipeline = WorkerPipeline()
        pipeline.running = True
        pipeline.stop()
        self.assertFalse(pipeline.running)


class TestHealthServer(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.pipeline = WorkerPipeline()
        cls.pipeline.add_step(PipelineStep('test_step', lambda: None))
        cls.server = start_health_server(cls.pipeline, port=18901)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_health_endpoint(self):
        resp = urlopen('http://localhost:18901/health')
        data = json.loads(resp.read())
        self.assertEqual(data['status'], 'ok')

    def test_status_endpoint(self):
        resp = urlopen('http://localhost:18901/status')
        data = json.loads(resp.read())
        self.assertIn('steps', data)
        self.assertEqual(len(data['steps']), 1)
        self.assertEqual(data['steps'][0]['name'], 'test_step')

    def test_404_for_unknown_path(self):
        from urllib.error import HTTPError
        with self.assertRaises(HTTPError) as ctx:
            urlopen('http://localhost:18901/unknown')
        self.assertEqual(ctx.exception.code, 404)


if __name__ == '__main__':
    unittest.main()
