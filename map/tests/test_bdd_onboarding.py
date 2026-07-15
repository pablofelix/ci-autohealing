"""BDD tests for the onboarding overlay on the system map.

Tests the full data contract from HTTP request through LiveStatusService
to JSON response, with the IC client mocked at the service boundary.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pytest_bdd import given, parsers, scenarios, then, when

scenarios("features/onboarding_overlay.feature")


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


# ==============================================================================
# Fixtures
# ==============================================================================


@pytest.fixture
def mock_ic_client():
    """Pre-configured IC client mock — scenarios customize return values."""
    client = MagicMock()
    client.get_alerts.return_value = None
    client.get_health.return_value = None
    client.get_readiness.return_value = None
    client.get_onboarding.return_value = None
    client.get_pr_activity.return_value = None
    return client


@pytest.fixture
def app_client(mock_ic_client):
    """FastAPI TestClient with LiveStatusService patched to use mock IC client."""
    from map.backend.live_status_service import LiveStatusService

    real_init = LiveStatusService.__init__

    def patched_init(self, client=None, cache_ttl=55):
        real_init(self, client=mock_ic_client, cache_ttl=0)

    with patch("map.backend.graph.get_driver"), \
         patch.object(LiveStatusService, "__init__", patched_init):
        from map.backend.main import app
        yield TestClient(app)


# ==============================================================================
# Background
# ==============================================================================


@given("the live status endpoint is available")
def live_status_available():
    pass


# ==============================================================================
# Given — IC data scenarios
# ==============================================================================


@given(parsers.parse('IC reports onboarding data for "{application}"'))
def ic_has_onboarding(mock_ic_client, application):
    mock_ic_client.get_onboarding.return_value = SAMPLE_ONBOARDING


@given("IC is unavailable")
def ic_unavailable(mock_ic_client):
    pass


@given(parsers.parse('IC reports empty onboarding for "{application}"'))
def ic_empty_onboarding(mock_ic_client, application):
    mock_ic_client.get_onboarding.return_value = {"components": []}


@given("IC reports onboarding with a nameless component")
def ic_nameless_component(mock_ic_client):
    mock_ic_client.get_onboarding.return_value = {
        "components": [
            {"component": "", "overall": "incomplete", "score": 0, "checks": {}, "failing": [], "warnings": []},
            {"component": "valid-comp", "overall": "complete", "score": 100, "checks": {}, "failing": [], "warnings": []},
        ],
    }


# ==============================================================================
# When
# ==============================================================================


@when(
    parsers.parse('I request the live status for "{application}"'),
    target_fixture="live_status_response",
)
def request_live_status(application, app_client):
    resp = app_client.get(f"/api/map/live-status?application={application}")
    assert resp.status_code == 200
    return resp.json()


# ==============================================================================
# Then — response structure
# ==============================================================================


@then(parsers.parse('the response contains an "{field}" array'))
def response_has_array(live_status_response, field):
    assert field in live_status_response
    assert isinstance(live_status_response[field], list)


@then(parsers.parse("the onboarding array has {count:d} entries"))
def onboarding_count(live_status_response, count):
    assert len(live_status_response["onboarding"]) == count


@then("ic_available is false")
def ic_not_available(live_status_response):
    assert live_status_response["ic_available"] is False


# ==============================================================================
# Then — badge colors and scores
# ==============================================================================


def _find_onboarding(response, component_name):
    node_id = f"comp-{component_name}"
    for ob in response["onboarding"]:
        if ob["node_id"] == node_id:
            return ob
    raise AssertionError(f"No onboarding entry for {node_id}")


@then(parsers.parse('component "{name}" has onboarding score {score:d}'))
def check_score(live_status_response, name, score):
    ob = _find_onboarding(live_status_response, name)
    assert ob["score"] == score


@then(parsers.parse('component "{name}" has onboarding overall "{overall}"'))
def check_overall(live_status_response, name, overall):
    ob = _find_onboarding(live_status_response, name)
    assert ob["overall"] == overall


@then(parsers.parse('component "{name}" has badge color "{color}"'))
def check_badge_color(live_status_response, name, color):
    ob = _find_onboarding(live_status_response, name)
    assert ob["badge_color"] == color


# ==============================================================================
# Then — onboarding checks
# ==============================================================================


@then(parsers.parse('component "{name}" check "{check}" has status "{status}"'))
def check_status(live_status_response, name, check, status):
    ob = _find_onboarding(live_status_response, name)
    assert check in ob["checks"], f"Check '{check}' not found in {list(ob['checks'].keys())}"
    assert ob["checks"][check]["status"] == status


@then(parsers.parse('component "{name}" check "{check}" has a fix suggestion'))
def check_has_fix(live_status_response, name, check):
    ob = _find_onboarding(live_status_response, name)
    assert "fix" in ob["checks"][check], f"No fix suggestion in check '{check}'"


@then(parsers.parse('component "{name}" has failing checks "{checks_csv}"'))
def check_failing_list(live_status_response, name, checks_csv):
    ob = _find_onboarding(live_status_response, name)
    expected = checks_csv.split(",")
    assert ob["failing"] == expected


@then(parsers.parse('component "{name}" has no failing checks'))
def check_no_failing(live_status_response, name):
    ob = _find_onboarding(live_status_response, name)
    assert ob["failing"] == []


# ==============================================================================
# Then — Jira
# ==============================================================================


@then(parsers.parse('component "{name}" has jira key "{key}"'))
def check_jira_key(live_status_response, name, key):
    ob = _find_onboarding(live_status_response, name)
    assert ob["jira_key"] == key


@then(parsers.parse('component "{name}" has no jira key'))
def check_no_jira_key(live_status_response, name):
    ob = _find_onboarding(live_status_response, name)
    assert ob["jira_key"] == ""


# ==============================================================================
# Then — node ID format
# ==============================================================================


@then('all onboarding node_ids start with "comp-"')
def all_node_ids_prefixed(live_status_response):
    for ob in live_status_response["onboarding"]:
        assert ob["node_id"].startswith("comp-"), f"node_id '{ob['node_id']}' missing comp- prefix"
