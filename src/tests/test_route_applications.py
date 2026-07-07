"""Tests for application endpoints."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import applications

app = FastAPI()
app.include_router(applications.router, prefix="/api/v1")
client = TestClient(app)


# --- list_applications ---


@patch("api.routes.applications._conforma_repo")
@patch("api.routes.applications._build_repo")
def test_list_applications_returns_counts(mock_build, mock_conforma):
    repo = MagicMock()
    repo.get_applications.return_value = [{"application": "rhoai-v2-19"}]
    repo.get_overview_stats.return_value = {"components": 12, "total": 3}
    mock_build.return_value = repo

    conforma = MagicMock()
    conforma.find_unresolved_component_names.return_value = ["comp-a", "comp-b"]
    mock_conforma.return_value = conforma

    response = client.get("/api/v1/applications")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "rhoai-v2-19"
    assert data[0]["component_count"] == 12
    assert data[0]["failure_count"] == 3
    assert data[0]["conforma_count"] == 2


@patch("api.routes.applications._conforma_repo")
@patch("api.routes.applications._build_repo")
def test_list_applications_empty(mock_build, mock_conforma):
    repo = MagicMock()
    repo.get_applications.return_value = []
    mock_build.return_value = repo
    mock_conforma.return_value = MagicMock()

    response = client.get("/api/v1/applications")

    assert response.status_code == 200
    assert response.json() == []


# --- get_stats ---


@patch("api.routes.applications._ai_repo")
def test_get_stats_returns_response(mock_ai):
    ai = MagicMock()
    ai.get_extended_status.return_value = {
        "build": {"pending": 5, "analyzed": 10, "auto_fixable": 2},
        "conforma": {"pending": 3, "analyzed": 7, "auto_fixable": 1},
        "total_cost": 1.25,
    }
    ai.get_recent_analyses.return_value = [{"id": 1, "component": "foo"}]
    mock_ai.return_value = ai

    response = client.get("/api/v1/applications/rhoai-v2-19/stats")

    assert response.status_code == 200
    data = response.json()
    assert data["application"] == "rhoai-v2-19"
    assert data["build_failures"] == {"pending": 5, "analyzed": 10, "autofixable": 2}
    assert data["conforma_violations"] == {"pending": 3, "analyzed": 7, "autofixable": 1}
    assert data["total_cost_30d"] == 1.25
    assert len(data["recent_analyses"]) == 1


# --- get_daily_stats ---


@patch("api.routes.applications._build_repo")
def test_get_daily_stats_default_days(mock_build):
    repo = MagicMock()
    repo.get_daily_stats.return_value = [{"date": "2026-07-01", "count": 4}]
    mock_build.return_value = repo

    response = client.get("/api/v1/applications/rhoai-v2-19/stats/daily")

    assert response.status_code == 200
    repo.get_daily_stats.assert_called_once_with("rhoai-v2-19", 7)
    assert response.json() == [{"date": "2026-07-01", "count": 4}]


@patch("api.routes.applications._build_repo")
def test_get_daily_stats_custom_days(mock_build):
    repo = MagicMock()
    repo.get_daily_stats.return_value = []
    mock_build.return_value = repo

    response = client.get("/api/v1/applications/rhoai-v2-19/stats/daily?days=14")

    assert response.status_code == 200
    repo.get_daily_stats.assert_called_once_with("rhoai-v2-19", 14)


# --- get_triage ---


@patch("api.routes.applications._build_repo")
def test_get_triage_returns_summary(mock_build):
    repo = MagicMock()
    repo.get_triage_summary.return_value = {
        "total": 20,
        "failing": 5,
        "working": 15,
        "failing_components": [{"component": "odh-dashboard", "error": "OOM"}],
    }
    mock_build.return_value = repo

    response = client.get("/api/v1/applications/rhoai-v2-19/triage-summary")

    assert response.status_code == 200
    data = response.json()
    assert data["application"] == "rhoai-v2-19"
    assert data["total"] == 20
    assert data["failing"] == 5
    assert data["working"] == 15
    assert len(data["failing_components"]) == 1


# --- get_dashboard ---


@patch("api.routes.applications.get_repository")
@patch("api.routes.applications._ai_repo")
@patch("api.routes.applications._build_repo")
def test_get_dashboard_returns_full_response(mock_build, mock_ai, mock_get_repo):
    build = MagicMock()
    build.get_overview_stats.return_value = {"components": 10, "total": 2}
    build.get_enrichment_coverage.return_value = {"covered": 8, "total": 10}
    mock_build.return_value = build

    ai = MagicMock()
    ai.get_extended_status.return_value = {"build": {}, "conforma": {}}
    ai.get_cost_summary.return_value = {"total": 5.0}
    mock_ai.return_value = ai

    pattern_repo = MagicMock()
    pattern_repo.get_library_summary.return_value = {"total_patterns": 42}
    resolution_repo = MagicMock()
    resolution_repo.get_outcome_summary.return_value = {"resolved": 10}

    def side_effect(repo_cls):
        if repo_cls.__name__ == "ErrorPatternRepository":
            return pattern_repo
        if repo_cls.__name__ == "ResolutionAttemptRepository":
            return resolution_repo
        return MagicMock()

    mock_get_repo.side_effect = side_effect

    response = client.get("/api/v1/applications/rhoai-v2-19/dashboard")

    assert response.status_code == 200
    data = response.json()
    assert data["application"] == "rhoai-v2-19"
    assert data["overview"]["components"] == 10
    assert data["overview"]["enrichment"] == {"covered": 8, "total": 10}
    assert data["patterns"] == {"total_patterns": 42}
    assert data["fixes"] == {"resolved": 10}


# --- get_health ---


@patch("api.routes.applications.get_pool")
@patch("proactive.health_monitor.HealthMonitor")
def test_get_health_returns_list(mock_monitor_cls, mock_pool):
    monitor = MagicMock()
    monitor.get_component_health_summary.return_value = [
        {"component": "odh-dashboard", "score": 0.9}
    ]
    mock_monitor_cls.return_value = monitor

    response = client.get("/api/v1/applications/rhoai-v2-19/health")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["component"] == "odh-dashboard"


@patch("api.routes.applications.get_pool")
@patch("proactive.health_monitor.HealthMonitor")
def test_get_health_empty(mock_monitor_cls, mock_pool):
    monitor = MagicMock()
    monitor.get_component_health_summary.return_value = None
    mock_monitor_cls.return_value = monitor

    response = client.get("/api/v1/applications/rhoai-v2-19/health")

    assert response.status_code == 200
    assert response.json() == []


# --- get_health_warnings ---


@patch("api.routes.applications.get_pool")
@patch("proactive.health_monitor.HealthMonitor")
def test_get_health_warnings_returns_list(mock_monitor_cls, mock_pool):
    warning = MagicMock()
    warning.signal_type = "degradation"
    warning.component_name = "odh-notebook-controller"
    warning.message = "Failure rate increasing"
    warning.severity = "warning"

    monitor = MagicMock()
    monitor.run_checks.return_value = [warning]
    mock_monitor_cls.return_value = monitor

    response = client.get("/api/v1/applications/rhoai-v2-19/health/warnings")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["type"] == "degradation"
    assert data[0]["component"] == "odh-notebook-controller"
    assert data[0]["message"] == "Failure rate increasing"
    assert data[0]["severity"] == "warning"
