"""Tests for api.routes.releases — freeze calendar, schedule, and readiness checks."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.errors import register_error_handlers
from api.routes.releases import (
    _build_manual_checks,
    _check_blockers,
    _check_fbc_health,
    _check_gha_nightly,
    _check_integration_tests,
    _check_jira_health,
    _check_nudge_propagation,
    _check_pcc,
    _check_policy_consistency,
    _check_snapshot_completeness,
    _check_snapshot_freshness,
    _check_stale_components,
    _derive_branch,
    router,
)


@pytest.fixture
def client():
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router, prefix="/api/v1")
    return TestClient(app, raise_server_exceptions=False)


# ───────────────────────────────────────────────────────────────────────────
# Pure Function Tests: _derive_branch
# ───────────────────────────────────────────────────────────────────────────


def test_derive_branch_ea_version():
    """Test EA version conversion (rhoai-v3-5-ea-2 → rhoai-3.5-ea.2)."""
    assert _derive_branch("rhoai-v3-5-ea-2") == "rhoai-3.5-ea.2"


def test_derive_branch_ga_version():
    """Test GA version conversion (rhoai-v3-5 → rhoai-3.5)."""
    assert _derive_branch("rhoai-v3-5") == "rhoai-3.5"


def test_derive_branch_multi_digit():
    """Test multi-digit versions (rhoai-v10-12-ea-3 → rhoai-10.12-ea.3)."""
    assert _derive_branch("rhoai-v10-12-ea-3") == "rhoai-10.12-ea.3"


def test_derive_branch_no_match():
    """Test non-matching pattern returns as-is."""
    assert _derive_branch("something-else") == "something-else"


# ───────────────────────────────────────────────────────────────────────────
# Check Function Tests: _check_fbc_health
# ───────────────────────────────────────────────────────────────────────────


def test_check_fbc_health_k8s_unreachable():
    """Test FBC health when K8s cluster is unreachable."""
    monitor = MagicMock()
    result = _check_fbc_health(monitor, "rhoai-v3-5", k8s_reachable=False)

    assert result["name"] == "FBC fragment health"
    assert result["phase"] == "pre-release"
    assert result["status"] == "SKIP"
    assert "unreachable" in result["detail"].lower()


def test_check_fbc_health_no_data():
    """Test FBC health when monitor returns no FBC data."""
    monitor = MagicMock()
    monitor.get_nightly_status.return_value = {"fbc_health": None}

    result = _check_fbc_health(monitor, "rhoai-v3-5", k8s_reachable=True)

    assert result["status"] == "WARN"
    assert "No health data" in result["detail"]


def test_check_fbc_health_failed():
    """Test FBC health when build failed."""
    monitor = MagicMock()
    monitor.get_nightly_status.return_value = {
        "fbc_health": {"current_status": "Failed"},
        "fbc_component": "odh-fbc-fragment",
    }

    result = _check_fbc_health(monitor, "rhoai-v3-5", k8s_reachable=True)

    assert result["status"] == "FAIL"
    assert "failed" in result["detail"].lower()


def test_check_fbc_health_pass():
    """Test FBC health when build is healthy."""
    monitor = MagicMock()
    monitor.get_nightly_status.return_value = {
        "fbc_health": {"current_status": "Succeeded", "health_score": 100},
    }

    result = _check_fbc_health(monitor, "rhoai-v3-5", k8s_reachable=True)

    assert result["status"] == "PASS"
    assert "healthy" in result["detail"].lower()


def test_check_fbc_health_exception():
    """Test FBC health when monitor raises exception."""
    monitor = MagicMock()
    monitor.get_nightly_status.side_effect = Exception("Connection error")

    result = _check_fbc_health(monitor, "rhoai-v3-5", k8s_reachable=True)

    assert result["status"] == "SKIP"
    assert "Check failed" in result["detail"]


# ───────────────────────────────────────────────────────────────────────────
# Check Function Tests: _check_pcc
# ───────────────────────────────────────────────────────────────────────────


def test_check_pcc_skip():
    """Test PCC check when no GitHub token."""
    monitor = MagicMock()
    monitor._check_pcc_freshness.return_value = None

    result = _check_pcc(monitor)

    assert result["status"] == "SKIP"
    assert "github token" in result["detail"].lower()


def test_check_pcc_stale():
    """Test PCC check when cache is stale."""
    monitor = MagicMock()
    monitor._check_pcc_freshness.return_value = {
        "status": "stale",
        "missing_versions": ["v1.2.3", "v1.2.4"],
    }

    result = _check_pcc(monitor)

    assert result["status"] == "FAIL"
    assert "2 version(s)" in result["detail"]
    assert "v1.2.3" in result["detail"]


def test_check_pcc_fresh():
    """Test PCC check when cache is fresh."""
    monitor = MagicMock()
    monitor._check_pcc_freshness.return_value = {
        "status": "fresh",
        "cached_versions": 5,
    }

    result = _check_pcc(monitor)

    assert result["status"] == "PASS"
    assert "5 versions" in result["detail"]


def test_check_pcc_exception():
    """Test PCC check when exception occurs."""
    monitor = MagicMock()
    monitor._check_pcc_freshness.side_effect = RuntimeError("API error")

    result = _check_pcc(monitor)

    assert result["status"] == "SKIP"


# ───────────────────────────────────────────────────────────────────────────
# Check Function Tests: _check_gha_nightly
# ───────────────────────────────────────────────────────────────────────────


def test_check_gha_nightly_no_data():
    """Test GHA nightly when no workflow data available."""
    monitor = MagicMock()
    monitor.get_nightly_status.return_value = {"gha_validation": None}

    result = _check_gha_nightly(monitor, "rhoai-v3-5")

    assert result["status"] == "SKIP"
    assert "No GHA workflow" in result["detail"]


def test_check_gha_nightly_failure():
    """Test GHA nightly when workflow failed."""
    monitor = MagicMock()
    monitor.get_nightly_status.return_value = {
        "gha_validation": {
            "conclusion": "failure",
            "created_at": "2026-07-01T10:00:00Z",
            "url": "https://github.com/...",
        }
    }

    result = _check_gha_nightly(monitor, "rhoai-v3-5")

    assert result["status"] == "WARN"
    assert "failed" in result["detail"].lower()


def test_check_gha_nightly_success():
    """Test GHA nightly when workflow passed."""
    monitor = MagicMock()
    monitor.get_nightly_status.return_value = {
        "gha_validation": {
            "conclusion": "success",
            "created_at": "2026-07-01T10:00:00Z",
        }
    }

    result = _check_gha_nightly(monitor, "rhoai-v3-5")

    assert result["status"] == "PASS"
    assert "passed" in result["detail"].lower()


# ───────────────────────────────────────────────────────────────────────────
# Check Function Tests: _check_stale_components
# ───────────────────────────────────────────────────────────────────────────


def test_check_stale_components_k8s_unreachable():
    """Test stale components check when K8s unreachable."""
    monitor = MagicMock()
    result = _check_stale_components(monitor, "rhoai-v3-5", k8s_reachable=False)

    assert result["status"] == "SKIP"
    assert "unreachable" in result["detail"].lower()


def test_check_stale_components_none_stale():
    """Test stale components check when no stale components."""
    monitor = MagicMock()
    monitor.get_stale_components.return_value = {
        "stale_count": 0,
        "checked": 10,
    }

    result = _check_stale_components(monitor, "rhoai-v3-5", k8s_reachable=True)

    assert result["status"] == "PASS"
    assert "0 components" in result["detail"]


def test_check_stale_components_some_stale():
    """Test stale components check when some are stale."""
    monitor = MagicMock()
    monitor.get_stale_components.return_value = {
        "stale_count": 3,
        "stale": [
            {"component": "comp1"},
            {"component": "comp2"},
            {"component": "comp3"},
        ],
    }

    result = _check_stale_components(monitor, "rhoai-v3-5", k8s_reachable=True)

    assert result["status"] == "WARN"
    assert "3 component(s)" in result["detail"]
    assert "comp1" in result["detail"]


# ───────────────────────────────────────────────────────────────────────────
# Check Function Tests: _check_snapshot_freshness
# ───────────────────────────────────────────────────────────────────────────


def test_check_snapshot_freshness_k8s_unreachable():
    """Test snapshot freshness when K8s unreachable."""
    monitor = MagicMock()
    result = _check_snapshot_freshness(monitor, "rhoai-v3-5", k8s_reachable=False)

    assert result["status"] == "SKIP"


def test_check_snapshot_freshness_all_fresh():
    """Test snapshot freshness when all components fresh."""
    monitor = MagicMock()
    monitor.check_snapshot_freshness.return_value = {
        "stale_count": 0,
        "fresh_count": 10,
    }

    result = _check_snapshot_freshness(monitor, "rhoai-v3-5", k8s_reachable=True)

    assert result["status"] == "PASS"
    assert "10 components" in result["detail"]


def test_check_snapshot_freshness_some_stale():
    """Test snapshot freshness when some components stale."""
    monitor = MagicMock()
    monitor.check_snapshot_freshness.return_value = {
        "stale_count": 2,
        "stale": [{"component": "c1"}, {"component": "c2"}],
    }

    result = _check_snapshot_freshness(monitor, "rhoai-v3-5", k8s_reachable=True)

    assert result["status"] == "WARN"
    assert "2 component(s)" in result["detail"]


# ───────────────────────────────────────────────────────────────────────────
# Check Function Tests: _check_snapshot_completeness
# ───────────────────────────────────────────────────────────────────────────


def test_check_snapshot_completeness_no_k8s():
    """Test snapshot completeness when k8s is None."""
    result = _check_snapshot_completeness(None, {}, "rhoai-v3-5")

    assert result["status"] == "SKIP"
    assert "not available" in result["detail"]


def test_check_snapshot_completeness_all_present():
    """Test snapshot completeness when all components present."""
    k8s = MagicMock()
    k8s.list_components.return_value = [
        {"name": "comp1"},
        {"name": "comp2"},
    ]

    snapshot = {
        "spec": {
            "components": [
                {"name": "comp1"},
                {"name": "comp2"},
            ]
        }
    }

    result = _check_snapshot_completeness(k8s, snapshot, "rhoai-v3-5")

    assert result["status"] == "PASS"
    assert "all 2 application components" in result["detail"].lower()


def test_check_snapshot_completeness_missing():
    """Test snapshot completeness when components missing."""
    k8s = MagicMock()
    k8s.list_components.return_value = [
        {"name": "comp1"},
        {"name": "comp2"},
        {"name": "comp3"},
    ]

    snapshot = {
        "spec": {
            "components": [
                {"name": "comp1"},
            ]
        }
    }

    result = _check_snapshot_completeness(k8s, snapshot, "rhoai-v3-5")

    assert result["status"] == "WARN"
    assert "2 component(s) missing" in result["detail"]


# ───────────────────────────────────────────────────────────────────────────
# Check Function Tests: _check_policy_consistency
# ───────────────────────────────────────────────────────────────────────────


@patch("clients.konflux_client.KonfluxClient")
def test_check_policy_consistency_no_rpas(mock_kfx_class):
    """Test policy consistency when no RPAs."""
    result = _check_policy_consistency([])

    assert result["status"] == "SKIP"
    assert "No RPAs" in result["detail"]


@patch("clients.konflux_client.KonfluxClient")
def test_check_policy_consistency_same_policy(mock_kfx_class):
    """Test policy consistency when stage and prod use same policy."""
    mock_kfx_class.extract_rpa_bindings.return_value = [
        {"target": "stage", "policy": "rhoai-policy"},
        {"target": "prod", "policy": "rhoai-policy"},
    ]

    result = _check_policy_consistency([{"dummy": "rpa"}])

    assert result["status"] == "PASS"
    assert "rhoai-policy" in result["detail"]


@patch("clients.konflux_client.KonfluxClient")
def test_check_policy_consistency_different_policy(mock_kfx_class):
    """Test policy consistency when stage and prod differ."""
    mock_kfx_class.extract_rpa_bindings.return_value = [
        {"target": "stage", "policy": "rhoai-stage-policy"},
        {"target": "prod", "policy": "rhoai-prod-policy"},
    ]

    result = _check_policy_consistency([{"dummy": "rpa"}])

    assert result["status"] == "WARN"
    assert "differs" in result["detail"]


@patch("clients.konflux_client.KonfluxClient")
def test_check_policy_consistency_missing_stage(mock_kfx_class):
    """Test policy consistency when stage RPA missing."""
    mock_kfx_class.extract_rpa_bindings.return_value = [
        {"target": "prod", "policy": "rhoai-policy"},
    ]

    result = _check_policy_consistency([{"dummy": "rpa"}])

    assert result["status"] == "SKIP"
    assert "Missing" in result["detail"]


# ───────────────────────────────────────────────────────────────────────────
# Check Function Tests: _check_integration_tests
# ───────────────────────────────────────────────────────────────────────────


@patch("clients.konflux_client.KonfluxClient")
def test_check_integration_tests_no_snapshot(mock_kfx_class):
    """Test integration tests when no snapshot."""
    result = _check_integration_tests(None)

    assert result["status"] == "SKIP"
    assert "No snapshot" in result["detail"]


@patch("clients.konflux_client.KonfluxClient")
def test_check_integration_tests_no_results(mock_kfx_class):
    """Test integration tests when no test results."""
    mock_kfx_class.extract_snapshot_status.return_value = {
        "test_results": {}
    }

    result = _check_integration_tests({"dummy": "snapshot"})

    assert result["status"] == "SKIP"
    assert "No test results" in result["detail"]


@patch("clients.konflux_client.KonfluxClient")
def test_check_integration_tests_all_passing(mock_kfx_class):
    """Test integration tests when all tests pass."""
    mock_kfx_class.extract_snapshot_status.return_value = {
        "test_results": {
            "test1": {"status": "True"},
            "test2": {"status": "True"},
        }
    }

    result = _check_integration_tests({"dummy": "snapshot"})

    assert result["status"] == "PASS"
    assert "All 2 integration tests passed" in result["detail"]


@patch("clients.konflux_client.KonfluxClient")
def test_check_integration_tests_some_failing(mock_kfx_class):
    """Test integration tests when some fail."""
    mock_kfx_class.extract_snapshot_status.return_value = {
        "test_results": {
            "test1": {"status": "True"},
            "test2": {"status": "False"},
            "test3": {"status": "False"},
        }
    }

    result = _check_integration_tests({"dummy": "snapshot"})

    assert result["status"] == "FAIL"
    assert "2 test(s) not passing" in result["detail"]


# ───────────────────────────────────────────────────────────────────────────
# Check Function Tests: _check_nudge_propagation
# ───────────────────────────────────────────────────────────────────────────


def test_check_nudge_propagation_no_token():
    """Test nudge propagation when no GitHub token."""
    with patch.dict(os.environ, {}, clear=True):
        result = _check_nudge_propagation("rhoai-v3-5")

        assert result["status"] == "SKIP"
        assert "No GITHUB_TOKEN" in result["detail"]


@patch("clients.github_client.GitHubClient")
def test_check_nudge_propagation_no_prs(mock_gh_class):
    """Test nudge propagation when no open nudge PRs."""
    mock_gh = MagicMock()
    mock_gh_class.return_value = mock_gh
    mock_gh.list_pull_requests.return_value = []

    with patch.dict(os.environ, {"GITHUB_TOKEN": "test"}):
        result = _check_nudge_propagation("rhoai-v3-5")

        assert result["status"] == "PASS"
        assert "No open nudge PRs" in result["detail"]


@patch("clients.github_client.GitHubClient")
def test_check_nudge_propagation_recent_prs(mock_gh_class):
    """Test nudge propagation when nudge PRs are recent."""
    mock_gh = MagicMock()
    mock_gh_class.return_value = mock_gh

    # Recent PR (< 48h)
    recent_time = datetime.utcnow() - timedelta(hours=24)
    mock_gh.list_pull_requests.return_value = [
        {
            "title": "Nudge component version",
            "created_at": recent_time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
    ]

    with patch.dict(os.environ, {"GITHUB_TOKEN": "test"}):
        result = _check_nudge_propagation("rhoai-v3-5")

        assert result["status"] == "PASS"
        assert "all recent" in result["detail"].lower()


@patch("clients.github_client.GitHubClient")
def test_check_nudge_propagation_stale_prs(mock_gh_class):
    """Test nudge propagation when nudge PRs are stale."""
    mock_gh = MagicMock()
    mock_gh_class.return_value = mock_gh

    # Stale PR (> 48h)
    stale_time = datetime.utcnow() - timedelta(hours=72)
    mock_gh.list_pull_requests.return_value = [
        {
            "title": "Nudge component version",
            "created_at": stale_time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
    ]

    with patch.dict(os.environ, {"GITHUB_TOKEN": "test"}):
        result = _check_nudge_propagation("rhoai-v3-5")

        assert result["status"] == "WARN"
        assert "1 nudge PR(s) open >48h" in result["detail"]


# ───────────────────────────────────────────────────────────────────────────
# Helper Tests: _build_manual_checks
# ───────────────────────────────────────────────────────────────────────────


def test_build_manual_checks_structure():
    """Test that _build_manual_checks returns expected structure."""
    with patch.dict(os.environ, {"NAMESPACE": "rhoai"}):
        result = _build_manual_checks("rhoai-v3-5")

        assert "pre_release" in result
        assert "post_stage" in result
        assert isinstance(result["pre_release"], list)
        assert isinstance(result["post_stage"], list)
        assert len(result["pre_release"]) > 0
        assert len(result["post_stage"]) > 0


def test_build_manual_checks_content():
    """Test that _build_manual_checks includes expected commands."""
    with patch.dict(os.environ, {"NAMESPACE": "rhoai"}):
        result = _build_manual_checks("rhoai-v3-5")

        # Check pre-release commands
        pre_names = [c["name"] for c in result["pre_release"]]
        assert "IC readiness check" in pre_names
        assert "Check snapshot components" in pre_names

        # Check post-stage commands
        post_names = [c["name"] for c in result["post_stage"]]
        assert "Verify stage release completed" in post_names
        assert "Verify prod RPA exists" in post_names


# ───────────────────────────────────────────────────────────────────────────
# Endpoint Tests: Freeze Calendar
# ───────────────────────────────────────────────────────────────────────────


@patch("api.routes.releases._db")
def test_list_freezes_empty(mock_db, client):
    """Test GET /freezes with no freezes."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = []
    mock_conn.cursor.return_value = mock_cursor
    mock_db.return_value.connection.return_value.__enter__.return_value = mock_conn

    response = client.get("/api/v1/freezes")

    assert response.status_code == 200
    assert response.json() == []


@patch("api.routes.releases._db")
@patch("api.routes.releases.date")
def test_list_freezes_with_data(mock_date, mock_db, client):
    """Test GET /freezes with multiple freezes."""
    mock_date.today.return_value = date(2026, 7, 7)

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [
        (1, date(2026, 7, 1), date(2026, 7, 5), "Past freeze"),
        (2, date(2026, 7, 5), date(2026, 7, 10), "Active freeze"),
        (3, date(2026, 7, 15), date(2026, 7, 20), "Future freeze"),
    ]
    mock_conn.cursor.return_value = mock_cursor
    mock_db.return_value.connection.return_value.__enter__.return_value = mock_conn

    response = client.get("/api/v1/freezes")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 3
    assert data[0]["status"] == "past"
    assert data[1]["status"] == "active"
    assert data[2]["status"] == "upcoming"


@patch("api.routes.releases._db")
def test_get_active_freeze_none(mock_db, client):
    """Test GET /freezes/active with no active freeze."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = None
    mock_conn.cursor.return_value = mock_cursor
    mock_db.return_value.connection.return_value.__enter__.return_value = mock_conn

    response = client.get("/api/v1/freezes/active")

    assert response.status_code == 200
    assert response.json() is None


@patch("api.routes.releases._db")
def test_get_active_freeze_exists(mock_db, client):
    """Test GET /freezes/active with active freeze."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = (
        1,
        date(2026, 7, 5),
        date(2026, 7, 10),
        "Code freeze",
    )
    mock_conn.cursor.return_value = mock_cursor
    mock_db.return_value.connection.return_value.__enter__.return_value = mock_conn

    response = client.get("/api/v1/freezes/active")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == 1
    assert data["status"] == "active"
    assert data["reason"] == "Code freeze"


@patch("api.routes.releases._db")
def test_add_freeze_valid(mock_db, client):
    """Test POST /freezes with valid data."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = (1,)
    mock_conn.cursor.return_value = mock_cursor
    mock_db.return_value.connection.return_value.__enter__.return_value = mock_conn

    response = client.post(
        "/api/v1/freezes",
        json={
            "start_date": "2026-07-15",
            "end_date": "2026-07-20",
            "reason": "Feature freeze",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == 1
    assert data["start_date"] == "2026-07-15"
    assert data["end_date"] == "2026-07-20"


def test_add_freeze_invalid_date_format(client):
    """Test POST /freezes with invalid date format."""
    response = client.post(
        "/api/v1/freezes",
        json={
            "start_date": "2026/07/15",
            "end_date": "2026-07-20",
            "reason": "Feature freeze",
        },
    )

    assert response.status_code == 400
    assert "Invalid date format" in response.json()["detail"]


def test_add_freeze_end_before_start(client):
    """Test POST /freezes with end_date before start_date."""
    response = client.post(
        "/api/v1/freezes",
        json={
            "start_date": "2026-07-20",
            "end_date": "2026-07-15",
            "reason": "Feature freeze",
        },
    )

    assert response.status_code == 400
    assert "end_date must be >= start_date" in response.json()["detail"]


@patch("api.routes.releases._db")
def test_remove_freeze_found(mock_db, client):
    """Test DELETE /freezes/{id} when freeze exists."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.rowcount = 1
    mock_conn.cursor.return_value = mock_cursor
    mock_db.return_value.connection.return_value.__enter__.return_value = mock_conn

    response = client.delete("/api/v1/freezes/1")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "deleted"
    assert data["id"] == "1"


@patch("api.routes.releases._db")
def test_remove_freeze_not_found(mock_db, client):
    """Test DELETE /freezes/{id} when freeze doesn't exist."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.rowcount = 0
    mock_conn.cursor.return_value = mock_cursor
    mock_db.return_value.connection.return_value.__enter__.return_value = mock_conn

    response = client.delete("/api/v1/freezes/999")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


# ───────────────────────────────────────────────────────────────────────────
# Endpoint Tests: Schedule
# ───────────────────────────────────────────────────────────────────────────


@patch("api.routes.releases._db")
def test_get_schedule_not_found(mock_db, client):
    """Test GET /applications/{app}/schedule when no schedule."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = None
    mock_conn.cursor.return_value = mock_cursor
    mock_db.return_value.connection.return_value.__enter__.return_value = mock_conn

    response = client.get("/api/v1/applications/rhoai-v3-5/schedule")

    assert response.status_code == 200
    assert response.json() is None


@patch("api.routes.releases._db")
@patch("api.routes.releases.date")
def test_get_schedule_with_data(mock_date, mock_db, client):
    """Test GET /applications/{app}/schedule with schedule data."""
    mock_date.today.return_value = date(2026, 7, 7)

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = (
        date(2026, 7, 10),  # planning_freeze
        date(2026, 7, 15),  # feature_freeze
        date(2026, 7, 20),  # code_freeze
        date(2026, 7, 25),  # initial_rc
        date(2026, 7, 30),  # release_window_start
        date(2026, 8, 5),   # release_date
        "v3.6",             # next_release
        datetime(2026, 7, 1, 10, 0, 0),  # updated_at
    )
    mock_conn.cursor.return_value = mock_cursor
    mock_db.return_value.connection.return_value.__enter__.return_value = mock_conn

    response = client.get("/api/v1/applications/rhoai-v3-5/schedule")

    assert response.status_code == 200
    data = response.json()
    assert data["application"] == "rhoai-v3-5"
    assert data["planning_freeze"] == "2026-07-10"
    assert data["planning_freeze_days"] == 3
    assert data["next_release"] == "v3.6"


@patch("api.routes.releases._db")
def test_sync_schedule(mock_db, client):
    """Test POST /releases/schedule/sync."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_db.return_value.connection.return_value.__enter__.return_value = mock_conn

    response = client.post(
        "/api/v1/releases/schedule/sync",
        json=[
            {
                "application": "rhoai-v3-5",
                "planning_freeze": "2026-07-10",
                "feature_freeze": "2026-07-15",
                "code_freeze": None,
                "initial_rc": None,
                "release_window_start": None,
                "release_date": "2026-08-05",
                "next_release": "v3.6",
            }
        ],
    )

    assert response.status_code == 200
    data = response.json()
    assert data["synced"] == ["rhoai-v3-5"]
    assert data["count"] == 1


# ───────────────────────────────────────────────────────────────────────────
# Endpoint Tests: Readiness
# ───────────────────────────────────────────────────────────────────────────


@patch("api.routes.releases.get_repository")
@patch("api.routes.releases.get_active_freeze")
@patch("api.routes.releases.get_schedule")
@patch("api.routes.releases._run_readiness_checks")
@patch("api.routes.releases._build_manual_checks")
def test_get_readiness_ready(
    mock_manual, mock_checks, mock_schedule, mock_freeze, mock_repo, client
):
    """Test GET /applications/{app}/readiness - READY verdict."""
    # Setup mocks
    mock_build_repo = MagicMock()
    mock_build_repo.find_failing_component_names.return_value = set()

    mock_conforma_repo = MagicMock()
    mock_conforma_repo.find_unresolved_component_names.return_value = set()
    mock_conforma_repo.get_violation_summaries.return_value = []

    mock_repo.side_effect = lambda cls: (
        mock_build_repo
        if cls.__name__ == "BuildFailureRepository"
        else mock_conforma_repo
    )

    mock_freeze.return_value = None
    mock_schedule.return_value = {"application": "rhoai-v3-5"}
    mock_checks.return_value = [
        {"name": "Test", "status": "PASS", "detail": "OK", "fix": None}
    ]
    mock_manual.return_value = {"pre_release": [], "post_stage": []}

    response = client.get("/api/v1/applications/rhoai-v3-5/readiness")

    assert response.status_code == 200
    data = response.json()
    assert data["verdict"] == "READY"
    assert data["build_failures"] == 0
    assert data["conforma_violations"] == 0
    assert len(data["blockers"]) == 0
    assert len(data["risks"]) == 0


@patch("api.routes.releases.get_repository")
@patch("api.routes.releases.get_active_freeze")
@patch("api.routes.releases.get_schedule")
@patch("api.routes.releases._run_readiness_checks")
@patch("api.routes.releases._build_manual_checks")
def test_get_readiness_at_risk(
    mock_manual, mock_checks, mock_schedule, mock_freeze, mock_repo, client
):
    """Test GET /applications/{app}/readiness - AT_RISK verdict."""
    # Setup mocks
    mock_build_repo = MagicMock()
    mock_build_repo.find_failing_component_names.return_value = {"comp1", "comp2"}

    mock_conforma_repo = MagicMock()
    mock_conforma_repo.find_unresolved_component_names.return_value = set()
    mock_conforma_repo.get_violation_summaries.return_value = []

    mock_repo.side_effect = lambda cls: (
        mock_build_repo
        if cls.__name__ == "BuildFailureRepository"
        else mock_conforma_repo
    )

    mock_freeze.return_value = None
    mock_schedule.return_value = None
    mock_checks.return_value = []
    mock_manual.return_value = {"pre_release": [], "post_stage": []}

    response = client.get("/api/v1/applications/rhoai-v3-5/readiness")

    assert response.status_code == 200
    data = response.json()
    assert data["verdict"] == "AT_RISK"
    assert data["build_failures"] == 2
    assert len(data["risks"]) == 1
    assert "2 component(s) with failing builds" in data["risks"][0]


@patch("api.routes.releases.get_repository")
@patch("api.routes.releases.get_active_freeze")
@patch("api.routes.releases.get_schedule")
@patch("api.routes.releases._run_readiness_checks")
@patch("api.routes.releases._build_manual_checks")
def test_get_readiness_not_ready(
    mock_manual, mock_checks, mock_schedule, mock_freeze, mock_repo, client
):
    """Test GET /applications/{app}/readiness - NOT_READY verdict."""
    # Setup mocks
    mock_build_repo = MagicMock()
    mock_build_repo.find_failing_component_names.return_value = set()

    mock_conforma_repo = MagicMock()
    mock_conforma_repo.find_unresolved_component_names.return_value = {"comp1"}
    mock_conforma_repo.get_violation_summaries.return_value = [
        {
            'component_name': 'comp1',
            'scenario': 'conforma-registry-rhoai-prod-v3-5-single-component',
            'violation_summary': '✕ [Violation] source_image.exists\n  Reason: source image missing',
        },
    ]

    mock_repo.side_effect = lambda cls: (
        mock_build_repo
        if cls.__name__ == "BuildFailureRepository"
        else mock_conforma_repo
    )

    mock_freeze.return_value = {
        "end_date": "2026-07-10",
        "reason": "Code freeze",
    }
    mock_schedule.return_value = None
    mock_checks.return_value = [
        {
            "name": "Test",
            "status": "FAIL",
            "detail": "Integration tests failing",
            "fix": None,
        }
    ]
    mock_manual.return_value = {"pre_release": [], "post_stage": []}

    response = client.get("/api/v1/applications/rhoai-v3-5/readiness")

    assert response.status_code == 200
    data = response.json()
    assert data["verdict"] == "NOT_READY"
    assert data["conforma_violations"] == 1
    assert data["conforma_blockers"] == 1
    assert len(data["blockers"]) == 3  # conforma + freeze + check failure


# ===================================================================
# Readiness: exception-aware conforma counting
# ===================================================================


@patch("api.routes.releases.get_repository")
@patch("api.routes.releases.get_active_freeze")
@patch("api.routes.releases.get_schedule")
@patch("api.routes.releases._run_readiness_checks")
@patch("api.routes.releases._build_manual_checks")
@patch("conforma.policy_tools.fetch_exceptions_by_policy")
def test_readiness_conforma_all_exceptions_covered(
    mock_exceptions, mock_manual, mock_checks, mock_schedule, mock_freeze, mock_repo, client
):
    """Violations fully covered by exceptions should NOT be blockers."""
    mock_build_repo = MagicMock()
    mock_build_repo.find_failing_component_names.return_value = set()

    mock_conforma_repo = MagicMock()
    mock_conforma_repo.find_unresolved_component_names.return_value = {"comp1", "comp2"}
    mock_conforma_repo.get_violation_summaries.return_value = [
        {
            'component_name': 'comp1',
            'scenario': 'conforma-registry-rhoai-prod-v3-5-ea-2-single-component',
            'violation_summary': '✕ [Violation] deprecated_image_check\n  Reason: deprecated base image',
        },
        {
            'component_name': 'comp2',
            'scenario': 'conforma-registry-rhoai-prod-v3-5-ea-2-single-component',
            'violation_summary': '✕ [Violation] deprecated_image_check\n  Reason: deprecated base image',
        },
    ]

    mock_repo.side_effect = lambda cls: (
        mock_build_repo
        if cls.__name__ == "BuildFailureRepository"
        else mock_conforma_repo
    )

    mock_exceptions.return_value = {
        'registry-rhoai-prod': [
            {'value': 'deprecated_image_check', 'permanent': True,
             'effectiveUntil': None, 'days_left': None},
        ],
    }
    mock_freeze.return_value = None
    mock_schedule.return_value = None
    mock_checks.return_value = []
    mock_manual.return_value = {"pre_release": [], "post_stage": []}

    response = client.get("/api/v1/applications/rhoai-v3-5-ea-2/readiness")

    assert response.status_code == 200
    data = response.json()
    assert data["conforma_violations"] == 2
    assert data["conforma_blockers"] == 0
    assert data["verdict"] == "READY"
    blocker_texts = ' '.join(data.get("blockers", []))
    assert "conforma" not in blocker_texts.lower()


@patch("api.routes.releases.get_repository")
@patch("api.routes.releases.get_active_freeze")
@patch("api.routes.releases.get_schedule")
@patch("api.routes.releases._run_readiness_checks")
@patch("api.routes.releases._build_manual_checks")
@patch("conforma.policy_tools.fetch_exceptions_by_policy")
def test_readiness_conforma_partial_coverage(
    mock_exceptions, mock_manual, mock_checks, mock_schedule, mock_freeze, mock_repo, client
):
    """Mix of covered and uncovered violations — only uncovered should block."""
    mock_build_repo = MagicMock()
    mock_build_repo.find_failing_component_names.return_value = set()

    mock_conforma_repo = MagicMock()
    mock_conforma_repo.find_unresolved_component_names.return_value = {"comp1", "comp2"}
    mock_conforma_repo.get_violation_summaries.return_value = [
        {
            'component_name': 'comp1',
            'scenario': 'conforma-registry-rhoai-prod-v3-5-ea-2-single-component',
            'violation_summary': '✕ [Violation] deprecated_image_check\n  Reason: deprecated base image',
        },
        {
            'component_name': 'comp2',
            'scenario': 'conforma-registry-rhoai-prod-v3-5-ea-2-single-component',
            'violation_summary': '✕ [Violation] source_image.exists\n  Reason: source image missing',
        },
    ]

    mock_repo.side_effect = lambda cls: (
        mock_build_repo
        if cls.__name__ == "BuildFailureRepository"
        else mock_conforma_repo
    )

    mock_exceptions.return_value = {
        'registry-rhoai-prod': [
            {'value': 'deprecated_image_check', 'permanent': True,
             'effectiveUntil': None, 'days_left': None},
        ],
    }
    mock_freeze.return_value = None
    mock_schedule.return_value = None
    mock_checks.return_value = []
    mock_manual.return_value = {"pre_release": [], "post_stage": []}

    response = client.get("/api/v1/applications/rhoai-v3-5-ea-2/readiness")

    assert response.status_code == 200
    data = response.json()
    assert data["conforma_violations"] == 2
    assert data["conforma_blockers"] == 1
    assert data["verdict"] == "NOT_READY"
    assert any("1 component" in b and "conforma" in b.lower() for b in data["blockers"])


@patch("api.routes.releases.get_repository")
@patch("api.routes.releases.get_active_freeze")
@patch("api.routes.releases.get_schedule")
@patch("api.routes.releases._run_readiness_checks")
@patch("api.routes.releases._build_manual_checks")
@patch("conforma.policy_tools.fetch_exceptions_by_policy")
def test_readiness_conforma_no_exceptions_available(
    mock_exceptions, mock_manual, mock_checks, mock_schedule, mock_freeze, mock_repo, client
):
    """When no exceptions are available, all violations are blockers (graceful degradation)."""
    mock_build_repo = MagicMock()
    mock_build_repo.find_failing_component_names.return_value = set()

    mock_conforma_repo = MagicMock()
    mock_conforma_repo.find_unresolved_component_names.return_value = {"comp1"}
    mock_conforma_repo.get_violation_summaries.return_value = [
        {
            'component_name': 'comp1',
            'scenario': 'conforma-registry-rhoai-prod-v3-5-ea-2-single-component',
            'violation_summary': '✕ [Violation] deprecated_image_check\n  Reason: deprecated base image',
        },
    ]

    mock_repo.side_effect = lambda cls: (
        mock_build_repo
        if cls.__name__ == "BuildFailureRepository"
        else mock_conforma_repo
    )

    mock_exceptions.return_value = {}
    mock_freeze.return_value = None
    mock_schedule.return_value = None
    mock_checks.return_value = []
    mock_manual.return_value = {"pre_release": [], "post_stage": []}

    response = client.get("/api/v1/applications/rhoai-v3-5-ea-2/readiness")

    assert response.status_code == 200
    data = response.json()
    assert data["conforma_violations"] == 1
    assert data["conforma_blockers"] == 1
    assert data["verdict"] == "NOT_READY"


# ───────────────────────────────────────────────────────────────────────────
# Jira Health Check Tests
# ───────────────────────────────────────────────────────────────────────────


def test_check_jira_health_valid():
    """Jira health check returns PASS when token is valid."""
    with patch('clients.jira_client.JiraClient') as MockJira:
        MockJira.return_value.check_token_health.return_value = {
            'status': 'valid',
            'user': 'test-user',
            'message': 'Token valid, authenticated as test-user',
        }
        result = _check_jira_health()

    assert result['name'] == 'Jira API health'
    assert result['phase'] == 'pre-release'
    assert result['status'] == 'PASS'
    assert 'valid' in result['detail'].lower()


def test_check_jira_health_expired():
    """Jira health check returns FAIL when token is expired."""
    with patch('clients.jira_client.JiraClient') as MockJira:
        MockJira.return_value.check_token_health.return_value = {
            'status': 'expired',
            'user': None,
            'message': 'Jira token expired or invalid (HTTP 401)',
        }
        result = _check_jira_health()

    assert result['status'] == 'FAIL'
    assert result['fix'] is not None
    assert 'regenerate' in result['fix'].lower() or 'token' in result['fix'].lower()


def test_check_jira_health_missing():
    """Jira health check returns WARN when no token configured."""
    with patch('clients.jira_client.JiraClient') as MockJira:
        MockJira.return_value.check_token_health.return_value = {
            'status': 'missing',
            'user': None,
            'message': 'JIRA_TOKEN environment variable is not set',
        }
        result = _check_jira_health()

    assert result['status'] == 'WARN'


def test_check_jira_health_unreachable():
    """Jira health check returns WARN when Jira API is unreachable."""
    with patch('clients.jira_client.JiraClient') as MockJira:
        MockJira.return_value.check_token_health.return_value = {
            'status': 'unreachable',
            'user': None,
            'message': 'Jira API unreachable: connection refused',
        }
        result = _check_jira_health()

    assert result['status'] == 'WARN'


# ───────────────────────────────────────────────────────────────────────────
# Jira Blocker Check Tests
# ───────────────────────────────────────────────────────────────────────────


def test_check_blockers_with_results():
    """Blocker check returns FAIL with category breakdown when blockers exist."""
    with patch('clients.jira_client.JiraClient') as MockJira, \
         patch('clients.jira_query_builder.JiraBlockerQuery') as MockQuery, \
         patch('clients.jira_query_builder.categorize_blocker') as mock_cat:
        MockQuery.return_value.for_application.return_value.build.return_value = 'jql'
        MockJira.return_value.search_blockers.return_value = [
            {'key': 'RHOAIENG-100', 'summary': 'Build fails', 'status': 'Open',
             'status_category': 'new', 'labels': []},
            {'key': 'RHOAIENG-101', 'summary': 'TFA: test failure',
             'status': 'Open', 'status_category': 'new', 'labels': []},
            {'key': 'RHOAIENG-102', 'summary': 'Product Sign Off',
             'status': 'Closed', 'status_category': 'done', 'labels': []},
        ]
        mock_cat.side_effect = ['product', 'tfa']
        result = _check_blockers('rhoai-v3-5-ea-2')

    assert result['status'] == 'FAIL'
    assert '2 open blocker' in result['detail']
    summary = result['blocker_summary']
    assert summary['total'] == 3
    assert summary['open'] == 2
    assert summary['by_category']['product'] == 1
    assert summary['by_category']['tfa'] == 1


def test_check_blockers_none():
    """Blocker check returns PASS when no blockers found."""
    with patch('clients.jira_client.JiraClient') as MockJira, \
         patch('clients.jira_query_builder.JiraBlockerQuery') as MockQuery:
        MockQuery.return_value.for_application.return_value.build.return_value = 'jql'
        MockJira.return_value.search_blockers.return_value = []
        result = _check_blockers('rhoai-v3-5-ea-2')

    assert result['status'] == 'PASS'
    assert '0' in result['detail']
    assert result['blocker_summary']['total'] == 0


def test_check_blockers_jira_unavailable():
    """Blocker check returns SKIP when Jira is unavailable."""
    with patch('clients.jira_client.JiraClient') as MockJira, \
         patch('clients.jira_query_builder.JiraBlockerQuery') as MockQuery:
        MockQuery.return_value.for_application.return_value.build.return_value = 'jql'
        MockJira.return_value.search_blockers.side_effect = Exception('connection refused')
        result = _check_blockers('rhoai-v3-5-ea-2')

    assert result['status'] == 'SKIP'


# ───────────────────────────────────────────────────────────────────────────
# FBC Health DB Fallback Tests
# ───────────────────────────────────────────────────────────────────────────


def test_fbc_health_db_fallback_pass():
    """FBC health returns PASS from DB when cluster unreachable and last build succeeded."""
    mock_monitor = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.connection.return_value.__enter__ = MagicMock(return_value=MagicMock(cursor=MagicMock(return_value=mock_cursor)))
    mock_db.connection.return_value.__exit__ = MagicMock(return_value=False)
    mock_cursor.fetchone.return_value = ('Success', 95, None, '2026-07-14T10:00:00Z')

    with patch('proactive.health_monitor._nightly_component_for_app', return_value='fbc-fragment-v3-5'):
        result = _check_fbc_health(mock_monitor, 'rhoai-v3-5', k8s_reachable=False, db=mock_db)

    assert result['status'] == 'PASS'
    assert 'from DB' in result['detail']
    assert 'cluster unreachable' in result['detail']


def test_fbc_health_db_fallback_fail():
    """FBC health returns FAIL from DB when cluster unreachable and last build failed."""
    mock_monitor = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.connection.return_value.__enter__ = MagicMock(return_value=MagicMock(cursor=MagicMock(return_value=mock_cursor)))
    mock_db.connection.return_value.__exit__ = MagicMock(return_value=False)
    mock_cursor.fetchone.return_value = ('Failed', 30, '2026-07-14T09:00:00Z', '2026-07-13T10:00:00Z')

    with patch('proactive.health_monitor._nightly_component_for_app', return_value='fbc-fragment-v3-5'):
        result = _check_fbc_health(mock_monitor, 'rhoai-v3-5', k8s_reachable=False, db=mock_db)

    assert result['status'] == 'FAIL'
    assert 'from DB' in result['detail']


def test_fbc_health_db_fallback_no_data():
    """FBC health returns SKIP from DB when no DB record exists."""
    mock_monitor = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.connection.return_value.__enter__ = MagicMock(return_value=MagicMock(cursor=MagicMock(return_value=mock_cursor)))
    mock_db.connection.return_value.__exit__ = MagicMock(return_value=False)
    mock_cursor.fetchone.return_value = None

    with patch('proactive.health_monitor._nightly_component_for_app', return_value='fbc-fragment-v3-5'):
        result = _check_fbc_health(mock_monitor, 'rhoai-v3-5', k8s_reachable=False, db=mock_db)

    assert result['status'] == 'SKIP'


# ───────────────────────────────────────────────────────────────────────────
# Stale Components DB Fallback Tests
# ───────────────────────────────────────────────────────────────────────────


def test_stale_components_db_fallback_stale():
    """Stale check returns WARN from DB when cluster unreachable and stale commits found."""
    mock_monitor = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.connection.return_value.__enter__ = MagicMock(return_value=MagicMock(cursor=MagicMock(return_value=mock_cursor)))
    mock_db.connection.return_value.__exit__ = MagicMock(return_value=False)
    mock_cursor.description = [('component_name',), ('repository_url',), ('branch',), ('commit_sha',)]
    mock_cursor.fetchall.return_value = [
        ('comp-a', 'https://github.com/org/repo-a', 'release-3.5', 'aaa111aaa111aaa111'),
        ('comp-b', 'https://github.com/org/repo-b', 'release-3.5', 'bbb222bbb222bbb222'),
    ]

    with patch('clients.github_client.parse_github_repo', side_effect=[('org', 'repo-a'), ('org', 'repo-b')]), \
         patch('clients.github_client.GitHubClient') as MockGH:
        MockGH.return_value.get_ref_sha.side_effect = ['ccc333ccc333ccc333', 'bbb222bbb222bbb222']
        result = _check_stale_components(mock_monitor, 'rhoai-v3-5', k8s_reachable=False, db=mock_db)

    assert result['status'] == 'WARN'
    assert 'from DB' in result['detail']
    assert 'comp-a' in result['detail']


def test_stale_components_db_fallback_fresh():
    """Stale check returns PASS from DB when all commits match."""
    mock_monitor = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.connection.return_value.__enter__ = MagicMock(return_value=MagicMock(cursor=MagicMock(return_value=mock_cursor)))
    mock_db.connection.return_value.__exit__ = MagicMock(return_value=False)
    mock_cursor.description = [('component_name',), ('repository_url',), ('branch',), ('commit_sha',)]
    mock_cursor.fetchall.return_value = [
        ('comp-a', 'https://github.com/org/repo-a', 'release-3.5', 'aaa111aaa111aaa111'),
    ]

    with patch('clients.github_client.parse_github_repo', return_value=('org', 'repo-a')), \
         patch('clients.github_client.GitHubClient') as MockGH:
        MockGH.return_value.get_ref_sha.return_value = 'aaa111aaa111aaa111'
        result = _check_stale_components(mock_monitor, 'rhoai-v3-5', k8s_reachable=False, db=mock_db)

    assert result['status'] == 'PASS'
    assert 'from DB' in result['detail']


# ───────────────────────────────────────────────────────────────────────────
# Snapshot Freshness DB Fallback Tests
# ───────────────────────────────────────────────────────────────────────────


def test_snapshot_freshness_db_fallback_stale():
    """Snapshot freshness returns WARN from DB when oldest build >24h."""
    from datetime import UTC, datetime, timedelta
    mock_monitor = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.connection.return_value.__enter__ = MagicMock(return_value=MagicMock(cursor=MagicMock(return_value=mock_cursor)))
    mock_db.connection.return_value.__exit__ = MagicMock(return_value=False)
    old_ts = datetime.now(UTC) - timedelta(hours=48)
    mock_cursor.fetchall.return_value = [
        ('stale-comp', old_ts),
    ]

    result = _check_snapshot_freshness(mock_monitor, 'rhoai-v3-5', k8s_reachable=False, db=mock_db)

    assert result['status'] == 'WARN'
    assert 'from DB' in result['detail']


def test_snapshot_freshness_db_fallback_fresh():
    """Snapshot freshness returns PASS from DB when all builds <24h."""
    from datetime import UTC, datetime, timedelta
    mock_monitor = MagicMock()
    mock_db = MagicMock()
    mock_cursor = MagicMock()
    mock_db.connection.return_value.__enter__ = MagicMock(return_value=MagicMock(cursor=MagicMock(return_value=mock_cursor)))
    mock_db.connection.return_value.__exit__ = MagicMock(return_value=False)
    recent_ts = datetime.now(UTC) - timedelta(hours=2)
    mock_cursor.fetchall.return_value = [
        ('fresh-comp', recent_ts),
    ]

    result = _check_snapshot_freshness(mock_monitor, 'rhoai-v3-5', k8s_reachable=False, db=mock_db)

    assert result['status'] == 'PASS'
    assert 'from DB' in result['detail']


# Run the tests if this file is executed directly
if __name__ == "__main__":
    import subprocess
    subprocess.run(["python3", "-m", "pytest", __file__, "-x", "-q", "--tb=short"])
