"""Tests for Phase 9 — PR & Resolution Event Feed in live status service."""

from datetime import datetime
from unittest.mock import MagicMock

from map.backend.live_status_service import LiveStatusService


def _make_service(pr_data=None, alerts=None, health=None, readiness=None, onboarding=None):
    client = MagicMock()
    client.get_alerts.return_value = alerts
    client.get_health.return_value = health
    client.get_readiness.return_value = readiness
    client.get_onboarding.return_value = onboarding
    client.get_pr_activity.return_value = pr_data
    return LiveStatusService(client=client, cache_ttl=0)


class TestBuildPrEvents:

    def test_open_pr(self):
        pr_data = {"prs": [{
            "component_name": "comp-a",
            "pr_url": "https://github.com/org/repo/pull/42",
            "pr_number": 42,
            "pr_merged": None,
            "was_successful": None,
            "attempted_at": "2026-07-10T12:00:00Z",
        }]}
        service = _make_service(pr_data=pr_data)
        events = service._build_pr_events(pr_data)
        assert len(events) == 1
        assert events[0].severity == "pr_open"
        assert events[0].event_type == "fix_pr"
        assert "PR #42" in events[0].message

    def test_merged_and_verified_pr(self):
        pr_data = {"prs": [{
            "component_name": "comp-b",
            "pr_url": "https://github.com/org/repo/pull/99",
            "pr_number": 99,
            "pr_merged": True,
            "was_successful": True,
            "attempted_at": "2026-07-10T10:00:00Z",
        }]}
        service = _make_service(pr_data=pr_data)
        events = service._build_pr_events(pr_data)
        assert len(events) == 1
        assert events[0].severity == "pr_merged"
        assert "verified" in events[0].message

    def test_merged_but_failed_pr(self):
        pr_data = {"prs": [{
            "component_name": "comp-c",
            "pr_url": "https://github.com/org/repo/pull/7",
            "pr_number": 7,
            "pr_merged": True,
            "was_successful": False,
            "attempted_at": "2026-07-09T08:00:00Z",
        }]}
        service = _make_service(pr_data=pr_data)
        events = service._build_pr_events(pr_data)
        assert len(events) == 1
        assert events[0].severity == "pr_stale"
        assert "failed" in events[0].message

    def test_merged_no_verification_yet(self):
        pr_data = {"prs": [{
            "component_name": "comp-d",
            "pr_url": "https://github.com/org/repo/pull/50",
            "pr_number": 50,
            "pr_merged": True,
            "was_successful": None,
            "attempted_at": "2026-07-10T11:00:00Z",
        }]}
        service = _make_service(pr_data=pr_data)
        events = service._build_pr_events(pr_data)
        assert events[0].severity == "pr_merged"
        assert "merged" in events[0].message

    def test_empty_pr_data(self):
        service = _make_service()
        assert service._build_pr_events(None) == []
        assert service._build_pr_events({}) == []

    def test_pr_events_merged_into_activity(self):
        alerts = {
            "build_failures": [{
                "component": "failing-comp",
                "error_type": "Build failed",
                "last_seen": "2026-07-10T13:00:00Z",
            }],
            "conforma_violations": [],
        }
        pr_data = {"prs": [{
            "component_name": "fixed-comp",
            "pr_url": "https://github.com/org/repo/pull/1",
            "pr_number": 1,
            "pr_merged": None,
            "was_successful": None,
            "attempted_at": "2026-07-10T14:00:00Z",
        }]}
        service = _make_service(pr_data=pr_data, alerts=alerts)
        result = service._fetch_and_compute("test-app")
        event_types = {e["event_type"] for e in result["activity"]}
        assert "build_failure" in event_types
        assert "fix_pr" in event_types

    def test_datetime_timestamp_converted(self):
        pr_data = {"prs": [{
            "component_name": "comp-e",
            "pr_url": "https://github.com/org/repo/pull/5",
            "pr_number": 5,
            "pr_merged": None,
            "was_successful": None,
            "attempted_at": datetime(2026, 7, 10, 12, 0, 0),
        }]}
        service = _make_service(pr_data=pr_data)
        events = service._build_pr_events(pr_data)
        assert "2026-07-10" in events[0].timestamp
