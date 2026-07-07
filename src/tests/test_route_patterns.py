"""Tests for the error pattern endpoints."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import patterns

app = FastAPI()
app.include_router(patterns.router, prefix="/api/v1")
client = TestClient(app)


SAMPLE_PATTERNS = [
    {
        "name": "oom-killed",
        "failure_type": "infrastructure",
        "description": "Container killed due to OOM",
        "typical_fix": "Increase memory limits",
    },
    {
        "name": "image-pull-backoff",
        "failure_type": "infrastructure",
        "description": "Failed to pull container image",
        "typical_fix": "Check image reference and registry credentials",
    },
    {
        "name": "test-flake",
        "failure_type": "test",
        "description": "Intermittent test failure",
        "typical_fix": "Retry or fix test isolation",
    },
]


@patch("api.routes.patterns._pattern_repo")
def test_list_patterns_returns_all(mock_repo):
    mock_repo.return_value.get_all.return_value = SAMPLE_PATTERNS
    response = client.get("/api/v1/patterns")
    assert response.status_code == 200
    assert response.json() == SAMPLE_PATTERNS
    mock_repo.return_value.get_all.assert_called_once_with(None)


@patch("api.routes.patterns._pattern_repo")
def test_list_patterns_with_failure_type_filter(mock_repo):
    infra_patterns = [p for p in SAMPLE_PATTERNS if p["failure_type"] == "infrastructure"]
    mock_repo.return_value.get_all.return_value = infra_patterns
    response = client.get("/api/v1/patterns", params={"failure_type": "infrastructure"})
    assert response.status_code == 200
    assert len(response.json()) == 2
    mock_repo.return_value.get_all.assert_called_once_with("infrastructure")


@patch("api.routes.patterns._pattern_repo")
def test_list_patterns_empty_result(mock_repo):
    mock_repo.return_value.get_all.return_value = []
    response = client.get("/api/v1/patterns", params={"failure_type": "nonexistent"})
    assert response.status_code == 200
    assert response.json() == []


@patch("api.routes.patterns._pattern_repo")
def test_get_pattern_found(mock_repo):
    mock_repo.return_value.get_by_name_or_category.return_value = SAMPLE_PATTERNS[0]
    response = client.get("/api/v1/patterns/oom-killed")
    assert response.status_code == 200
    assert response.json()["name"] == "oom-killed"
    mock_repo.return_value.get_by_name_or_category.assert_called_once_with("oom-killed")


@patch("api.routes.patterns._pattern_repo")
def test_get_pattern_not_found(mock_repo):
    mock_repo.return_value.get_by_name_or_category.return_value = None
    response = client.get("/api/v1/patterns/unknown")
    assert response.status_code == 200
    assert response.json() is None
    mock_repo.return_value.get_by_name_or_category.assert_called_once_with("unknown")
