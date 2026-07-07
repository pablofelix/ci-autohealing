"""Tests for external service proxy endpoints."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.errors import register_error_handlers
from api.routes.external import router


@pytest.fixture
def client():
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router, prefix="/api/v1")
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# GET /jira/{jira_key}
# ---------------------------------------------------------------------------

@patch("api.routes.external._jira_client")
@patch("api.validators.validate_jira_key", side_effect=lambda k: k)
def test_get_jira_issue_happy_path(mock_validate, mock_client_fn, client):
    jira = MagicMock()
    jira.get_issue_status.return_value = {"status": "In Progress"}
    jira.get_comments.return_value = [{"body": "working on it"}]
    mock_client_fn.return_value = jira

    resp = client.get("/api/v1/jira/RHOAIENG-123")

    assert resp.status_code == 200
    data = resp.json()
    assert data["key"] == "RHOAIENG-123"
    assert data["status"] == {"status": "In Progress"}
    assert data["comments"] == [{"body": "working on it"}]
    jira.get_issue_status.assert_called_once_with("RHOAIENG-123")
    jira.get_comments.assert_called_once_with("RHOAIENG-123")


@patch("api.routes.external._jira_client")
@patch("api.validators.validate_jira_key", side_effect=lambda k: k)
def test_get_jira_issue_client_none_config_error(mock_validate, mock_client_fn, client):
    mock_client_fn.return_value = None

    resp = client.get("/api/v1/jira/RHOAIENG-999")

    assert resp.status_code == 400
    data = resp.json()
    assert data["error"] == "config_error"
    assert "Jira not configured" in data["detail"]


@patch("api.routes.external._jira_client")
@patch("api.validators.validate_jira_key", side_effect=lambda k: k)
def test_get_jira_issue_api_error_502(mock_validate, mock_client_fn, client):
    jira = MagicMock()
    jira.get_issue_status.side_effect = RuntimeError("connection refused")
    mock_client_fn.return_value = jira

    resp = client.get("/api/v1/jira/RHOAIENG-456")

    assert resp.status_code == 502
    data = resp.json()
    assert data["error"] == "jira_error"
    assert "Jira API error" in data["detail"]


# ---------------------------------------------------------------------------
# POST /jira
# ---------------------------------------------------------------------------

@patch("api.routes.external._jira_client")
def test_create_jira_issue_happy_path(mock_client_fn, client):
    jira = MagicMock()
    jira.create_issue.return_value = {"key": "RHOAIENG-100", "url": "https://issues.redhat.com/browse/RHOAIENG-100"}
    mock_client_fn.return_value = jira

    resp = client.post(
        "/api/v1/jira",
        params={
            "summary": "Test bug",
            "description": "Something broke",
            "issue_type": "Bug",
            "component": "odh-dashboard",
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["key"] == "RHOAIENG-100"
    jira.create_issue.assert_called_once_with(
        summary="Test bug",
        description_text="Something broke",
        issue_type="Bug",
        labels=[],
    )


# ---------------------------------------------------------------------------
# GET /policies/exceptions
# ---------------------------------------------------------------------------

@patch("api.routes.external._konflux_client")
def test_get_policy_exceptions_happy_path(mock_client_fn, client):
    kc = MagicMock()
    kc.get_ec_policies.return_value = [{"name": "policy-a"}, {"name": "policy-b"}]
    kc.extract_exceptions.side_effect = [
        [{"rule": "deprecated-image-check"}],
        [{"rule": "snyk-check"}, {"rule": "rpm-sig-check"}],
    ]
    mock_client_fn.return_value = kc

    resp = client.get("/api/v1/policies/exceptions")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert len(data["exceptions"]) == 3
    assert data["exceptions"][0]["rule"] == "deprecated-image-check"


@patch("api.routes.external._konflux_client")
def test_get_policy_exceptions_error_502(mock_client_fn, client):
    kc = MagicMock()
    kc.get_ec_policies.side_effect = RuntimeError("cluster unreachable")
    mock_client_fn.return_value = kc

    resp = client.get("/api/v1/policies/exceptions")

    assert resp.status_code == 502
    data = resp.json()
    assert data["error"] == "konflux_error"
    assert "Failed to fetch policy exceptions" in data["detail"]
    assert "suggestion" in data


# ---------------------------------------------------------------------------
# GET /policies/bindings
# ---------------------------------------------------------------------------

@patch("api.routes.external._konflux_client")
def test_get_policy_bindings_happy_path(mock_client_fn, client):
    kc = MagicMock()
    kc.get_release_plan_admissions.return_value = [
        {
            "metadata": {"name": "rpa-odh-dashboard"},
            "spec": {
                "application": "odh-dashboard",
                "policy": "rhoai-stage-policy",
            },
        },
        {
            "metadata": {"name": "rpa-notebook-controller"},
            "spec": {
                "application": "notebook-controller",
                "policy": "rhoai-prod-policy",
            },
        },
    ]
    mock_client_fn.return_value = kc

    resp = client.get("/api/v1/policies/bindings")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert data["bindings"][0]["name"] == "rpa-odh-dashboard"
    assert data["bindings"][0]["application"] == "odh-dashboard"
    assert data["bindings"][0]["policy"] == "rhoai-stage-policy"
    assert data["bindings"][1]["name"] == "rpa-notebook-controller"
