"""Tests for the triage tracking endpoints."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import triage

app = FastAPI()
app.include_router(triage.router, prefix="/api/v1")
client = TestClient(app)


@pytest.fixture(autouse=True)
def mock_repo():
    repo = MagicMock()
    with patch("api.routes.triage._triage_repo", return_value=repo):
        yield repo


# --- GET /applications/{app}/triage ---


def test_get_triage_report(mock_repo):
    mock_repo.get_report.return_value = [{"id": 1, "components": ["comp-a"]}]
    mock_repo.get_summary.return_value = {"active": 1, "resolved": 0}

    resp = client.get("/api/v1/applications/rhoai/triage")

    assert resp.status_code == 200
    data = resp.json()
    assert data["application"] == "rhoai"
    assert data["summary"] == {"active": 1, "resolved": 0}
    assert len(data["items"]) == 1
    mock_repo.get_report.assert_called_once_with("rhoai", date=None)


def test_get_triage_report_alt_returns_same(mock_repo):
    mock_repo.get_report.return_value = []
    mock_repo.get_summary.return_value = {"active": 0, "resolved": 0}

    resp = client.get("/api/v1/applications/rhoai/triage/report")

    assert resp.status_code == 200
    data = resp.json()
    assert data["application"] == "rhoai"
    assert data["summary"] == {"active": 0, "resolved": 0}


# --- GET /applications/{app}/triage/{item_id} ---


def test_get_triage_item_found(mock_repo):
    mock_repo.get_by_id.return_value = {"id": 5, "components": ["comp-x"]}

    resp = client.get("/api/v1/applications/rhoai/triage/5")

    assert resp.status_code == 200
    assert resp.json()["id"] == 5
    mock_repo.get_by_id.assert_called_once_with(5)


def test_get_triage_item_not_found(mock_repo):
    mock_repo.get_by_id.return_value = None

    resp = client.get("/api/v1/applications/rhoai/triage/99")

    assert resp.status_code == 404


# --- POST /applications/{app}/triage ---


def test_track_create_new_item(mock_repo):
    mock_repo.find_by_component.return_value = None
    mock_repo.create_item.return_value = 10

    resp = client.post("/api/v1/applications/rhoai/triage", json={
        "component": "odh-dashboard",
        "root_cause": "flaky test",
        "failed_step": "build",
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "created"
    assert data["item_id"] == 10
    assert data["component"] == "odh-dashboard"
    mock_repo.create_item.assert_called_once()


def test_track_component_already_exists(mock_repo):
    mock_repo.find_by_component.return_value = {"id": 3, "components": ["comp-a"]}

    resp = client.post("/api/v1/applications/rhoai/triage", json={
        "component": "comp-a",
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "exists"
    assert data["item"]["id"] == 3
    mock_repo.create_item.assert_not_called()


def test_track_add_to_existing_item(mock_repo):
    mock_repo.get_by_id.return_value = {"id": 2, "components": ["comp-a"]}

    resp = client.post("/api/v1/applications/rhoai/triage", json={
        "component": "comp-b",
        "add_to_id": 2,
        "slack_thread_url": "https://slack.example.com/t/123",
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "added"
    assert data["item_id"] == 2
    mock_repo.update_item.assert_called_once_with(2, components=["comp-a", "comp-b"])
    mock_repo.add_slack_url.assert_called_once_with(2, "https://slack.example.com/t/123")


def test_track_add_to_id_not_found(mock_repo):
    mock_repo.get_by_id.return_value = None

    resp = client.post("/api/v1/applications/rhoai/triage", json={
        "component": "comp-b",
        "add_to_id": 999,
    })

    assert resp.status_code == 404


# --- PUT /applications/{app}/triage/{item_id} ---


def test_update_triage_item(mock_repo):
    mock_repo.get_by_id.return_value = {"id": 1, "components": ["comp-a"]}

    resp = client.put("/api/v1/applications/rhoai/triage/1", json={
        "jira_key": "RHOAI-1234",
        "notes": "investigating",
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "updated"
    assert data["item_id"] == 1
    assert "jira_key" in data["updates"]
    assert "notes" in data["updates"]
    mock_repo.update_item.assert_called_once()


def test_update_triage_item_not_found(mock_repo):
    mock_repo.get_by_id.return_value = None

    resp = client.put("/api/v1/applications/rhoai/triage/99", json={
        "notes": "test",
    })

    assert resp.status_code == 404


def test_update_triage_item_no_updates(mock_repo):
    mock_repo.get_by_id.return_value = {"id": 1, "components": ["comp-a"]}

    resp = client.put("/api/v1/applications/rhoai/triage/1", json={})

    assert resp.status_code == 400


# --- POST /applications/{app}/triage/{item_id}/resolve ---


def test_resolve_triage_item(mock_repo):
    mock_repo.get_by_id.return_value = {"id": 1, "components": ["comp-a"]}

    resp = client.post("/api/v1/applications/rhoai/triage/1/resolve", json={
        "resolution": "PR merged",
        "resolution_pr_url": "https://github.com/org/repo/pull/42",
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "resolved"
    assert data["item_id"] == 1
    mock_repo.resolve_item.assert_called_once_with(
        1, resolution="PR merged", pr_url="https://github.com/org/repo/pull/42"
    )


def test_resolve_triage_item_not_found(mock_repo):
    mock_repo.get_by_id.return_value = None

    resp = client.post("/api/v1/applications/rhoai/triage/99/resolve", json={})

    assert resp.status_code == 404


@patch("repositories.repository_factory.get_repository")
def test_resolve_with_verdict_records_ai_verdict(mock_get_repo, mock_repo):
    mock_repo.get_by_id.return_value = {"id": 1, "components": ["comp-a"]}
    mock_ai_repo = MagicMock()
    mock_get_repo.return_value = mock_ai_repo

    resp = client.post("/api/v1/applications/rhoai/triage/1/resolve", json={
        "verdict": "correct",
    })

    assert resp.status_code == 200
    mock_ai_repo.record_verdict.assert_called_once_with(
        "comp-a", "rhoai", "correct", verdict_by="api"
    )
