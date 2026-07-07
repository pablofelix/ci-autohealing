"""Tests for export endpoints."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.errors import register_error_handlers
from api.routes.exports import (
    _format_failure_duration,
    _row_to_analysis,
    _sanitize_for_jira,
    _sanitize_for_slack,
    router,
)


@pytest.fixture
def client():
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router, prefix="/api/v1")
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def mock_build_repo():
    return MagicMock()


@pytest.fixture
def mock_conforma_repo():
    return MagicMock()


@pytest.fixture
def mock_ai_repo():
    return MagicMock()


def _patch_repos(build_repo, conforma_repo, ai_repo):
    """Context manager helper to patch all three repository factories."""
    return (
        patch("api.routes.exports._build_repo", return_value=build_repo),
        patch("api.routes.exports._conforma_repo", return_value=conforma_repo),
        patch("api.routes.exports._ai_repo", return_value=ai_repo),
    )


SAMPLE_FAILURE = {
    "component_name": "odh-dashboard",
    "pipelinerun_name": "odh-dashboard-build-abc123",
    "error_message": "go build failed",
    "error_type": "compilation",
    "failed_step_name": "build",
    "first_detected_at": "2026-06-28",
    "last_updated_at": "2026-06-30",
    "repository_url": "https://github.com/opendatahub-io/odh-dashboard",
    "branch": "rhoai-2.19",
    "commit_sha": "abc12345def67890",
    "commit_url": "https://github.com/opendatahub-io/odh-dashboard/commit/abc12345",
    "commit_message": "fix: update deps",
    "commit_author": "dev1",
    "build_logs": "some long build log content here",
    "konflux_url": "https://konflux.example.com/logs",
}

SAMPLE_VIOLATION = {
    "component_name": "odh-dashboard",
    "pipelinerun_name": "odh-dashboard-pr-xyz789",
    "scenario": "rhoai-policy",
    "violations_count": 3,
    "warnings_count": 1,
    "first_detected_at": "2026-06-29",
    "last_updated_at": "2026-06-30",
    "snapshot_name": "snap-123",
}

SAMPLE_ANALYSIS_ROW = {
    "analysis_type": "build",
    "component_name": "odh-dashboard",
    "model_used": "claude-sonnet-4-20250514",
    "root_cause": "Missing dependency in go.mod",
    "failure_category": "dependency",
    "confidence_score": 0.85,
    "recommended_fix": "Run go mod tidy",
    "recommended_files": ["go.mod", "go.sum"],
    "can_auto_fix": True,
    "requires_human_review": False,
    "analyzed_at": datetime(2026, 6, 30, 12, 0, 0),
    "langfuse_trace_url": "https://langfuse.example.com/trace/123",
    "tokens_used": 5000,
    "cost_usd": 0.02,
}


# --- _sanitize_for_jira ---


@patch("api.routes.exports.redact_secrets", side_effect=lambda x: x)
def test_sanitize_jira_removes_markup(mock_redact):
    result = _sanitize_for_jira("before {code}some code{code} after")
    assert "{code}" not in result
    assert "before" in result


@patch("api.routes.exports.redact_secrets", side_effect=lambda x: x)
def test_sanitize_jira_removes_panel(mock_redact):
    result = _sanitize_for_jira("text {panel:title=My Panel} content {panel} end")
    assert "{panel" not in result


@patch("api.routes.exports.redact_secrets", side_effect=lambda x: x)
def test_sanitize_jira_removes_user_mentions(mock_redact):
    result = _sanitize_for_jira("assigned to [~jdoe] for review")
    assert "[~jdoe]" not in result
    assert "assigned to" in result


@patch("api.routes.exports.redact_secrets", side_effect=lambda x: x)
def test_sanitize_jira_none_input(mock_redact):
    assert _sanitize_for_jira(None) is None
    assert _sanitize_for_jira("") == ""


@patch("api.routes.exports.redact_secrets", side_effect=lambda x: x)
def test_sanitize_jira_calls_redact(mock_redact):
    _sanitize_for_jira("some text")
    mock_redact.assert_called_once()


# --- _sanitize_for_slack ---


@patch("api.routes.exports.redact_secrets", side_effect=lambda x: x)
def test_sanitize_slack_replaces_mentions(mock_redact):
    result = _sanitize_for_slack("hey @here and @channel")
    assert "(at)here" in result
    assert "(at)channel" in result
    assert "@here" not in result
    assert "@channel" not in result


@patch("api.routes.exports.redact_secrets", side_effect=lambda x: x)
def test_sanitize_slack_removes_user_ids(mock_redact):
    result = _sanitize_for_slack("cc <@UABC123> and <@U9876XYZ>")
    assert "<@UABC123>" not in result
    assert "<@U9876XYZ>" not in result
    assert "cc" in result


@patch("api.routes.exports.redact_secrets", side_effect=lambda x: x)
def test_sanitize_slack_none_input(mock_redact):
    assert _sanitize_for_slack(None) is None
    assert _sanitize_for_slack("") == ""


# --- _format_failure_duration ---


def test_format_failure_duration_none():
    assert _format_failure_duration(None) is None


def test_format_failure_duration_today():
    now = datetime.now(UTC)
    assert _format_failure_duration(now) == "today"


def test_format_failure_duration_yesterday():
    yesterday = datetime.now(UTC) - timedelta(days=1)
    assert _format_failure_duration(yesterday) == "yesterday"


def test_format_failure_duration_days_ago():
    five_days_ago = datetime.now(UTC) - timedelta(days=5)
    result = _format_failure_duration(five_days_ago)
    assert "(5 days)" in result
    assert five_days_ago.strftime("%b %d") in result


# --- _row_to_analysis ---


def test_row_to_analysis_list_files():
    analysis = _row_to_analysis(SAMPLE_ANALYSIS_ROW)
    assert analysis.recommended_files == ["go.mod", "go.sum"]
    assert analysis.type == "build"
    assert analysis.confidence_score == 0.85


def test_row_to_analysis_csv_files():
    row = {**SAMPLE_ANALYSIS_ROW, "recommended_files": "go.mod, go.sum, main.go"}
    analysis = _row_to_analysis(row)
    assert analysis.recommended_files == ["go.mod", "go.sum", "main.go"]


def test_row_to_analysis_missing_fields():
    minimal = {
        "analysis_type": "conforma",
        "component_name": "comp-a",
    }
    analysis = _row_to_analysis(minimal)
    assert analysis.type == "conforma"
    assert analysis.recommended_files == []
    assert analysis.confidence_score == 0.0
    assert analysis.root_cause == ""
    assert analysis.tokens_used == 0
    assert analysis.cost_usd == 0.0


# --- Export endpoint: format=json ---


def test_export_json_build_failure(client, mock_build_repo, mock_conforma_repo, mock_ai_repo):
    mock_build_repo.get_failure_details.return_value = SAMPLE_FAILURE.copy()
    mock_ai_repo.get_analysis_by_component.return_value = None

    p1, p2, p3 = _patch_repos(mock_build_repo, mock_conforma_repo, mock_ai_repo)
    with p1, p2, p3:
        resp = client.get("/api/v1/applications/myapp/export/odh-dashboard?format=json")

    assert resp.status_code == 200
    data = json.loads(resp.text)
    assert data["type"] == "build_failure"
    assert data["failure"]["component_name"] == "odh-dashboard"


def test_export_json_conforma_violation(client, mock_build_repo, mock_conforma_repo, mock_ai_repo):
    mock_build_repo.get_failure_details.return_value = None
    mock_conforma_repo.get_violation_details.return_value = SAMPLE_VIOLATION.copy()
    mock_ai_repo.get_analysis_by_component.return_value = None

    p1, p2, p3 = _patch_repos(mock_build_repo, mock_conforma_repo, mock_ai_repo)
    with p1, p2, p3:
        resp = client.get("/api/v1/applications/myapp/export/odh-dashboard?format=json")

    data = json.loads(resp.text)
    assert data["type"] == "conforma_violation"
    assert data["violation"]["scenario"] == "rhoai-policy"


def test_export_json_not_found(client, mock_build_repo, mock_conforma_repo, mock_ai_repo):
    mock_build_repo.get_failure_details.return_value = None
    mock_conforma_repo.get_violation_details.return_value = None

    p1, p2, p3 = _patch_repos(mock_build_repo, mock_conforma_repo, mock_ai_repo)
    with p1, p2, p3:
        resp = client.get("/api/v1/applications/myapp/export/odh-dashboard?format=json")

    data = json.loads(resp.text)
    assert data["type"] == "not_found"


def test_export_json_include_logs_false(client, mock_build_repo, mock_conforma_repo, mock_ai_repo):
    failure = SAMPLE_FAILURE.copy()
    failure["build_logs"] = "big logs here"
    mock_build_repo.get_failure_details.return_value = failure
    mock_ai_repo.get_analysis_by_component.return_value = None

    p1, p2, p3 = _patch_repos(mock_build_repo, mock_conforma_repo, mock_ai_repo)
    with p1, p2, p3:
        resp = client.get(
            "/api/v1/applications/myapp/export/odh-dashboard?format=json&include_logs=false"
        )

    data = json.loads(resp.text)
    assert "build_logs" not in data["failure"]


# --- Export endpoint: format=jira ---


@patch("api.routes.exports.redact_secrets", side_effect=lambda x: x)
def test_export_jira_build_failure(mock_redact, client, mock_build_repo, mock_conforma_repo, mock_ai_repo):
    mock_build_repo.get_failure_details.return_value = SAMPLE_FAILURE.copy()
    mock_ai_repo.get_analysis_by_component.return_value = SAMPLE_ANALYSIS_ROW.copy()

    p1, p2, p3 = _patch_repos(mock_build_repo, mock_conforma_repo, mock_ai_repo)
    with p1, p2, p3:
        resp = client.get("/api/v1/applications/myapp/export/odh-dashboard?format=jira")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/plain; charset=utf-8"
    assert "h2. Build Failure" in resp.text
    assert "odh-dashboard" in resp.text


# --- Export endpoint: format=markdown ---


def test_export_markdown_build_failure(client, mock_build_repo, mock_conforma_repo, mock_ai_repo):
    mock_build_repo.get_failure_details.return_value = SAMPLE_FAILURE.copy()
    mock_ai_repo.get_analysis_by_component.return_value = SAMPLE_ANALYSIS_ROW.copy()

    p1, p2, p3 = _patch_repos(mock_build_repo, mock_conforma_repo, mock_ai_repo)
    with p1, p2, p3:
        resp = client.get("/api/v1/applications/myapp/export/odh-dashboard?format=markdown")

    assert resp.status_code == 200
    assert "## Build Failure" in resp.text
    assert "odh-dashboard" in resp.text
    assert "### AI Analysis" in resp.text


# --- Export endpoint: format=slack ---


@patch("api.routes.exports.redact_secrets", side_effect=lambda x: x)
@patch("api.routes.exports._fetch_open_prs", return_value=[])
def test_export_slack_build_failure(
    mock_prs, mock_redact, client, mock_build_repo, mock_conforma_repo, mock_ai_repo
):
    failure = SAMPLE_FAILURE.copy()
    failure["first_detected_at"] = datetime(2026, 6, 28, tzinfo=UTC)
    mock_build_repo.get_failure_details.return_value = failure
    mock_ai_repo.get_analysis_by_component.return_value = None

    p1, p2, p3 = _patch_repos(mock_build_repo, mock_conforma_repo, mock_ai_repo)
    with p1, p2, p3:
        resp = client.get("/api/v1/applications/myapp/export/odh-dashboard?format=slack")

    assert resp.status_code == 200
    assert ":red_circle:" in resp.text
    assert "odh-dashboard" in resp.text
