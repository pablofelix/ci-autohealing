"""Tests for src/api/routes/rebuilds.py — rebuild trigger endpoint."""

from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.rebuilds import router

app = FastAPI()
app.include_router(router, prefix="/api/v1")
client = TestClient(app)


@patch("api.routes.rebuilds.NAMESPACE", "test-ns")
@patch("clients.kubernetes.KubernetesClient")
def test_rebuild_triggers_k8s(mock_kc_cls):
    mock_kc = MagicMock()
    mock_kc_cls.return_value = mock_kc
    mock_kc.trigger_rebuild.return_value = True

    resp = client.post("/api/v1/applications/rhoai-v3-5/rebuild/odh-dashboard")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "triggered"
    assert data["component"] == "odh-dashboard"
    mock_kc.trigger_rebuild.assert_called_once_with("odh-dashboard", namespace="test-ns")


@patch("api.routes.rebuilds.NAMESPACE", "test-ns")
@patch("clients.kubernetes.KubernetesClient")
def test_rebuild_invalid_component(mock_kc_cls):
    mock_kc = MagicMock()
    mock_kc_cls.return_value = mock_kc
    mock_kc.trigger_rebuild.side_effect = ValueError("Invalid component name: 'bad!!name'")

    resp = client.post("/api/v1/applications/rhoai-v3-5/rebuild/bad!!name")
    assert resp.status_code == 400


@patch("api.routes.rebuilds.NAMESPACE", "")
def test_rebuild_no_namespace():
    resp = client.post("/api/v1/applications/rhoai-v3-5/rebuild/odh-dashboard")
    assert resp.status_code == 400
    assert "namespace" in resp.json()["detail"].lower()


@patch("api.routes.rebuilds.NAMESPACE", "test-ns")
@patch("clients.kubernetes.KubernetesClient")
def test_rebuild_k8s_error(mock_kc_cls):
    mock_kc = MagicMock()
    mock_kc_cls.return_value = mock_kc
    mock_kc.trigger_rebuild.side_effect = RuntimeError("cluster unreachable")

    resp = client.post("/api/v1/applications/rhoai-v3-5/rebuild/odh-dashboard")
    assert resp.status_code == 502


@patch("api.routes.rebuilds.NAMESPACE", "test-ns")
@patch("clients.kubernetes.KubernetesClient")
def test_rebuild_with_namespace_override(mock_kc_cls):
    mock_kc = MagicMock()
    mock_kc_cls.return_value = mock_kc
    mock_kc.trigger_rebuild.return_value = True

    resp = client.post(
        "/api/v1/applications/rhoai-v3-5/rebuild/comp-x",
        json={"namespace": "custom-ns"},
    )
    assert resp.status_code == 200
    mock_kc.trigger_rebuild.assert_called_once_with("comp-x", namespace="custom-ns")
