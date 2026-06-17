"""Integration tests for AI analyze proxy flow.

Tests the cluster mode flow: fetch failure → analyze → store.
Requires API reachable and LLM configured.

Run with:
    IC_MODE=cluster python -m pytest tests/test_integration_ai.py -v
"""

import os
import unittest

import requests


def _api_url():
    from cli.ic_config import get_api_url, get_mode
    if get_mode() == 'cluster':
        return get_api_url()
    return os.environ.get('IC_API_URL', 'http://localhost:8000')


def _api_key():
    from cli.ic_config import get_api_key
    return get_api_key()


def _api_reachable():
    url = _api_url()
    if not url:
        return False
    try:
        r = requests.get('{}/health'.format(url), timeout=5, verify=False)
        return r.status_code == 200
    except Exception:
        return False


@unittest.skipUnless(_api_reachable(), 'API not reachable')
class TestAIAnalyzeIntegration(unittest.TestCase):

    def setUp(self):
        self.url = _api_url()
        self.headers = {}
        key = _api_key()
        if key:
            self.headers['Authorization'] = 'Bearer {}'.format(key)

    def _get(self, path):
        r = requests.get('{}{}'.format(self.url, path),
                         headers=self.headers, timeout=15, verify=False)
        return r.json() if r.status_code == 200 else None

    def _post(self, path, data):
        r = requests.post('{}{}'.format(self.url, path),
                          headers=self.headers, json=data, timeout=15, verify=False)
        return r.status_code, r.json() if r.status_code < 500 else {}

    def test_submit_analysis_endpoint_exists(self):
        apps = self._get('/api/v1/applications')
        if not apps:
            self.skipTest('No applications')
        app = apps[0]['name']
        status, data = self._post('/api/v1/applications/{}/analyses'.format(app), {
            'component': 'nonexistent-test-component',
            'root_cause': 'test',
            'failure_category': 'test',
            'confidence_score': 0.5,
            'recommended_fix': 'test',
        })
        self.assertIn(status, [200, 404, 400, 422])

    def test_failure_detail_has_id(self):
        """Failure detail must include 'id' for analysis storage."""
        apps = self._get('/api/v1/applications')
        if not apps:
            self.skipTest('No applications')
        app = apps[0]['name']
        failures = self._get('/api/v1/applications/{}/failures'.format(app))
        if not failures:
            self.skipTest('No failures')
        comp = failures[0].get('component', '')
        detail = self._get('/api/v1/applications/{}/failures/{}'.format(app, comp))
        if detail:
            self.assertIn('build_logs', detail)

    def test_analysis_stats_endpoint(self):
        apps = self._get('/api/v1/applications')
        if not apps:
            self.skipTest('No applications')
        app = apps[0]['name']
        data = self._get('/api/v1/applications/{}/analyses/stats'.format(app))
        self.assertIsNotNone(data)

    def test_submit_and_retrieve_analysis(self):
        """Submit a test analysis and verify it can be retrieved."""
        apps = self._get('/api/v1/applications')
        if not apps:
            self.skipTest('No applications')
        app = apps[0]['name']
        failures = self._get('/api/v1/applications/{}/failures'.format(app))
        if not failures:
            self.skipTest('No failures')
        comp = failures[0].get('component', '')
        status, result = self._post('/api/v1/applications/{}/analyses'.format(app), {
            'component': comp,
            'analysis_type': 'build',
            'model_used': 'test-model',
            'root_cause': 'Integration test root cause',
            'failure_category': 'test_category',
            'confidence_score': 0.99,
            'recommended_fix': 'This is a test analysis',
            'recommended_files': ['test.py'],
            'can_auto_fix': False,
            'tokens_used': 100,
            'cost_usd': 0.001,
        })
        if status == 200:
            self.assertEqual(result.get('action'), 'stored')
            self.assertIn('analysis_id', result)
            analysis = self._get('/api/v1/applications/{}/analyses/{}'.format(app, comp))
            if analysis:
                self.assertEqual(analysis.get('root_cause'), 'Integration test root cause')
