"""Tests for fix attempt endpoints."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.fixes import router


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    return TestClient(app)


@pytest.fixture
def mock_repo():
    repo = MagicMock()
    repo.get_all.return_value = []
    repo.get_outcome_summary.return_value = {}
    return repo


def test_get_fix_history_default_days(client, mock_repo):
    """Default request uses 30 days."""
    mock_repo.get_all.return_value = [{"id": 1, "outcome": "success"}]
    mock_repo.get_outcome_summary.return_value = {"success": 1, "failure": 0}

    with patch("api.routes.fixes._resolution_repo", return_value=mock_repo):
        resp = client.get("/api/v1/fixes")

    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"] == {"success": 1, "failure": 0}
    assert data["attempts"] == [{"id": 1, "outcome": "success"}]
    mock_repo.get_all.assert_called_once_with(30)
    mock_repo.get_outcome_summary.assert_called_once_with(30)


def test_get_fix_history_custom_days(client, mock_repo):
    """Custom days parameter is forwarded to repo methods."""
    mock_repo.get_all.return_value = [{"id": 2}]
    mock_repo.get_outcome_summary.return_value = {"success": 0, "failure": 1}

    with patch("api.routes.fixes._resolution_repo", return_value=mock_repo):
        resp = client.get("/api/v1/fixes?days=7")

    assert resp.status_code == 200
    mock_repo.get_all.assert_called_once_with(7)
    mock_repo.get_outcome_summary.assert_called_once_with(7)


def test_get_fix_history_empty_results(client, mock_repo):
    """Empty results return empty summary and attempts."""
    with patch("api.routes.fixes._resolution_repo", return_value=mock_repo):
        resp = client.get("/api/v1/fixes")

    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"] == {}
    assert data["attempts"] == []


def test_get_fix_history_calls_both_repo_methods(client, mock_repo):
    """Both get_all and get_outcome_summary are called with the same days arg."""
    with patch("api.routes.fixes._resolution_repo", return_value=mock_repo):
        client.get("/api/v1/fixes?days=14")

    mock_repo.get_all.assert_called_once_with(14)
    mock_repo.get_outcome_summary.assert_called_once_with(14)
