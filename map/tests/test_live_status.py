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


SAMPLE_ONBOARDING = {
    "application": "rhoai-v3-5",
    "total": 3,
    "complete": 1,
    "partial": 1,
    "incomplete": 1,
    "components": [
        {
            "component": "odh-dashboard-v3-5",
            "overall": "complete",
            "score": 100,
            "checks": {
                "repository": {"status": "PASS", "detail": "https://github.com/opendatahub-io/odh-dashboard"},
                "branch": {"status": "PASS", "detail": "rhoai-3.5"},
                "container_image": {"status": "PASS", "detail": "quay.io/rhoai/odh-dashboard"},
                "pac": {"status": "PASS", "detail": "PaC Repository CR found"},
                "builds": {"status": "PASS", "detail": "Latest build succeeded"},
                "last_built": {"status": "PASS", "detail": "Last built commit: abc123"},
                "nudges": {"status": "PASS", "detail": "3 nudge(s) configured"},
            },
            "failing": [],
            "warnings": [],
        },
        {
            "component": "odh-vllm-cpu-v3-5",
            "overall": "partial",
            "score": 75,
            "checks": {
                "repository": {"status": "PASS", "detail": "https://github.com/vllm-project/vllm"},
                "branch": {"status": "PASS", "detail": "rhoai-3.5"},
                "container_image": {"status": "PASS", "detail": "quay.io/rhoai/vllm"},
                "pac": {"status": "WARN", "detail": "No PaC Repository CR found", "fix": "Create a PaC Repository CR"},
                "builds": {"status": "PASS", "detail": "Latest build succeeded"},
                "last_built": {"status": "PASS", "detail": "Last built commit: def456"},
                "nudges": {"status": "INFO", "detail": "No nudges configured"},
            },
            "failing": [],
            "warnings": ["pac"],
            "jira_key": "RHOAI-12345",
        },
        {
            "component": "odh-notebook-v3-5",
            "overall": "incomplete",
            "score": 35,
            "checks": {
                "repository": {"status": "PASS", "detail": "https://github.com/opendatahub-io/notebooks"},
                "branch": {"status": "FAIL", "detail": "No branch configured", "fix": "Set spec.source.git.revision"},
                "container_image": {"status": "FAIL", "detail": "No container image configured", "fix": "Set spec.containerImage"},
                "pac": {"status": "SKIP", "detail": "No repository URL to match"},
                "builds": {"status": "FAIL", "detail": "No builds found", "fix": "Trigger an initial build"},
                "last_built": {"status": "WARN", "detail": "No successful build recorded"},
                "nudges": {"status": "INFO", "detail": "No nudges configured"},
            },
            "failing": ["branch", "container_image", "builds"],
            "warnings": ["last_built"],
        },
    ],
}


class TestLiveStatusService:
    def _make_service(self, alerts=None, health=None, readiness=None, onboarding=None):
        from map.backend.live_status_service import LiveStatusService
        client = MagicMock()
        client.get_alerts.return_value = alerts
        client.get_health.return_value = health
        client.get_readiness.return_value = readiness
        client.get_onboarding.return_value = onboarding
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

    def test_onboarding_included_in_response(self):
        service = self._make_service(onboarding=SAMPLE_ONBOARDING)
        result = service.get_live_status("rhoai-v3-5")
        assert "onboarding" in result
        assert len(result["onboarding"]) == 3

    def test_onboarding_complete_gets_green(self):
        service = self._make_service(onboarding=SAMPLE_ONBOARDING)
        result = service.get_live_status("rhoai-v3-5")
        ob_map = {o["node_id"]: o for o in result["onboarding"]}
        dashboard = ob_map["comp-odh-dashboard-v3-5"]
        assert dashboard["score"] == 100
        assert dashboard["overall"] == "complete"
        assert dashboard["badge_color"] == "#10b981"

    def test_onboarding_partial_gets_yellow(self):
        service = self._make_service(onboarding=SAMPLE_ONBOARDING)
        result = service.get_live_status("rhoai-v3-5")
        ob_map = {o["node_id"]: o for o in result["onboarding"]}
        vllm = ob_map["comp-odh-vllm-cpu-v3-5"]
        assert vllm["score"] == 75
        assert vllm["overall"] == "partial"
        assert vllm["badge_color"] == "#f59e0b"
        assert vllm["jira_key"] == "RHOAI-12345"

    def test_onboarding_incomplete_gets_red(self):
        service = self._make_service(onboarding=SAMPLE_ONBOARDING)
        result = service.get_live_status("rhoai-v3-5")
        ob_map = {o["node_id"]: o for o in result["onboarding"]}
        notebook = ob_map["comp-odh-notebook-v3-5"]
        assert notebook["score"] == 35
        assert notebook["overall"] == "incomplete"
        assert notebook["badge_color"] == "#ef4444"
        assert "branch" in notebook["failing"]
        assert "container_image" in notebook["failing"]

    def test_onboarding_checks_preserved(self):
        service = self._make_service(onboarding=SAMPLE_ONBOARDING)
        result = service.get_live_status("rhoai-v3-5")
        ob_map = {o["node_id"]: o for o in result["onboarding"]}
        checks = ob_map["comp-odh-notebook-v3-5"]["checks"]
        assert checks["branch"]["status"] == "FAIL"
        assert "fix" in checks["branch"]

    def test_onboarding_none_returns_empty_list(self):
        service = self._make_service(onboarding=None)
        result = service.get_live_status("rhoai-v3-5")
        assert result["onboarding"] == []

    def test_onboarding_empty_components_returns_empty(self):
        service = self._make_service(onboarding={"components": []})
        result = service.get_live_status("rhoai-v3-5")
        assert result["onboarding"] == []

    def test_healthy_detail_shows_last_build_date(self):
        health = [{"component_name": "comp-a", "health_score": 95,
                   "last_successful_build": "2026-07-10T08:30:00Z"}]
        service = self._make_service(health=health)
        result = service.get_live_status("rhoai-v3-5")
        nodes = {n["node_id"]: n for n in result["nodes"]}
        assert "2026-07-10" in nodes["comp-comp-a"]["detail"]

    def test_healthy_detail_fallback_without_timestamp(self):
        health = [{"component_name": "comp-b", "health_score": 90}]
        service = self._make_service(health=health)
        result = service.get_live_status("rhoai-v3-5")
        nodes = {n["node_id"]: n for n in result["nodes"]}
        assert "success" in nodes["comp-comp-b"]["detail"]

    def test_failing_detail_shows_consecutive_failures(self):
        health = [{"component_name": "comp-c", "health_score": 20,
                   "consecutive_failures": 5, "last_failed_build": "2026-07-09T14:00:00Z"}]
        service = self._make_service(health=health)
        result = service.get_live_status("rhoai-v3-5")
        nodes = {n["node_id"]: n for n in result["nodes"]}
        detail = nodes["comp-comp-c"]["detail"]
        assert "5 consecutive" in detail
        assert "2026-07-09" in detail

    def test_degraded_detail_shows_score_and_rate(self):
        health = [{"component_name": "comp-d", "health_score": 60,
                   "success_rate_last_7d": "85.71"}]
        service = self._make_service(health=health)
        result = service.get_live_status("rhoai-v3-5")
        nodes = {n["node_id"]: n for n in result["nodes"]}
        detail = nodes["comp-comp-d"]["detail"]
        assert "60/100" in detail
        assert "85.71%" in detail

    def test_cache_returns_same_data(self):
        from map.backend.live_status_service import LiveStatusService
        client = MagicMock()
        client.get_alerts.return_value = SAMPLE_ALERTS
        client.get_health.return_value = None
        client.get_readiness.return_value = None
        client.get_onboarding.return_value = None
        service = LiveStatusService(client=client, cache_ttl=300)

        first = service.get_live_status("rhoai-v3-5")
        second = service.get_live_status("rhoai-v3-5")

        assert first is second
        assert client.get_alerts.call_count == 1


# ─── Route ────────────────────────────────────────────────────────────────────


class TestLiveStatusRoute:
    @pytest.fixture
    def client(self):
        with patch("map.backend.graph.get_driver"), \
             patch("map.backend.main._auto_seed_from_ic"):
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


# ─── Phase 10f fixes ─────────────────────────────────────────────────────────


class TestActivityFeedStaleness:
    def _make_service(self, alerts=None):
        from map.backend.live_status_service import LiveStatusService
        client = MagicMock()
        client.get_alerts.return_value = alerts
        client.get_health.return_value = None
        client.get_readiness.return_value = None
        client.get_onboarding.return_value = None
        client.get_pr_activity.return_value = None
        return LiveStatusService(client=client, cache_ttl=0)

    def test_stale_events_filtered_out(self):
        stale_alerts = {
            "build_failures": [
                {
                    "component": "old-comp",
                    "status": "Failed",
                    "error_type": "build_error",
                    "last_seen": "2024-01-01T00:00:00Z",
                },
                {
                    "component": "fresh-comp",
                    "status": "Failed",
                    "error_type": "build_error",
                    "last_seen": "2099-01-01T00:00:00Z",
                },
            ],
            "conforma_violations": [],
        }
        service = self._make_service(alerts=stale_alerts)
        result = service.get_live_status("rhoai-v3-5")
        components = [e["component"] for e in result["activity"]]
        assert "fresh-comp" in components
        assert "old-comp" not in components

    def test_all_stale_returns_empty_feed(self):
        stale_alerts = {
            "build_failures": [
                {
                    "component": "ancient",
                    "status": "Failed",
                    "error_type": "timeout",
                    "last_seen": "2020-01-01T00:00:00Z",
                },
            ],
            "conforma_violations": [],
        }
        service = self._make_service(alerts=stale_alerts)
        result = service.get_live_status("rhoai-v3-5")
        assert len(result["activity"]) == 0

    def test_datetime_object_timestamps_handled(self):
        from datetime import datetime
        alerts = {
            "build_failures": [
                {
                    "component": "dt-comp",
                    "status": "Failed",
                    "error_type": "build_error",
                    "last_seen": datetime(2099, 6, 1, 12, 0, 0),
                },
            ],
            "conforma_violations": [],
        }
        service = self._make_service(alerts=alerts)
        result = service.get_live_status("rhoai-v3-5")
        components = [e["component"] for e in result["activity"]]
        assert "dt-comp" in components

    def test_timezone_aware_timestamps_handled(self):
        alerts = {
            "build_failures": [
                {
                    "component": "tz-comp",
                    "status": "Failed",
                    "error_type": "build_error",
                    "last_seen": "2099-01-01T00:00:00+00:00",
                },
            ],
            "conforma_violations": [],
        }
        service = self._make_service(alerts=alerts)
        result = service.get_live_status("rhoai-v3-5")
        components = [e["component"] for e in result["activity"]]
        assert "tz-comp" in components


class TestReadinessDetailWithSkips:
    def _make_service(self, readiness=None):
        from map.backend.live_status_service import LiveStatusService
        client = MagicMock()
        client.get_alerts.return_value = None
        client.get_health.return_value = None
        client.get_readiness.return_value = readiness
        client.get_onboarding.return_value = None
        client.get_pr_activity.return_value = None
        return LiveStatusService(client=client, cache_ttl=0)

    def test_readiness_skip_count_in_detail(self):
        readiness = {
            "verdict": "NOT_READY",
            "checks": [
                {"name": "build_failures", "status": "FAIL"},
                {"name": "conforma", "status": "FAIL"},
                {"name": "fbc_health", "status": "PASS"},
                {"name": "pcc_cache", "status": "SKIP"},
                {"name": "stale", "status": "SKIP"},
                {"name": "snapshot", "status": "SKIP"},
            ],
        }
        service = self._make_service(readiness=readiness)
        result = service.get_live_status("rhoai-v3-5")
        app_nodes = [n for n in result["nodes"] if n["node_type"] == "Application"]
        assert len(app_nodes) == 1
        detail = app_nodes[0]["detail"]
        assert "3/6 checks skipped" in detail
        assert "2 failing" in detail

    def test_readiness_no_skips_no_skip_text(self):
        readiness = {
            "verdict": "READY",
            "checks": [
                {"name": "build_failures", "status": "PASS"},
                {"name": "conforma", "status": "PASS"},
            ],
        }
        service = self._make_service(readiness=readiness)
        result = service.get_live_status("rhoai-v3-5")
        app_nodes = [n for n in result["nodes"] if n["node_type"] == "Application"]
        assert "skipped" not in app_nodes[0]["detail"]


class TestICClientReadinessTimeout:
    def test_readiness_uses_longer_timeout(self):
        from map.backend.ic_client import ICClient
        client = ICClient(base_url="http://test:8000")
        session = MagicMock()
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"verdict": "READY"}
        session.get.return_value = resp
        client._session = session

        client.get_readiness("rhoai-v3-5")
        call_kwargs = session.get.call_args
        assert call_kwargs.kwargs.get("timeout") == 35 or call_kwargs[1].get("timeout") == 35
