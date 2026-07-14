"""Verify endpoints return useful errors instead of 500 on dependency failure."""
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.errors import register_error_handlers


def _make_app():
    app = FastAPI()
    register_error_handlers(app)
    from api.routes import applications, violations
    app.include_router(applications.router, prefix="/api/v1")
    app.include_router(violations.router, prefix="/api/v1")
    return app


@pytest.fixture
def client():
    return TestClient(_make_app())


@patch("api.routes.applications.get_pool")
def test_health_returns_error_on_db_failure(mock_pool, client):
    """Health endpoint should return 500 with error body on DB failure."""
    mock_pool.return_value = MagicMock()
    with patch("proactive.health_monitor.HealthMonitor.get_component_health_summary",
               side_effect=Exception("DB connection refused")):
        resp = client.get("/api/v1/applications/rhoai-v3-5/health")
    assert resp.status_code == 500
    body = resp.json()
    assert "error" in body or "detail" in body
    # Should have useful error message, not generic 500
    if "detail" in body:
        assert "DB connection refused" in str(body["detail"]) or "Health check failed" in str(body["detail"])


@patch("api.routes.applications._build_repo")
def test_dashboard_returns_error_on_db_failure(mock_build_repo, client):
    """Dashboard endpoint should return 500 with error body on DB failure."""
    # Make the _build_repo() call fail with DB error
    mock_build_repo.side_effect = Exception("DB pool exhausted")
    resp = client.get("/api/v1/applications/rhoai-v3-5/dashboard")
    assert resp.status_code == 500
    body = resp.json()
    assert "error" in body or "detail" in body
    # Should have useful error message
    if "detail" in body:
        assert "DB pool exhausted" in str(body["detail"]) or "Dashboard data unavailable" in str(body["detail"])


@patch("conforma.policy_tools.fetch_exceptions_by_policy")
@patch("api.routes.violations._conforma_repo")
def test_violations_report_degrades_gracefully_on_k8s_failure(mock_repo, mock_fetch, client):
    """Violations report should proceed without exception data when K8s API fails."""
    # Mock the DB repo to return violations
    mock_repo_instance = MagicMock()
    mock_repo_instance.get_violation_summaries.return_value = [
        {
            'component_name': 'test-component',
            'scenario': 'verify-ec-policy',
            'violation_summary': 'rule1: violation\nrule2: violation',
            'violations_count': 2,
            'warnings_count': 0,
            'successes_count': 0,
        }
    ]
    mock_repo.return_value = mock_repo_instance

    # Make K8s API fail
    mock_fetch.side_effect = Exception("K8s API unreachable")

    resp = client.get("/api/v1/applications/rhoai-v3-5/violations/report")

    # Should succeed with degraded data (no exception coverage)
    assert resp.status_code == 200
    body = resp.json()
    assert body['application'] == 'rhoai-v3-5'
    assert body['total_violations'] >= 0
    # coverage_summary should be None when exceptions fetch fails
    assert body.get('coverage_summary') is None
    # Violations should still be present
    assert 'violations' in body


@patch("conforma.policy_tools.fetch_exceptions_by_policy")
@patch("api.routes.violations._conforma_repo")
def test_violations_list_degrades_gracefully_on_k8s_failure(mock_repo, mock_fetch, client):
    """Violations list should proceed without exception data when K8s API fails."""
    mock_repo_instance = MagicMock()
    mock_repo_instance.get_violation_summaries.return_value = [
        {
            'component_name': 'test-component',
            'scenario': 'verify-ec-policy',
            'violation_summary': 'rule1: violation',
            'violations_count': 1,
            'warnings_count': 0,
            'successes_count': 0,
        }
    ]
    mock_repo.return_value = mock_repo_instance

    # Make K8s API fail
    mock_fetch.side_effect = Exception("K8s API unreachable")

    resp = client.get("/api/v1/applications/rhoai-v3-5/violations")

    # Should succeed with degraded data
    assert resp.status_code == 200
    violations = resp.json()
    assert isinstance(violations, list)
    assert len(violations) > 0
    # Exception coverage fields should not be present or should be None
    v = violations[0]
    assert v.get('exception_coverage') in (None, 'not_covered')
