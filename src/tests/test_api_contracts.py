"""API contract tests — verify response schemas, status codes, and headers.

Contract tests ensure API consumers get stable, predictable responses.
They verify the SHAPE of responses (required fields, types, status codes)
rather than the CONTENT (which unit tests cover).
"""
import os
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api import create_app
from mcp_server.models import (
    AlertsSummary,
    ApplicationInfo,
    BlockersSummary,
    DashboardResponse,
    ExceptionLifecycleSummary,
    StatsResponse,
    TriageResponse,
)


@pytest.fixture
def client():
    """Full app with all routes mounted — mirrors production setup."""
    with patch.dict(os.environ, {
        'DB_HOST': 'localhost',
        'DB_NAME': 'test_db',
        'DB_USER': 'test_user',
        'DB_PASS': 'test_pass',
        'NAMESPACE': 'test-tenant',
    }), patch('api.init_pool'), patch('api.rate_limit.setup_rate_limiter'):
        app = create_app()
        yield TestClient(app, raise_server_exceptions=False)


class TestHealthEndpoint:
    """Health check contracts."""

    @patch('api.routes.health.get_pool')
    def test_contract_health_success(self, mock_pool, client):
        """GET /health returns status and database fields."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (1,)
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=None)
        mock_pool.return_value.connection.return_value = mock_conn

        resp = client.get('/health')

        assert resp.status_code == 200
        assert 'application/json' in resp.headers['content-type']
        data = resp.json()
        assert 'status' in data
        assert 'database' in data
        assert isinstance(data['status'], str)
        assert isinstance(data['database'], str)


class TestApplicationsEndpoints:
    """Application-level endpoint contracts."""

    @patch('api.routes.applications.get_repository')
    def test_contract_list_applications(self, mock_get_repo, client):
        """GET /applications returns List[ApplicationInfo]-shaped response."""
        mock_build_repo = MagicMock()
        mock_conforma_repo = MagicMock()
        mock_build_repo.get_applications.return_value = [
            {'application': 'test-app'},
        ]
        mock_build_repo.get_overview_stats.return_value = {
            'components': 5,
            'total': 2,
        }
        mock_conforma_repo.find_unresolved_component_names.return_value = ['comp1']

        def side_effect(repo_class):
            from repositories.build_failure_repository import BuildFailureRepository
            from repositories.conforma_repository import ConformaRepository
            if repo_class == BuildFailureRepository:
                return mock_build_repo
            if repo_class == ConformaRepository:
                return mock_conforma_repo
            raise ValueError('Unknown repo')

        mock_get_repo.side_effect = side_effect

        resp = client.get('/api/v1/applications')

        assert resp.status_code == 200
        assert 'application/json' in resp.headers['content-type']
        data = resp.json()
        assert isinstance(data, list)
        for item in data:
            ApplicationInfo(**item)

    @patch('api.routes.applications.get_repository')
    def test_contract_get_stats(self, mock_get_repo, client):
        """GET /applications/{app}/stats returns StatsResponse-shaped response."""
        mock_ai_repo = MagicMock()
        mock_ai_repo.get_extended_status.return_value = {
            'build': {'pending': 3, 'analyzed': 1, 'auto_fixable': 0},
            'conforma': {'pending': 2, 'analyzed': 0, 'auto_fixable': 0},
            'total_cost': 1.5,
        }
        mock_ai_repo.get_recent_analyses.return_value = [
            {'component': 'test-comp', 'cost': 0.1},
        ]
        mock_get_repo.return_value = mock_ai_repo

        resp = client.get('/api/v1/applications/test-app/stats')

        assert resp.status_code == 200
        assert 'application/json' in resp.headers['content-type']
        data = resp.json()
        StatsResponse(**data)
        assert data['application'] == 'test-app'

    @patch('api.routes.applications.get_repository')
    def test_contract_get_dashboard(self, mock_get_repo, client):
        """GET /applications/{app}/dashboard returns DashboardResponse-shaped response."""
        mock_build_repo = MagicMock()
        mock_ai_repo = MagicMock()
        mock_pattern_repo = MagicMock()
        mock_fixes_repo = MagicMock()

        mock_build_repo.get_overview_stats.return_value = {
            'components': 10, 'total': 5, 'failing': 2,
        }
        mock_build_repo.get_enrichment_coverage.return_value = {
            'enriched': 8, 'total': 10,
        }
        mock_ai_repo.get_extended_status.return_value = {
            'build': {'pending': 1},
            'conforma': {'pending': 0},
        }
        mock_ai_repo.get_cost_summary.return_value = 2.0
        mock_pattern_repo.get_library_summary.return_value = {'total': 5}
        mock_fixes_repo.get_outcome_summary.return_value = {'success': 3}

        def side_effect(repo_class):
            from repositories.ai_analysis_repository import AIAnalysisRepository
            from repositories.build_failure_repository import BuildFailureRepository
            from repositories.error_pattern_repository import ErrorPatternRepository
            from repositories.resolution_attempt_repository import ResolutionAttemptRepository
            if repo_class == BuildFailureRepository:
                return mock_build_repo
            if repo_class == AIAnalysisRepository:
                return mock_ai_repo
            if repo_class == ErrorPatternRepository:
                return mock_pattern_repo
            if repo_class == ResolutionAttemptRepository:
                return mock_fixes_repo
            raise ValueError('Unknown repo')

        mock_get_repo.side_effect = side_effect

        resp = client.get('/api/v1/applications/test-app/dashboard')

        assert resp.status_code == 200
        assert 'application/json' in resp.headers['content-type']
        data = resp.json()
        DashboardResponse(**data)
        assert data['application'] == 'test-app'

    @patch('api.routes.applications.get_repository')
    def test_contract_get_triage(self, mock_get_repo, client):
        """GET /applications/{app}/triage-summary returns TriageResponse-shaped response."""
        mock_repo = MagicMock()
        mock_repo.get_triage_summary.return_value = {
            'total': 15,
            'failing': 3,
            'working': 12,
            'failing_components': [
                {'component': 'comp1', 'status': 'Failed'},
            ],
        }
        mock_get_repo.return_value = mock_repo

        resp = client.get('/api/v1/applications/test-app/triage-summary')

        assert resp.status_code == 200
        assert 'application/json' in resp.headers['content-type']
        data = resp.json()
        TriageResponse(**data)
        assert data['application'] == 'test-app'


class TestFailuresEndpoints:
    """Build failure endpoint contracts."""

    @patch('api.routes.failures.get_repository')
    @patch('api.routes.failures.check_jira_health')
    @patch('api.routes.releases.get_schedule')
    @patch('proactive.health_monitor.HealthMonitor')
    def test_contract_list_alerts(self, mock_health, mock_schedule, mock_jira,
                                    mock_get_repo, client):
        """GET /applications/{app}/alerts returns AlertsSummary-shaped response."""
        mock_build_repo = MagicMock()
        mock_conforma_repo = MagicMock()
        mock_triage_repo = MagicMock()

        mock_build_repo.get_triage_summary.return_value = {
            'total': 2,
            'failing': 1,
            'working': 1,
            'failing_components': [
                {
                    'component': 'test-comp',
                    'status': 'Failed',
                    'error_type': 'build',
                    'first_detected_at': datetime.utcnow(),
                    'last_updated_at': datetime.utcnow(),
                    'failure_count': 1,
                    'has_logs': True,
                    'ai_analyzed': False,
                },
            ],
        }
        mock_conforma_repo.find_unresolved_component_names.return_value = []
        mock_triage_repo.build_jira_map.return_value = {}

        def side_effect(repo_class):
            from repositories.build_failure_repository import BuildFailureRepository
            from repositories.conforma_repository import ConformaRepository
            from repositories.triage_repository import TriageRepository
            if repo_class == BuildFailureRepository:
                return mock_build_repo
            if repo_class == ConformaRepository:
                return mock_conforma_repo
            if repo_class == TriageRepository:
                return mock_triage_repo
            raise ValueError('Unknown repo')

        mock_get_repo.side_effect = side_effect

        mock_jira.return_value.status = 'valid'
        mock_schedule.return_value = None
        mock_health.return_value.get_stale_nightly_builds.return_value = []

        with patch('utils.conforma_utils.fetch_exceptions_by_policy', return_value={}):
            resp = client.get('/api/v1/applications/test-app/alerts')

        assert resp.status_code == 200
        assert 'application/json' in resp.headers['content-type']
        data = resp.json()
        AlertsSummary(**data)
        assert data['application'] == 'test-app'

    @patch('api.routes.failures.get_repository')
    def test_contract_list_failures(self, mock_get_repo, client):
        """GET /applications/{app}/failures returns list with FailureSummary shape."""
        mock_repo = MagicMock()
        mock_repo.get_triage_summary.return_value = {
            'failing_components': [
                {
                    'component': 'test-comp',
                    'status': 'Failed',
                    'error_type': 'build',
                    'first_detected_at': datetime.utcnow(),
                    'last_updated_at': datetime.utcnow(),
                    'failure_count': 1,
                    'has_logs': True,
                    'ai_analyzed': False,
                },
            ],
        }
        mock_get_repo.return_value = mock_repo

        resp = client.get('/api/v1/applications/test-app/failures')

        assert resp.status_code == 200
        assert 'application/json' in resp.headers['content-type']
        data = resp.json()
        assert isinstance(data, list)

    @patch('api.routes.failures.get_repository')
    def test_contract_get_blockers(self, mock_get_repo, client):
        """GET /applications/{app}/blockers returns BlockersSummary-shaped response."""
        from clients.jira_client import JiraClient
        with patch.object(JiraClient, 'search_blockers', return_value=[]):
            resp = client.get('/api/v1/applications/test-app/blockers')

        assert resp.status_code == 200
        assert 'application/json' in resp.headers['content-type']
        data = resp.json()
        BlockersSummary(**data)
        assert data['application'] == 'test-app'


class TestViolationsEndpoints:
    """Conforma violation endpoint contracts."""

    @patch('api.routes.violations.get_repository')
    def test_contract_list_violations(self, mock_get_repo, client):
        """GET /applications/{app}/violations returns list of violation dicts."""
        mock_conforma_repo = MagicMock()
        mock_triage_repo = MagicMock()
        mock_conforma_repo.get_violation_summaries.return_value = [
            {
                'component_name': 'test-comp',
                'scenario': 'ec-policy/test-policy.yaml',
                'violations_count': 3,
                'warnings_count': 1,
                'violation_summary': 'test: some error',
            },
        ]
        mock_triage_repo.build_jira_map.return_value = {}

        def side_effect(repo_class):
            from repositories.conforma_repository import ConformaRepository
            from repositories.triage_repository import TriageRepository
            if repo_class == ConformaRepository:
                return mock_conforma_repo
            if repo_class == TriageRepository:
                return mock_triage_repo
            raise ValueError('Unknown repo')

        mock_get_repo.side_effect = side_effect

        with patch('utils.conforma_utils.fetch_exceptions_by_policy', return_value={}):
            resp = client.get('/api/v1/applications/test-app/violations')

        assert resp.status_code == 200
        assert 'application/json' in resp.headers['content-type']
        data = resp.json()
        assert isinstance(data, list)

    @patch('utils.conforma_utils.fetch_exceptions_by_policy')
    def test_contract_get_exception_lifecycle(self, mock_fetch, client):
        """GET /exceptions/lifecycle returns ExceptionLifecycleSummary-shaped response."""
        mock_fetch.return_value = {
            'test-policy': [
                {
                    'value': 'test.rule',
                    'permanent': False,
                    'effectiveUntil': '2026-12-31',
                    'days_left': 100,
                },
            ],
        }

        resp = client.get('/api/v1/exceptions/lifecycle')

        assert resp.status_code == 200
        assert 'application/json' in resp.headers['content-type']
        data = resp.json()
        ExceptionLifecycleSummary(**data)


class TestConfigEndpoints:
    """Config endpoint contracts."""

    @patch('api.routes.config.get_repository')
    def test_contract_get_watched_applications(self, mock_get_repo, client):
        """GET /config/applications returns list of strings."""
        mock_repo = MagicMock()
        mock_repo.get_watched_applications.return_value = ['app1', 'app2']
        mock_get_repo.return_value = mock_repo

        resp = client.get('/api/v1/config/applications')

        assert resp.status_code == 200
        assert 'application/json' in resp.headers['content-type']
        data = resp.json()
        assert 'applications' in data
        assert isinstance(data['applications'], list)

    @patch('api.routes.config.get_repository')
    def test_contract_post_watched_application(self, mock_get_repo, client):
        """POST /config/applications returns updated list."""
        mock_repo = MagicMock()
        mock_repo.add_watched_application.return_value = ['app1', 'new-app']
        mock_get_repo.return_value = mock_repo

        resp = client.post('/api/v1/config/applications',
                          json={'application': 'new-app'})

        assert resp.status_code == 200
        assert 'application/json' in resp.headers['content-type']
        data = resp.json()
        assert 'applications' in data
        assert isinstance(data['applications'], list)

    @patch('api.routes.config.get_repository')
    def test_contract_get_all_config(self, mock_get_repo, client):
        """GET /config returns list of config dicts."""
        mock_repo = MagicMock()
        mock_repo.get_all.return_value = [
            {'key': 'test-key', 'value': 'test-value'},
        ]
        mock_get_repo.return_value = mock_repo

        resp = client.get('/api/v1/config')

        assert resp.status_code == 200
        assert 'application/json' in resp.headers['content-type']
        data = resp.json()
        assert isinstance(data, list)


class TestPatternsEndpoint:
    """Patterns endpoint contracts."""

    @patch('api.routes.patterns.get_repository')
    def test_contract_list_patterns(self, mock_get_repo, client):
        """GET /patterns returns list of pattern dicts."""
        mock_repo = MagicMock()
        mock_repo.get_all_patterns.return_value = [
            {
                'id': 1,
                'name': 'test-pattern',
                'match_type': 'substring',
                'error_text': 'test error',
                'category': 'build',
                'hit_count': 5,
            },
        ]
        mock_get_repo.return_value = mock_repo

        resp = client.get('/api/v1/patterns')

        assert resp.status_code == 200
        assert 'application/json' in resp.headers['content-type']
        data = resp.json()
        assert isinstance(data, list)


class TestSkillsEndpoint:
    """Skills endpoint contracts."""

    @patch('skills.db_registry.get_registry')
    def test_contract_list_skills(self, mock_get_registry, client):
        """GET /skills returns list of skill dicts."""
        mock_entry = MagicMock()
        mock_entry.qualified_name = 'source/skill-name'
        mock_entry.name = 'skill-name'
        mock_entry.source = 'source'
        mock_entry.status = 'active'
        mock_entry.tags = ['test']
        mock_entry.metadata.description = 'test skill'
        mock_entry.metadata.category = None
        mock_entry.metadata.allowed_tools = None
        mock_entry.metadata.user_invocable = True

        mock_registry = MagicMock()
        mock_registry.list_skills.return_value = [mock_entry]
        mock_get_registry.return_value = mock_registry

        resp = client.get('/api/v1/skills')

        assert resp.status_code == 200
        assert 'application/json' in resp.headers['content-type']
        data = resp.json()
        assert isinstance(data, list)


class TestErrorResponses:
    """Error response contracts."""

    @patch('api.routes.failures.get_repository')
    def test_contract_404_error(self, mock_get_repo, client):
        """404 errors return error, detail fields."""
        mock_repo = MagicMock()
        mock_repo.get_failure_details.return_value = None
        mock_get_repo.return_value = mock_repo

        resp = client.get('/api/v1/applications/test-app/failures/nonexistent')

        assert resp.status_code == 404
        assert 'application/json' in resp.headers['content-type']
        data = resp.json()
        assert 'error' in data
        assert 'detail' in data

    @patch('api.routes.violations.get_repository')
    def test_contract_validation_error(self, mock_get_repo, client):
        """Validation errors return 422 with error fields."""
        mock_conforma_repo = MagicMock()
        mock_triage_repo = MagicMock()
        mock_conforma_repo.get_violation_details.return_value = None

        def side_effect(repo_class):
            from repositories.conforma_repository import ConformaRepository
            from repositories.triage_repository import TriageRepository
            if repo_class == ConformaRepository:
                return mock_conforma_repo
            if repo_class == TriageRepository:
                return mock_triage_repo
            raise ValueError('Unknown repo')

        mock_get_repo.side_effect = side_effect

        resp = client.get('/api/v1/applications/test-app/violations/nonexistent')

        assert resp.status_code == 404
        assert 'application/json' in resp.headers['content-type']
        data = resp.json()
        assert 'error' in data
        assert 'detail' in data


class TestContentTypeHeaders:
    """Content-Type header contracts for all endpoints."""

    @patch('api.routes.health.get_pool')
    def test_health_content_type(self, mock_pool, client):
        """Health endpoint returns JSON content-type."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (1,)
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=None)
        mock_pool.return_value.connection.return_value = mock_conn

        resp = client.get('/health')
        assert 'application/json' in resp.headers['content-type']

    @patch('api.routes.applications.get_repository')
    def test_applications_content_type(self, mock_get_repo, client):
        """Applications endpoint returns JSON content-type."""
        mock_build_repo = MagicMock()
        mock_conforma_repo = MagicMock()
        mock_build_repo.get_applications.return_value = []
        mock_conforma_repo.find_unresolved_component_names.return_value = []

        def side_effect(repo_class):
            from repositories.build_failure_repository import BuildFailureRepository
            from repositories.conforma_repository import ConformaRepository
            if repo_class == BuildFailureRepository:
                return mock_build_repo
            if repo_class == ConformaRepository:
                return mock_conforma_repo
            raise ValueError('Unknown repo')

        mock_get_repo.side_effect = side_effect

        resp = client.get('/api/v1/applications')
        assert 'application/json' in resp.headers['content-type']


class TestListEndpoints:
    """Contracts for list endpoint pagination and structure."""

    @patch('api.routes.failures.get_repository')
    def test_list_failures_limit_param(self, mock_get_repo, client):
        """GET /failures respects limit parameter."""
        mock_repo = MagicMock()
        mock_repo.get_triage_summary.return_value = {
            'failing_components': [
                {
                    'component': f'comp-{i}',
                    'status': 'Failed',
                    'first_detected_at': datetime.utcnow(),
                    'last_updated_at': datetime.utcnow(),
                    'failure_count': 1,
                    'has_logs': True,
                    'ai_analyzed': False,
                }
                for i in range(100)
            ],
        }
        mock_get_repo.return_value = mock_repo

        resp = client.get('/api/v1/applications/test-app/failures?limit=10')

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) <= 10

    @patch('api.routes.violations.get_repository')
    def test_list_violations_rules_structure(self, mock_get_repo, client):
        """GET /violations/rules returns proper grouping structure."""
        mock_conforma_repo = MagicMock()
        mock_conforma_repo.get_violation_summaries.return_value = [
            {
                'component_name': 'comp1',
                'violation_summary': 'rule1: error\nrule2: error',
            },
            {
                'component_name': 'comp2',
                'violation_summary': 'rule1: error',
            },
        ]
        mock_get_repo.return_value = mock_conforma_repo

        with patch('utils.conforma_utils.count_unique_violations',
                  return_value=(2, [{'rule': 'rule1', 'detail': 'error'},
                                   {'rule': 'rule2', 'detail': 'error'}])):
            resp = client.get('/api/v1/applications/test-app/violations/rules')

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        if data:
            assert 'rule' in data[0]
            assert 'components' in data[0]
            assert 'count' in data[0]


class TestCRUDOperations:
    """Contracts for CRUD operation responses."""

    @patch('api.routes.config.get_repository')
    def test_create_returns_created_entity(self, mock_get_repo, client):
        """POST /config/applications returns created state."""
        mock_repo = MagicMock()
        mock_repo.add_watched_application.return_value = ['app1', 'new-app']
        mock_get_repo.return_value = mock_repo

        resp = client.post('/api/v1/config/applications',
                          json={'application': 'new-app'})

        assert resp.status_code == 200
        data = resp.json()
        assert 'new-app' in data['applications']

    @patch('api.routes.config.get_repository')
    def test_delete_returns_updated_state(self, mock_get_repo, client):
        """DELETE /config/applications/{app} returns updated list."""
        mock_repo = MagicMock()
        mock_repo.remove_watched_application.return_value = ['app1']
        mock_get_repo.return_value = mock_repo

        resp = client.delete('/api/v1/config/applications/removed-app')

        assert resp.status_code == 200
        data = resp.json()
        assert 'removed-app' not in data['applications']
