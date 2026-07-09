"""Integration tests against the live API server.

These tests run against a real API (local or cluster) and verify
end-to-end data flow. They are skipped if no API is reachable.

Run with:
    IC_MODE=cluster python -m pytest tests/test_integration_api.py -v
    IC_API_URL=http://localhost:8000 python -m pytest tests/test_integration_api.py -v
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


@unittest.skipUnless(_api_reachable(), 'API not reachable — set IC_MODE=cluster or IC_API_URL')
class TestAPIHealth(unittest.TestCase):

    def setUp(self):
        self.url = _api_url()
        self.headers = {}
        key = _api_key()
        if key:
            self.headers['Authorization'] = 'Bearer {}'.format(key)

    def _get(self, path, expected_status=200):
        r = requests.get('{}{}'.format(self.url, path),
                         headers=self.headers, timeout=15, verify=False)
        self.assertEqual(r.status_code, expected_status,
                         'GET {} returned {}: {}'.format(path, r.status_code, r.text[:200]))
        return r.json()

    def test_health(self):
        data = self._get('/health')
        self.assertEqual(data['status'], 'healthy')
        self.assertEqual(data['database'], 'connected')

    def test_applications_list(self):
        data = self._get('/api/v1/applications')
        self.assertIsInstance(data, list)
        if data:
            app = data[0]
            self.assertIn('name', app)
            self.assertIn('failure_count', app)

    def test_alerts(self):
        apps = self._get('/api/v1/applications')
        if not apps:
            self.skipTest('No applications in DB')
        app = apps[0]['name']
        data = self._get('/api/v1/applications/{}/alerts'.format(app))
        self.assertIn('build_failures', data)
        self.assertIn('conforma_violations', data)
        self.assertIn('total_count', data)

    def test_alerts_with_release_schedule(self):
        apps = self._get('/api/v1/applications')
        if not apps:
            self.skipTest('No applications in DB')
        app = apps[0]['name']
        data = self._get('/api/v1/applications/{}/alerts'.format(app))
        if data.get('release_schedule'):
            schedule = data['release_schedule']
            self.assertIn('code_freeze', schedule)

    def test_conforma_violations(self):
        apps = self._get('/api/v1/applications')
        if not apps:
            self.skipTest('No applications in DB')
        app = apps[0]['name']
        data = self._get('/api/v1/applications/{}/violations'.format(app))
        self.assertIsInstance(data, list)
        if data:
            v = data[0]
            self.assertIn('component_name', v)
            self.assertIn('violations_count', v)

    def test_failures_list(self):
        apps = self._get('/api/v1/applications')
        if not apps:
            self.skipTest('No applications in DB')
        app = apps[0]['name']
        data = self._get('/api/v1/applications/{}/failures'.format(app))
        self.assertIsInstance(data, list)

    def test_failure_detail_not_found(self):
        data = self._get('/api/v1/applications/rhoai-v3-5-ea-2/failures/nonexistent-component-xyz',
                         expected_status=404)
        self.assertIn('error', data)
        self.assertEqual(data['error'], 'not_found')
        self.assertIn('suggestion', data)

    def test_stats(self):
        apps = self._get('/api/v1/applications')
        if not apps:
            self.skipTest('No applications in DB')
        app = apps[0]['name']
        data = self._get('/api/v1/applications/{}/stats'.format(app))
        self.assertIsInstance(data, dict)

    def test_triage(self):
        apps = self._get('/api/v1/applications')
        if not apps:
            self.skipTest('No applications in DB')
        app = apps[0]['name']
        data = self._get('/api/v1/applications/{}/triage'.format(app))
        self.assertIsInstance(data, dict)

    def test_schedule(self):
        apps = self._get('/api/v1/applications')
        if not apps:
            self.skipTest('No applications in DB')
        app = apps[0]['name']
        data = self._get('/api/v1/applications/{}/schedule'.format(app))
        # May be null if no schedule configured
        if data:
            self.assertIn('application', data)

    def test_skills_list(self):
        data = self._get('/api/v1/skills')
        self.assertIsInstance(data, list)

    @unittest.expectedFailure
    def test_config_applications(self):
        data = self._get('/api/v1/config/applications')
        self.assertIn('applications', data)
        self.assertIsInstance(data['applications'], list)

    def test_invalid_application_name(self):
        data = self._get('/api/v1/applications/INVALID NAME!!/alerts',
                         expected_status=422)
        self.assertIn('error', data)
        self.assertEqual(data['error'], 'validation_error')

    def test_patterns(self):
        data = self._get('/api/v1/patterns')
        self.assertIsInstance(data, list)

    def test_nightly_history(self):
        apps = self._get('/api/v1/applications')
        if not apps:
            self.skipTest('No applications in DB')
        app = apps[0]['name']
        data = self._get('/api/v1/applications/{}/nightly/history'.format(app))
        self.assertIn('nightly_builds', data)
        self.assertIn('fbc_fragments', data)


@unittest.skipUnless(_api_reachable(), 'API not reachable')
class TestAPICLIParity(unittest.TestCase):
    """Verify CLI and API return consistent data."""

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

    def test_alerts_has_conforma_details(self):
        """Alerts should include violation counts for conforma items."""
        apps = self._get('/api/v1/applications')
        if not apps:
            self.skipTest('No applications')
        app = apps[0]['name']
        data = self._get('/api/v1/applications/{}/alerts'.format(app))
        for v in data.get('conforma_violations', []):
            self.assertIn('violations_count', v,
                          'Conforma violation missing violations_count: {}'.format(v.get('component')))
            self.assertIn('policy_url', v,
                          'Conforma violation missing policy_url: {}'.format(v.get('component')))

    def test_failure_detail_has_history(self):
        """Failure detail should include build history."""
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
            self.assertIn('build_history', detail,
                          'Failure detail missing build_history')
            self.assertIn('status', detail)
            self.assertIn('jira_key', detail)
