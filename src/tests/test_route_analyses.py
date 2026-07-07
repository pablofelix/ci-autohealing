"""Tests for AI analysis endpoints."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.errors import register_error_handlers
from api.routes.analyses import _row_to_analysis, router


@pytest.fixture
def client():
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router, prefix="/api/v1")
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def mock_ai_repo():
    return MagicMock()


# --- list_analyses ---

def test_list_analyses(client, mock_ai_repo):
    """GET /applications/{app}/analyses returns recent analyses."""
    mock_ai_repo.get_recent_analyses.return_value = [
        {"id": 1, "component": "comp-a"},
        {"id": 2, "component": "comp-b"},
    ]
    with patch("api.routes.analyses._ai_repo", return_value=mock_ai_repo):
        resp = client.get("/api/v1/applications/myapp/analyses?days=7&limit=5")

    assert resp.status_code == 200
    assert len(resp.json()) == 2
    mock_ai_repo.get_recent_analyses.assert_called_once_with("myapp", days=7, limit=5)


# --- get_analysis_stats ---

def test_get_analysis_stats(client, mock_ai_repo):
    """GET /applications/{app}/analyses/stats returns category stats."""
    mock_ai_repo.get_category_stats.return_value = {"build": 5, "conforma": 3}
    with patch("api.routes.analyses._ai_repo", return_value=mock_ai_repo):
        resp = client.get("/api/v1/applications/myapp/analyses/stats")

    assert resp.status_code == 200
    assert resp.json() == {"build": 5, "conforma": 3}
    mock_ai_repo.get_category_stats.assert_called_once_with("myapp")


# --- get_analysis ---

def test_get_analysis_found(client, mock_ai_repo):
    """GET /applications/{app}/analyses/{component} returns AnalysisDetails when found."""
    mock_ai_repo.get_analysis_by_component.return_value = {
        "analysis_type": "build",
        "component_name": "comp-a",
        "model_used": "claude-sonnet-4-20250514",
        "root_cause": "missing dep",
        "failure_category": "dependency",
        "confidence_score": 0.85,
        "recommended_fix": "add dep X",
        "recommended_files": ["go.mod"],
        "can_auto_fix": False,
        "requires_human_review": True,
        "analyzed_at": "2026-06-15T10:00:00",
        "tokens_used": 500,
        "cost_usd": 0.01,
    }
    with patch("api.routes.analyses._ai_repo", return_value=mock_ai_repo):
        resp = client.get("/api/v1/applications/myapp/analyses/comp-a?type=build")

    assert resp.status_code == 200
    data = resp.json()
    assert data["component"] == "comp-a"
    assert data["type"] == "build"
    assert data["confidence_score"] == 0.85
    assert data["recommended_files"] == ["go.mod"]
    mock_ai_repo.get_analysis_by_component.assert_called_once_with("comp-a", "myapp", "build")


def test_get_analysis_not_found(client, mock_ai_repo):
    """GET /applications/{app}/analyses/{component} returns null when not found."""
    mock_ai_repo.get_analysis_by_component.return_value = None
    with patch("api.routes.analyses._ai_repo", return_value=mock_ai_repo):
        resp = client.get("/api/v1/applications/myapp/analyses/missing-comp")

    assert resp.status_code == 200
    assert resp.json() is None


# --- submit_analysis ---

def test_submit_analysis_happy_path(client, mock_ai_repo):
    """POST /applications/{app}/analyses stores analysis when failure exists."""
    mock_build_repo = MagicMock()
    mock_build_repo.get_failure_details.return_value = {"id": 42}
    mock_ai_repo.insert_analysis.return_value = 99

    with (
        patch("api.routes.analyses._ai_repo", return_value=mock_ai_repo),
        patch("api.routes.analyses.get_repository", return_value=mock_build_repo),
    ):
        resp = client.post(
            "/api/v1/applications/myapp/analyses",
            json={
                "component": "comp-a",
                "analysis_type": "build",
                "model_used": "claude-sonnet-4-20250514",
                "root_cause": "test failure",
                "confidence_score": 0.9,
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "stored"
    assert data["analysis_id"] == 99
    assert data["component"] == "comp-a"
    assert data["confidence"] == 0.9


def test_submit_analysis_failure_not_found(client, mock_ai_repo):
    """POST /applications/{app}/analyses returns 404 when component failure doesn't exist."""
    mock_build_repo = MagicMock()
    mock_build_repo.get_failure_details.return_value = None

    with (
        patch("api.routes.analyses._ai_repo", return_value=mock_ai_repo),
        patch("api.routes.analyses.get_repository", return_value=mock_build_repo),
    ):
        resp = client.post(
            "/api/v1/applications/myapp/analyses",
            json={"component": "ghost-comp"},
        )

    assert resp.status_code == 404
    assert "not_found" in resp.json().get("error", "")


# --- submit_verdict ---

def test_submit_verdict_happy_path(client):
    """POST verdict records it and returns confirmation."""
    mock_repo = MagicMock()
    mock_repo.record_verdict.return_value = True
    with patch("api.routes.analyses._repo", return_value=mock_repo):
        resp = client.post(
            "/api/v1/applications/myapp/analyses/comp-a/verdict",
            json={"verdict": "correct"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "verdict_recorded"
    assert data["verdict"] == "correct"
    assert data["component"] == "comp-a"
    mock_repo.record_verdict.assert_called_once_with(
        "comp-a", "myapp", "correct", actual_root_cause=None, verdict_by="api"
    )


def test_submit_verdict_not_found(client):
    """POST verdict returns 404 when no analysis exists for the component."""
    mock_repo = MagicMock()
    mock_repo.record_verdict.return_value = False
    with patch("api.routes.analyses._repo", return_value=mock_repo):
        resp = client.post(
            "/api/v1/applications/myapp/analyses/missing/verdict",
            json={"verdict": "incorrect", "actual_root_cause": "was OOM"},
        )

    assert resp.status_code == 404


# --- get_ai_quality ---

def test_get_ai_quality(client):
    """GET /metrics/ai-quality returns quality metrics dict."""
    mock_repo = MagicMock()
    mock_repo.get_quality_metrics.return_value = {
        "total": 10,
        "correct": 7,
        "accuracy": 0.7,
    }
    with patch("api.routes.analyses._repo", return_value=mock_repo):
        resp = client.get("/api/v1/metrics/ai-quality?application=myapp&days=14")

    assert resp.status_code == 200
    assert resp.json()["accuracy"] == 0.7
    mock_repo.get_quality_metrics.assert_called_once_with(application="myapp", days=14)


# --- _row_to_analysis ---

def test_row_to_analysis_files_as_list():
    """_row_to_analysis passes through a list of recommended_files."""
    row = {
        "analysis_type": "build",
        "component_name": "comp",
        "model_used": "m",
        "root_cause": "r",
        "failure_category": "c",
        "confidence_score": 0.5,
        "recommended_fix": "f",
        "recommended_files": ["a.py", "b.py"],
        "can_auto_fix": True,
        "requires_human_review": False,
        "analyzed_at": datetime(2026, 1, 1),
        "tokens_used": 100,
        "cost_usd": 0.005,
    }
    result = _row_to_analysis(row)
    assert result.recommended_files == ["a.py", "b.py"]
    assert result.can_auto_fix is True


def test_row_to_analysis_files_as_string():
    """_row_to_analysis splits a comma-separated string into a list."""
    row = {
        "analysis_type": "conforma",
        "component_name": "comp",
        "model_used": "m",
        "root_cause": "r",
        "failure_category": "c",
        "confidence_score": 0.0,
        "recommended_fix": "f",
        "recommended_files": "x.go, y.go, z.go",
        "can_auto_fix": False,
        "requires_human_review": True,
        "analyzed_at": datetime(2026, 1, 1),
        "tokens_used": 0,
        "cost_usd": 0,
    }
    result = _row_to_analysis(row)
    assert result.recommended_files == ["x.go", "y.go", "z.go"]
    assert result.type == "conforma"
