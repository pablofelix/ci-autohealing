"""Verify triage endpoints return ICError-shaped responses, not HTTPException."""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


def _make_app():
    from fastapi import FastAPI

    from api.errors import register_error_handlers
    from api.routes.triage import router
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router, prefix="/api")
    return app


@pytest.fixture
def client():
    return TestClient(_make_app())


@patch("api.routes.triage._triage_repo")
def test_get_item_not_found_returns_ic_error_shape(mock_repo, client):
    mock_repo.return_value.get_by_id.return_value = None
    resp = client.get("/api/applications/rhoai-v3-5/triage/999")
    assert resp.status_code == 404
    body = resp.json()
    assert "error" in body
    assert body["error"] == "not_found"


@patch("api.routes.triage._triage_repo")
def test_update_no_changes_returns_ic_error_shape(mock_repo, client):
    mock_repo.return_value.get_by_id.return_value = {"id": 1, "components": ["x"]}
    resp = client.put("/api/applications/rhoai-v3-5/triage/1", json={})
    assert resp.status_code == 422
    body = resp.json()
    assert "error" in body
    assert body["error"] == "validation_error"
