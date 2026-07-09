"""Tests for conforma violation endpoints."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.errors import register_error_handlers
from api.routes.violations import router


@pytest.fixture
def client():
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router, prefix="/api/v1")
    return TestClient(app, raise_server_exceptions=False)


# Shared mock returns for conforma_utils lazy imports
CONFORMA_UTILS_PATCHES = {
    "conforma.policy_tools.categorize_policy": "Components",
    "conforma.policy_tools.compute_blocks": "release",
    "conforma.policy_tools.compute_coverage_by_env": {},
    "conforma.policy_tools.count_unique_violations": (3, [{"rule": "test_rule", "detail": ""}]),
    "conforma.policy_tools.extract_policy_from_scenario": "test-policy",
    "conforma.policy_tools.extract_violation_rules": ["rule1"],
    "conforma.policy_tools.fetch_exceptions_by_policy": {},
    "conforma.policy_tools.policy_env": "stage",
    "conforma.policy_tools.enrich_with_coverage": None,
    "conforma.policy_tools.compute_violation_coverage": {
        "coverage": "not_covered",
        "covered_rules": [],
        "uncovered_rules": ["rule1"],
        "matching_exceptions": [],
    },
    "conforma.policy_tools.lookup_exceptions": [],
    "conforma.policy_tools.compute_exception_coverage_details": {
        "coverage": "not_covered",
        "stage": None,
        "prod": None,
        "env_tag": None,
        "policy_url_stage": None,
        "policy_url_prod": None,
        "uncovered_rules_stage": [],
        "uncovered_rules_prod": [],
    },
    "conforma.policy_tools.policy_url": None,
}


def _make_violation(component="comp-a", scenario="scenario-stage"):
    return {
        "component_name": component,
        "scenario": scenario,
        "violations_count": 5,
        "warnings_count": 1,
        "successes_count": 10,
        "violation_summary": "FAIL: rule1 detail",
        "jira_key": None,
    }


def _apply_conforma_patches(extra=None):
    """Return a combined dict of patch targets/values."""
    patches = dict(CONFORMA_UTILS_PATCHES)
    if extra:
        patches.update(extra)
    return patches


def _start_patches(targets):
    """Start multiple patches and return (list_of_patchers, dict_of_mocks)."""
    patchers = []
    mocks = {}
    for target, rv in targets.items():
        p = patch(target, return_value=rv)
        mocks[target] = p.start()
        patchers.append(p)
    return patchers, mocks


def _stop_patches(patchers):
    for p in patchers:
        p.stop()


# ---------------------------------------------------------------------------
# GET /applications/{app}/violations
# ---------------------------------------------------------------------------

def test_list_violations_happy_path(client):
    violations = [_make_violation("comp-a"), _make_violation("comp-b")]
    patches = _apply_conforma_patches()
    patchers, _ = _start_patches(patches)
    try:
        with patch("api.routes.violations._conforma_repo") as mock_repo, \
             patch("api.routes.violations._triage_repo") as mock_triage, \
             patch("api.validators.validate_application_name", side_effect=lambda x: x):
            mock_repo.return_value.get_violation_summaries.return_value = violations
            mock_triage.return_value.build_jira_map.return_value = {"comp-a": "RHOAI-100"}

            resp = client.get("/api/v1/applications/rhoai-v2-19/violations")
    finally:
        _stop_patches(patchers)

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["category"] == "Components"
    assert data[0]["unique_violations"] == 3
    assert data[0]["blocks"] == "release"
    assert data[0]["policy_env"] == "stage"


def test_list_violations_empty(client):
    patches = _apply_conforma_patches()
    patchers, _ = _start_patches(patches)
    try:
        with patch("api.routes.violations._conforma_repo") as mock_repo, \
             patch("api.routes.violations._triage_repo") as mock_triage, \
             patch("api.validators.validate_application_name", side_effect=lambda x: x):
            mock_repo.return_value.get_violation_summaries.return_value = []
            mock_triage.return_value.build_jira_map.return_value = {}

            resp = client.get("/api/v1/applications/rhoai-v2-19/violations")
    finally:
        _stop_patches(patchers)

    assert resp.status_code == 200
    assert resp.json() == []


def test_list_violations_jira_map_enrichment(client):
    violations = [_make_violation("comp-a")]
    patches = _apply_conforma_patches()
    patchers, _ = _start_patches(patches)
    try:
        with patch("api.routes.violations._conforma_repo") as mock_repo, \
             patch("api.routes.violations._triage_repo") as mock_triage, \
             patch("api.validators.validate_application_name", side_effect=lambda x: x):
            mock_repo.return_value.get_violation_summaries.return_value = violations
            mock_triage.return_value.build_jira_map.return_value = {"comp-a": "RHOAI-42"}

            resp = client.get("/api/v1/applications/rhoai-v2-19/violations")
    finally:
        _stop_patches(patchers)

    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["jira_key"] == "RHOAI-42"


def test_list_violations_with_reporter_env(client):
    reporter_violations = [{"component": "comp-x", "rule": "r1"}]
    with patch("api.validators.validate_application_name", side_effect=lambda x: x), \
         patch("cli.config.app_to_reporter_branch", return_value="rhoai-2.19"), \
         patch("clients.conforma_reporter_client.fetch_reporter_violations",
               return_value=reporter_violations) as mock_fetch:

        resp = client.get(
            "/api/v1/applications/rhoai-v2-19/violations"
            "?reporter_env=stage&reporter_build_type=latest"
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data == reporter_violations
    mock_fetch.assert_called_once_with("rhoai-2.19", env="stage", build_type="latest")


# ---------------------------------------------------------------------------
# GET /applications/{app}/violations/report
# ---------------------------------------------------------------------------

def test_get_conforma_report_happy_path(client):
    violations = [_make_violation("comp-a"), _make_violation("comp-b")]
    patches = _apply_conforma_patches()
    patchers, _ = _start_patches(patches)
    try:
        with patch("api.routes.violations._conforma_repo") as mock_repo, \
             patch("api.routes.violations._triage_repo") as mock_triage, \
             patch("api.validators.validate_application_name", side_effect=lambda x: x):
            mock_repo.return_value.get_violation_summaries.return_value = violations
            mock_triage.return_value.build_jira_map.return_value = {}

            resp = client.get("/api/v1/applications/rhoai-v2-19/violations/report")
    finally:
        _stop_patches(patchers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["application"] == "rhoai-v2-19"
    assert data["total_violations"] == 2
    assert data["total_unique_violations"] == 6  # 3 per violation * 2
    assert "groups" in data
    assert "violations" in data
    assert "Components" in data["groups"]


def test_get_conforma_report_empty(client):
    patches = _apply_conforma_patches()
    patchers, _ = _start_patches(patches)
    try:
        with patch("api.routes.violations._conforma_repo") as mock_repo, \
             patch("api.routes.violations._triage_repo") as mock_triage, \
             patch("api.validators.validate_application_name", side_effect=lambda x: x):
            mock_repo.return_value.get_violation_summaries.return_value = []
            mock_triage.return_value.build_jira_map.return_value = {}

            resp = client.get("/api/v1/applications/rhoai-v2-19/violations/report")
    finally:
        _stop_patches(patchers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["total_violations"] == 0
    assert data["violations"] == []


def test_get_conforma_report_with_reporter(client):
    reporter_violations = [{"component": "comp-x"}]
    with patch("api.validators.validate_application_name", side_effect=lambda x: x), \
         patch("cli.config.app_to_reporter_branch", return_value="rhoai-2.19"), \
         patch("clients.conforma_reporter_client.fetch_reporter_violations",
               return_value=reporter_violations):

        resp = client.get(
            "/api/v1/applications/rhoai-v2-19/violations/report"
            "?reporter_env=prod"
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["application"] == "rhoai-v2-19"
    assert data["source"] == "prod/latest"
    assert data["total_violations"] == 1


# ---------------------------------------------------------------------------
# GET /applications/{app}/violations/rules
# ---------------------------------------------------------------------------

def test_list_violation_rules_happy_path(client):
    violations = [
        _make_violation("comp-a"),
        _make_violation("comp-b"),
    ]
    with patch("api.validators.validate_application_name", side_effect=lambda x: x), \
         patch("api.routes.violations._conforma_repo") as mock_repo, \
         patch("conforma.policy_tools.count_unique_violations",
               return_value=(2, [{"rule": "rule1", "detail": ""}, {"rule": "rule2", "detail": ""}])):
        mock_repo.return_value.get_violation_summaries.return_value = violations

        resp = client.get("/api/v1/applications/rhoai-v2-19/violations/rules")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    # Both components should appear in the rule entry
    for entry in data:
        assert "rule" in entry
        assert "components" in entry
        assert "count" in entry


def test_list_violation_rules_with_reporter(client):
    reporter_rules = [{"rule": "r1", "count": 2, "components": ["a", "b"]}]
    with patch("api.validators.validate_application_name", side_effect=lambda x: x), \
         patch("cli.config.app_to_reporter_branch", return_value="rhoai-2.19"), \
         patch("clients.conforma_reporter_client.fetch_reporter_rules",
               return_value=reporter_rules):

        resp = client.get(
            "/api/v1/applications/rhoai-v2-19/violations/rules"
            "?reporter_env=stage&reporter_build_type=latest"
        )

    assert resp.status_code == 200
    assert resp.json() == reporter_rules


def test_list_violation_rules_empty(client):
    with patch("api.validators.validate_application_name", side_effect=lambda x: x), \
         patch("api.routes.violations._conforma_repo") as mock_repo, \
         patch("conforma.policy_tools.count_unique_violations",
               return_value=(0, [])):
        mock_repo.return_value.get_violation_summaries.return_value = []

        resp = client.get("/api/v1/applications/rhoai-v2-19/violations/rules")

    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# GET /applications/{app}/violations/{component}
# ---------------------------------------------------------------------------

def test_get_violation_happy_path(client):
    now = datetime.utcnow()
    row = {
        "component_name": "comp-a",
        "scenario": "scenario-stage",
        "violations_count": 5,
        "warnings_count": 1,
        "successes_count": 10,
        "violation_summary": "FAIL: rule1",
        "pipelinerun_name": "pr-123",
        "repository_url": "https://github.com/org/repo",
        "commit_sha": "abc123",
        "snapshot_name": "snap-1",
        "first_detected_at": now,
    }
    patches = _apply_conforma_patches()
    patchers, _ = _start_patches(patches)
    try:
        with patch("api.routes.violations._conforma_repo") as mock_repo:
            mock_repo.return_value.get_violation_details.return_value = row

            resp = client.get("/api/v1/applications/rhoai-v2-19/violations/comp-a")
    finally:
        _stop_patches(patchers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["component"] == "comp-a"
    assert data["scenario"] == "scenario-stage"
    assert data["policy_name"] == "test-policy"
    assert data["violations_count"] == 5
    assert data["unique_violations"] == 3
    assert "konflux_url" in data


def test_get_violation_not_found(client):
    with patch("api.routes.violations._conforma_repo") as mock_repo:
        mock_repo.return_value.get_violation_details.return_value = None

        resp = client.get("/api/v1/applications/rhoai-v2-19/violations/nonexistent")

    assert resp.status_code == 404
    assert "not_found" in resp.json().get("error", "")


# ---------------------------------------------------------------------------
# GET /exceptions/lifecycle
# ---------------------------------------------------------------------------

def test_exception_lifecycle_no_exceptions(client):
    with patch("conforma.policy_tools.fetch_exceptions_by_policy", return_value={}):
        resp = client.get("/api/v1/exceptions/lifecycle")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total_exceptions"] == 0
    assert data["permanent"] == 0
    assert data["expired"] == 0
    assert data["exceptions"] == []


def test_exception_lifecycle_mixed(client):
    exceptions_by_policy = {
        "policy-a": [
            {"value": "rule1", "permanent": True, "days_left": None},
            {"value": "rule2", "permanent": False, "days_left": 3,
             "effectiveUntil": "2026-07-10"},
        ],
        "policy-b": [
            {"value": "rule3", "permanent": False, "days_left": -5,
             "effectiveUntil": "2026-06-25"},
            {"value": "rule4", "permanent": False, "days_left": 30,
             "effectiveUntil": "2026-07-31"},
        ],
    }
    with patch("conforma.policy_tools.fetch_exceptions_by_policy",
               return_value=exceptions_by_policy):
        resp = client.get("/api/v1/exceptions/lifecycle")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total_exceptions"] == 4
    assert data["permanent"] == 1
    assert data["expiring_soon"] == 1  # rule2 (3 days left < 7)
    assert data["expired"] == 1  # rule3 (days_left = -5)
    assert data["active_temporary"] == 2  # rule2 + rule4
    # Expired should sort first
    statuses = [e["status"] for e in data["exceptions"]]
    assert statuses[0] == "expired"


def test_exception_lifecycle_all_permanent(client):
    exceptions_by_policy = {
        "policy-x": [
            {"value": "rule-perm", "permanent": True, "days_left": None},
        ],
    }
    with patch("conforma.policy_tools.fetch_exceptions_by_policy",
               return_value=exceptions_by_policy):
        resp = client.get("/api/v1/exceptions/lifecycle")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total_exceptions"] == 1
    assert data["permanent"] == 1
    assert data["expired"] == 0
    assert data["expiring_soon"] == 0
    assert data["exceptions"][0]["status"] == "active"
    assert data["exceptions"][0]["permanent"] is True
