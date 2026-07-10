"""Tests for live status overlay — IC client, service, and route."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ─── IC Client ────────────────────────────────────────────────────────────────


class TestICClient:
    def _make_client(self):
        from map.backend.ic_client import ICClient
        return ICClient(base_url="http://test:8000")

    @patch("map.backend.ic_client.requests.Session")
    def test_get_alerts_success(self, mock_session_cls):
        session = MagicMock()
        mock_session_cls.return_value = session
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"build_failures": [], "conforma_violations": []}
        session.get.return_value = resp

        client = self._make_client()
        result = client.get_alerts("rhoai-v3-5")
        assert result is not None
        assert "build_failures" in result

    @patch("map.backend.ic_client.requests.Session")
    def test_get_alerts_server_error_returns_none(self, mock_session_cls):
        session = MagicMock()
        mock_session_cls.return_value = session
        session.get.return_value = MagicMock(status_code=500)

        client = self._make_client()
        assert client.get_alerts("rhoai-v3-5") is None

    @patch("map.backend.ic_client.requests.Session")
    def test_get_alerts_timeout_returns_none(self, mock_session_cls):
        import requests
        session = MagicMock()
        mock_session_cls.return_value = session
        session.get.side_effect = requests.ConnectionError("timeout")

        client = self._make_client()
        assert client.get_alerts("rhoai-v3-5") is None

    @patch("map.backend.ic_client.requests.Session")
    def test_is_available_true(self, mock_session_cls):
        session = MagicMock()
        mock_session_cls.return_value = session
        session.get.return_value = MagicMock(status_code=200)

        client = self._make_client()
        assert client.is_available() is True

    @patch("map.backend.ic_client.requests.Session")
    def test_is_available_false_on_error(self, mock_session_cls):
        import requests
        session = MagicMock()
        mock_session_cls.return_value = session
        session.get.side_effect = requests.ConnectionError()

        client = self._make_client()
        assert client.is_available() is False


# ─── Live Status Service ─────────────────────────────────────────────────────


SAMPLE_ALERTS = {
    "build_failures": [
        {
            "component": "odh-vllm-cpu-v3-5",
            "status": "Failed",
            "error_type": "build_error",
            "last_seen": "2026-07-09T10:00:00Z",
        },
    ],
    "conforma_violations": [
        {
            "component": "odh-dashboard-v3-5",
            "status": "Conforma Violation",
            "error_type": "verify-conforma-lp-rhoai",
            "last_seen": "2026-07-09T09:00:00Z",
            "blocks": "stage+prod",
        },
        {
            "component": "odh-notebook-v3-5",
            "status": "Conforma Violation",
            "error_type": "verify-conforma-lp-rhoai",
            "last_seen": "2026-07-09T08:00:00Z",
            "blocks": "none",
        },
    ],
}

SAMPLE_HEALTH = [
    {"component_name": "odh-dashboard-v3-5", "health_score": 90},
    {"component_name": "odh-vllm-cpu-v3-5", "health_score": 30},
    {"component_name": "odh-notebook-v3-5", "health_score": 75},
]

SAMPLE_READINESS = {
    "verdict": "AT_RISK",
    "build_failures": 1,
    "conforma_violations": 2,
}


class TestLiveStatusService:
    def _make_service(self, alerts=None, health=None, readiness=None):
        from map.backend.live_status_service import LiveStatusService
        client = MagicMock()
        client.get_alerts.return_value = alerts
        client.get_health.return_value = health
        client.get_readiness.return_value = readiness
        return LiveStatusService(client=client, cache_ttl=0)

    def test_healthy_component_gets_green(self):
        service = self._make_service(health=SAMPLE_HEALTH)
        result = service.get_live_status("rhoai-v3-5")
        nodes = {n["node_id"]: n for n in result["nodes"]}
        assert nodes["comp-odh-dashboard-v3-5"]["status"] == "healthy"
        assert nodes["comp-odh-dashboard-v3-5"]["border_color"] == "#10b981"

    def test_failing_component_gets_red(self):
        service = self._make_service(health=SAMPLE_HEALTH)
        result = service.get_live_status("rhoai-v3-5")
        nodes = {n["node_id"]: n for n in result["nodes"]}
        assert nodes["comp-odh-vllm-cpu-v3-5"]["status"] == "failing"
        assert nodes["comp-odh-vllm-cpu-v3-5"]["border_color"] == "#ef4444"

    def test_degraded_component_gets_yellow(self):
        service = self._make_service(health=SAMPLE_HEALTH)
        result = service.get_live_status("rhoai-v3-5")
        nodes = {n["node_id"]: n for n in result["nodes"]}
        assert nodes["comp-odh-notebook-v3-5"]["status"] == "degraded"
        assert nodes["comp-odh-notebook-v3-5"]["border_color"] == "#f59e0b"

    def test_build_failure_overrides_health(self):
        service = self._make_service(alerts=SAMPLE_ALERTS, health=SAMPLE_HEALTH)
        result = service.get_live_status("rhoai-v3-5")
        nodes = {n["node_id"]: n for n in result["nodes"]}
        vllm = nodes["comp-odh-vllm-cpu-v3-5"]
        assert vllm["status"] == "failing"

    def test_blocking_conforma_sets_degraded(self):
        service = self._make_service(alerts=SAMPLE_ALERTS, health=SAMPLE_HEALTH)
        result = service.get_live_status("rhoai-v3-5")
        nodes = {n["node_id"]: n for n in result["nodes"]}
        dashboard = nodes["comp-odh-dashboard-v3-5"]
        assert dashboard["status"] == "degraded"

    def test_non_blocking_conforma_ignored_uses_health_score(self):
        """blocks='none' is ignored — status comes from health_score=75 (degraded)."""
        service = self._make_service(alerts=SAMPLE_ALERTS, health=SAMPLE_HEALTH)
        result = service.get_live_status("rhoai-v3-5")
        nodes = {n["node_id"]: n for n in result["nodes"]}
        notebook = nodes["comp-odh-notebook-v3-5"]
        assert notebook["status"] == "degraded"
        assert "conforma" not in notebook.get("detail", "").lower()

    def test_readiness_verdict_sets_app_status(self):
        service = self._make_service(readiness=SAMPLE_READINESS)
        result = service.get_live_status("rhoai-v3-5")
        nodes = {n["node_id"]: n for n in result["nodes"]}
        app = nodes["app-rhoai-v3-5"]
        assert app["status"] == "degraded"
        assert app["node_type"] == "Application"

    def test_ready_verdict_is_green(self):
        service = self._make_service(readiness={"verdict": "READY"})
        result = service.get_live_status("rhoai-v3-5")
        nodes = {n["node_id"]: n for n in result["nodes"]}
        assert nodes["app-rhoai-v3-5"]["status"] == "healthy"

    def test_ic_unavailable_returns_empty(self):
        service = self._make_service()
        result = service.get_live_status("rhoai-v3-5")
        assert result["ic_available"] is False
        assert result["nodes"] == []
        assert result["activity"] == []

    def test_activity_feed_from_alerts(self):
        service = self._make_service(alerts=SAMPLE_ALERTS)
        result = service.get_live_status("rhoai-v3-5")
        assert len(result["activity"]) == 3
        assert result["activity"][0]["severity"] == "error"
        assert result["activity"][0]["event_type"] == "build_failure"

    def test_activity_feed_sorted_by_timestamp_desc(self):
        service = self._make_service(alerts=SAMPLE_ALERTS)
        result = service.get_live_status("rhoai-v3-5")
        timestamps = [e["timestamp"] for e in result["activity"]]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_cache_returns_same_data(self):
        from map.backend.live_status_service import LiveStatusService
        client = MagicMock()
        client.get_alerts.return_value = SAMPLE_ALERTS
        client.get_health.return_value = None
        client.get_readiness.return_value = None
        service = LiveStatusService(client=client, cache_ttl=300)

        first = service.get_live_status("rhoai-v3-5")
        second = service.get_live_status("rhoai-v3-5")

        assert first is second
        assert client.get_alerts.call_count == 1


# ─── Route ────────────────────────────────────────────────────────────────────


class TestLiveStatusRoute:
    @pytest.fixture
    def client(self):
        with patch("map.backend.graph.get_driver"):
            from map.backend.main import app
            yield TestClient(app)

    def test_live_status_endpoint_returns_json(self, client):
        mock_result = {
            "application": "rhoai-v3-5",
            "nodes": [],
            "activity": [],
            "ic_available": False,
            "last_updated": None,
        }
        with patch(
            "map.backend.live_status_service.LiveStatusService.get_live_status",
            return_value=mock_result,
        ):
            resp = client.get("/api/map/live-status?application=rhoai-v3-5")
        assert resp.status_code == 200
        data = resp.json()
        assert "nodes" in data
        assert "activity" in data
        assert "ic_available" in data

    def test_live_status_default_application(self, client):
        resp = client.get("/api/map/live-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["application"] == "rhoai-v3-5"

    def test_live_status_graceful_on_service_error(self, client):
        with patch(
            "map.backend.live_status_service.LiveStatusService.get_live_status",
            side_effect=ConnectionError("IC down"),
        ):
            resp = client.get("/api/map/live-status")
            assert resp.status_code == 200
            data = resp.json()
            assert data["ic_available"] is False
            assert data["nodes"] == []
