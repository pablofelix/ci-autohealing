"""Tests for build failure endpoints."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.errors import register_error_handlers
from api.routes.failures import (
    _compute_age_signals,
    _compute_freeze_countdown,
    _detect_systemic_patterns,
    _detect_unmapped_upstream,
    router,
)


@pytest.fixture
def client():
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router, prefix="/api/v1")
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Helper: _compute_freeze_countdown
# ---------------------------------------------------------------------------

def test_freeze_countdown_none_input():
    assert _compute_freeze_countdown(None) is None


def test_freeze_countdown_empty_dict():
    assert _compute_freeze_countdown({}) is None


def test_freeze_countdown_all_none_values():
    sched = {"code_freeze_days": None, "initial_rc_days": None, "release_date_days": None}
    assert _compute_freeze_countdown(sched) is None


def test_freeze_countdown_pre_freeze_normal():
    sched = {"code_freeze_days": 10, "initial_rc_days": 20, "release_date_days": 30}
    result = _compute_freeze_countdown(sched)
    assert result.phase == "pre-freeze"
    assert result.urgency == "normal"
    assert result.code_freeze_days == 10
    assert "10 days to code freeze" in result.message


def test_freeze_countdown_frozen_phase():
    sched = {"code_freeze_days": -2, "initial_rc_days": 5, "release_date_days": 15}
    result = _compute_freeze_countdown(sched)
    assert result.phase == "frozen"
    assert "Code freeze ACTIVE" in result.message


def test_freeze_countdown_rc_phase():
    sched = {"code_freeze_days": -10, "initial_rc_days": -2, "release_date_days": 5}
    result = _compute_freeze_countdown(sched)
    assert result.phase == "rc"
    assert "RC phase" in result.message


def test_freeze_countdown_critical_urgency():
    sched = {"code_freeze_days": 2, "initial_rc_days": 10, "release_date_days": 20}
    result = _compute_freeze_countdown(sched)
    assert result.urgency == "critical"


def test_freeze_countdown_high_urgency():
    sched = {"code_freeze_days": 5, "initial_rc_days": 15, "release_date_days": 25}
    result = _compute_freeze_countdown(sched)
    assert result.urgency == "high"


def test_freeze_countdown_released_phase():
    sched = {"code_freeze_days": -30, "initial_rc_days": -15, "release_date_days": -5}
    result = _compute_freeze_countdown(sched)
    assert result.phase == "released"
    assert "GA was 5 days ago" in result.message


# ---------------------------------------------------------------------------
# Helper: _compute_age_signals
# ---------------------------------------------------------------------------

def test_age_signals_recent():
    now = datetime.utcnow()
    first = now - timedelta(hours=5)
    last = now - timedelta(hours=1)
    age_h, is_new, status_changed = _compute_age_signals(first, last)
    assert is_new is True
    assert age_h < 24
    assert status_changed is False


def test_age_signals_old():
    now = datetime.utcnow()
    first = now - timedelta(hours=48)
    last = now - timedelta(hours=6)
    age_h, is_new, status_changed = _compute_age_signals(first, last)
    assert is_new is False
    assert age_h > 24


def test_age_signals_none_inputs():
    age_h, is_new, status_changed = _compute_age_signals(None, None)
    assert age_h == 0
    assert is_new is True  # 0 <= 24
    assert status_changed is False


def test_age_signals_status_changed():
    """Old failure that was updated recently triggers status_changed."""
    now = datetime.utcnow()
    first = now - timedelta(hours=72)
    last = now - timedelta(hours=2)
    age_h, is_new, status_changed = _compute_age_signals(first, last)
    assert is_new is False
    assert status_changed is True


# ---------------------------------------------------------------------------
# Helper: _detect_unmapped_upstream
# ---------------------------------------------------------------------------

def _make_failure(component, error_type="build_error", possible_cause=None):
    return SimpleNamespace(
        component=component,
        error_type=error_type,
        possible_cause=possible_cause,
    )


def test_unmapped_upstream_prefix():
    failures = [_make_failure("upstream-model-mesh")]
    unmapped = _detect_unmapped_upstream(failures)
    assert "upstream-model-mesh" in unmapped
    assert "upstream sync component" in failures[0].possible_cause


def test_unmapped_upstream_suffix():
    failures = [_make_failure("kserve-upstream")]
    unmapped = _detect_unmapped_upstream(failures)
    assert "kserve-upstream" in unmapped
    assert "upstream sync component" in failures[0].possible_cause


def test_unmapped_normal_component():
    failures = [_make_failure("odh-dashboard")]
    unmapped = _detect_unmapped_upstream(failures)
    assert unmapped == []
    assert failures[0].possible_cause is None


def test_unmapped_preserves_existing_cause():
    failures = [_make_failure("upstream-codeflare", possible_cause="Go version mismatch")]
    _detect_unmapped_upstream(failures)
    assert "Go version mismatch" in failures[0].possible_cause
    assert "upstream sync component" in failures[0].possible_cause


# ---------------------------------------------------------------------------
# Helper: _detect_systemic_patterns
# ---------------------------------------------------------------------------

def test_systemic_two_components_same_pattern():
    failures = [
        _make_failure("comp-a", error_type="go version mismatch"),
        _make_failure("comp-b", error_type="go version 1.22 required"),
    ]
    _detect_systemic_patterns(failures)
    assert failures[0].possible_cause is not None
    assert "systemic" in failures[0].possible_cause
    assert "systemic" in failures[1].possible_cause


def test_systemic_single_component_not_tagged():
    failures = [_make_failure("comp-a", error_type="go version mismatch")]
    _detect_systemic_patterns(failures)
    assert failures[0].possible_cause is None


def test_systemic_different_patterns_not_tagged():
    failures = [
        _make_failure("comp-a", error_type="go version mismatch"),
        _make_failure("comp-b", error_type="npm err registry timeout"),
    ]
    _detect_systemic_patterns(failures)
    # Each only has one match, so no systemic tag
    assert failures[0].possible_cause is None
    assert failures[1].possible_cause is None


# ---------------------------------------------------------------------------
# Endpoint: GET /applications/{app}/failures
# ---------------------------------------------------------------------------

@patch("api.routes.failures._build_repo")
def test_list_failures_happy_path(mock_build, client):
    now = datetime.utcnow()
    repo = MagicMock()
    repo.get_triage_summary.return_value = {
        "failing_components": [
            {
                "component": "comp-a",
                "status": "Failed",
                "error_type": "build_error",
                "first_detected_at": now - timedelta(hours=2),
                "last_updated_at": now,
                "failure_count": 3,
                "has_logs": True,
                "ai_analyzed": True,
            },
            {
                "component": "comp-b",
                "status": "Failed",
                "error_type": "test_error",
                "first_detected_at": now - timedelta(hours=5),
                "last_updated_at": now,
                "failure_count": 1,
                "has_logs": False,
                "ai_analyzed": False,
            },
        ]
    }
    mock_build.return_value = repo

    resp = client.get("/api/v1/applications/rhoai-v2-19/failures")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["component"] in ("comp-a", "comp-b")


@patch("api.routes.failures._build_repo")
def test_list_failures_has_analysis_filter(mock_build, client):
    now = datetime.utcnow()
    repo = MagicMock()
    repo.get_triage_summary.return_value = {
        "failing_components": [
            {"component": "analyzed", "ai_analyzed": True,
             "first_detected_at": now, "last_updated_at": now},
            {"component": "not-analyzed", "ai_analyzed": False,
             "first_detected_at": now, "last_updated_at": now},
        ]
    }
    mock_build.return_value = repo

    resp = client.get("/api/v1/applications/rhoai-v2-19/failures?has_analysis=true")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["component"] == "analyzed"


@patch("api.routes.failures._build_repo")
def test_list_failures_empty(mock_build, client):
    repo = MagicMock()
    repo.get_triage_summary.return_value = {"failing_components": []}
    mock_build.return_value = repo

    resp = client.get("/api/v1/applications/rhoai-v2-19/failures")
    assert resp.status_code == 200
    assert resp.json() == []


@patch("api.routes.failures._build_repo")
def test_list_failures_limit(mock_build, client):
    now = datetime.utcnow()
    repo = MagicMock()
    repo.get_triage_summary.return_value = {
        "failing_components": [
            {"component": f"comp-{i}", "first_detected_at": now,
             "last_updated_at": now, "ai_analyzed": False}
            for i in range(10)
        ]
    }
    mock_build.return_value = repo

    resp = client.get("/api/v1/applications/rhoai-v2-19/failures?limit=3")
    assert resp.status_code == 200
    assert len(resp.json()) == 3


# ---------------------------------------------------------------------------
# Endpoint: GET /applications/{app}/failures/{component}
# ---------------------------------------------------------------------------

@patch("api.routes.failures._build_repo")
def test_get_failure_happy_path(mock_build, client):
    now = datetime.utcnow()
    repo = MagicMock()
    repo.get_failure_details.return_value = {
        "component_name": "comp-a",
        "pipelinerun_name": "pr-123",
        "status": "Failed",
        "error_message": "build failed",
        "first_detected_at": now,
    }
    repo.get_component_history.return_value = {"builds": []}
    mock_build.return_value = repo

    resp = client.get("/api/v1/applications/rhoai-v2-19/failures/comp-a")
    assert resp.status_code == 200
    data = resp.json()
    assert data["component"] == "comp-a"
    assert data["status"] == "Failed"


@patch("api.routes.failures._build_repo")
def test_get_failure_not_found(mock_build, client):
    repo = MagicMock()
    repo.get_failure_details.return_value = None
    mock_build.return_value = repo

    resp = client.get("/api/v1/applications/rhoai-v2-19/failures/nonexistent")
    assert resp.status_code == 404
    assert "not_found" in resp.json().get("error", "")


# ---------------------------------------------------------------------------
# Endpoint: GET /applications/{app}/failures/{component}/history
# ---------------------------------------------------------------------------

@patch("api.routes.failures._build_repo")
def test_get_component_history(mock_build, client):
    repo = MagicMock()
    repo.get_component_history.return_value = {
        "summary": {"total": 5, "pass": 3, "fail": 2},
        "builds": [{"status": "Failed"}, {"status": "Succeeded"}],
    }
    mock_build.return_value = repo

    resp = client.get("/api/v1/applications/rhoai-v2-19/failures/comp-a/history")
    assert resp.status_code == 200
    data = resp.json()
    assert data["component"] == "comp-a"
    assert data["application"] == "rhoai-v2-19"
    assert len(data["builds"]) == 2


# ---------------------------------------------------------------------------
# Endpoint: GET /applications/{app}/working
# ---------------------------------------------------------------------------

@patch("api.routes.failures._build_repo")
def test_get_working(mock_build, client):
    repo = MagicMock()
    repo.get_working_components.return_value = [
        {"component": "comp-a", "status": "Succeeded"},
    ]
    mock_build.return_value = repo

    resp = client.get("/api/v1/applications/rhoai-v2-19/working")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["component"] == "comp-a"


# ---------------------------------------------------------------------------
# Endpoint: GET /applications/{app}/resolved
# ---------------------------------------------------------------------------

@patch("api.routes.failures._build_repo")
def test_get_resolved(mock_build, client):
    repo = MagicMock()
    repo.get_resolved_components.return_value = [
        {"component": "comp-b", "resolved_at": "2025-01-01T00:00:00"},
    ]
    mock_build.return_value = repo

    resp = client.get("/api/v1/applications/rhoai-v2-19/resolved")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["component"] == "comp-b"


# ---------------------------------------------------------------------------
# Endpoint: GET /lookup?image=...
# ---------------------------------------------------------------------------

@patch("api.routes.failures._build_repo")
def test_lookup_image_db_match(mock_build, client):
    repo = MagicMock()
    repo.find_by_image.return_value = [
        {"component": "comp-a", "output_image": "sha256:abc123"},
    ]
    mock_build.return_value = repo

    resp = client.get("/api/v1/lookup?image=sha256:abc123")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["db_matches"]) == 1
    assert data["query"] == "sha256:abc123"
    assert data["total_matches"] >= 1


@patch("api.routes.failures._build_repo")
def test_lookup_image_no_matches(mock_build, client):
    repo = MagicMock()
    repo.find_by_image.return_value = []
    mock_build.return_value = repo

    resp = client.get("/api/v1/lookup?image=sha256:doesnotexist")
    assert resp.status_code == 200
    data = resp.json()
    assert data["db_matches"] == []
    assert data["total_matches"] == 0


# ---------------------------------------------------------------------------
# Endpoint: GET /applications/{app}/blockers  (Jira unavailable path)
# ---------------------------------------------------------------------------

@patch("api.routes.failures._build_repo")
def test_list_blockers_jira_unavailable(mock_build, client):
    """When JiraClient import/init fails, return empty with signal message."""
    mock_build.return_value = MagicMock()

    with (patch.dict(os.environ, {"JIRA_EMAIL": "", "JIRA_TOKEN": ""}),
          patch("clients.jira_client.JiraClient", side_effect=Exception("no jira"))):
        resp = client.get("/api/v1/applications/rhoai-v2-19/blockers")

    # The endpoint catches the exception internally and returns a fallback
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_blockers"] == 0
    assert any("unavailable" in s.lower() for s in data["critical_signals"])


# ---------------------------------------------------------------------------
# Endpoint: GET /jira/health  (unreachable path)
# ---------------------------------------------------------------------------

def test_jira_health_unreachable(client):
    """When JiraClient fails, return unreachable status."""
    with patch.dict(os.environ, {"JIRA_EMAIL": "", "JIRA_TOKEN": ""}), patch(
        "clients.jira_client.JiraClient",
        side_effect=Exception("connection refused"),
    ):
        resp = client.get("/api/v1/jira/health")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "unreachable"
    assert "connection refused" in data["message"]


# ---------------------------------------------------------------------------
# Endpoint: GET /applications/{app}/alerts  (minimal smoke test)
# ---------------------------------------------------------------------------

@patch("api.routes.failures.check_jira_health")
@patch("api.routes.failures.list_blockers")
@patch("api.routes.failures._triage_repo")
@patch("api.routes.failures._conforma_repo")
@patch("api.routes.failures._build_repo")
def test_list_alerts_minimal(
    mock_build, mock_conforma, mock_triage,
    mock_list_blockers, mock_jira_health, client,
):
    """Smoke test: alerts endpoint returns 200 with mocked dependencies."""
    now = datetime.utcnow()

    build_repo = MagicMock()
    build_repo.get_applications.return_value = [{'application': 'rhoai-v2-19'}]
    build_repo.get_triage_summary.return_value = {
        "failing_components": [
            {
                "component": "comp-a",
                "status": "Failed",
                "first_detected_at": now,
                "last_updated_at": now,
                "failure_count": 1,
                "has_logs": True,
                "ai_analyzed": False,
            }
        ]
    }
    mock_build.return_value = build_repo

    conforma_repo = MagicMock()
    conforma_repo.get_violation_summaries.return_value = []
    mock_conforma.return_value = conforma_repo

    triage_repo = MagicMock()
    triage_repo.build_jira_map.return_value = {}
    mock_triage.return_value = triage_repo

    mock_jira_health.return_value = MagicMock(status="valid")
    mock_list_blockers.return_value = MagicMock(critical_signals=[])

    with (patch("api.validators.validate_application_name", side_effect=lambda x: x),
          patch("conforma.policy_tools.fetch_exceptions_by_policy", return_value={})):
        resp = client.get("/api/v1/applications/rhoai-v2-19/alerts")

    assert resp.status_code == 200
    data = resp.json()
    assert data["application"] == "rhoai-v2-19"
    assert data["total_count"] >= 1
    assert len(data["build_failures"]) == 1
