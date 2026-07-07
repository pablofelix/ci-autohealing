"""Tests for runtime configuration endpoints."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.config import router

app = FastAPI()
app.include_router(router, prefix="/api/v1")
client = TestClient(app)


# --- get_watched_applications ---


@patch("api.routes.config._repo")
def test_get_watched_applications_returns_list(mock_repo):
    repo = MagicMock()
    repo.get_watched_applications.return_value = ["rhoai-v2-19", "rhoai-v2-20"]
    mock_repo.return_value = repo

    response = client.get("/api/v1/config/applications")

    assert response.status_code == 200
    data = response.json()
    assert data["applications"] == ["rhoai-v2-19", "rhoai-v2-20"]
    repo.get_watched_applications.assert_called_once()


@patch("api.routes.config._repo")
def test_get_watched_applications_empty(mock_repo):
    repo = MagicMock()
    repo.get_watched_applications.return_value = []
    mock_repo.return_value = repo

    response = client.get("/api/v1/config/applications")

    assert response.status_code == 200
    assert response.json() == {"applications": []}


# --- add_watched_application ---


@patch("api.routes.config._repo")
def test_add_watched_application(mock_repo):
    repo = MagicMock()
    repo.add_watched_application.return_value = ["rhoai-v2-19", "rhoai-v2-20"]
    mock_repo.return_value = repo

    response = client.post(
        "/api/v1/config/applications",
        json={"application": "rhoai-v2-20"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["applications"] == ["rhoai-v2-19", "rhoai-v2-20"]
    repo.add_watched_application.assert_called_once_with("rhoai-v2-20", updated_by="api")


@patch("api.routes.config._repo")
def test_add_watched_application_returns_updated_list(mock_repo):
    repo = MagicMock()
    repo.add_watched_application.return_value = ["app-a"]
    mock_repo.return_value = repo

    response = client.post(
        "/api/v1/config/applications",
        json={"application": "app-a"},
    )

    assert response.status_code == 200
    assert response.json()["applications"] == ["app-a"]


def test_add_watched_application_missing_body():
    """POST without body returns 422."""
    response = client.post("/api/v1/config/applications")
    assert response.status_code == 422


# --- remove_watched_application ---


@patch("api.routes.config._repo")
def test_remove_watched_application(mock_repo):
    repo = MagicMock()
    repo.remove_watched_application.return_value = ["rhoai-v2-19"]
    mock_repo.return_value = repo

    response = client.delete("/api/v1/config/applications/rhoai-v2-20")

    assert response.status_code == 200
    data = response.json()
    assert data["applications"] == ["rhoai-v2-19"]
    repo.remove_watched_application.assert_called_once_with("rhoai-v2-20", updated_by="api")


@patch("api.routes.config._repo")
def test_remove_watched_application_returns_empty_after_last(mock_repo):
    repo = MagicMock()
    repo.remove_watched_application.return_value = []
    mock_repo.return_value = repo

    response = client.delete("/api/v1/config/applications/only-app")

    assert response.status_code == 200
    assert response.json() == {"applications": []}


# --- get_all_config ---


@patch("api.routes.config._repo")
def test_get_all_config_returns_list(mock_repo):
    repo = MagicMock()
    repo.get_all.return_value = [
        {"key": "watched_applications", "value": ["rhoai-v2-19"]},
        {"key": "refresh_interval", "value": 300},
    ]
    mock_repo.return_value = repo

    response = client.get("/api/v1/config")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["key"] == "watched_applications"
    assert data[1]["key"] == "refresh_interval"
    repo.get_all.assert_called_once()


@patch("api.routes.config._repo")
def test_get_all_config_empty(mock_repo):
    repo = MagicMock()
    repo.get_all.return_value = []
    mock_repo.return_value = repo

    response = client.get("/api/v1/config")

    assert response.status_code == 200
    assert response.json() == []
