"""Tests for seed.py — no Neo4j required (mocked driver)."""

import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_driver():
    driver = MagicMock()
    session = MagicMock()
    driver.session.return_value.__enter__ = MagicMock(return_value=session)
    driver.session.return_value.__exit__ = MagicMock(return_value=False)
    return driver, session


class TestSeedComponentsFromIC:
    def test_graceful_fallback_when_ic_unavailable(self, mock_driver):
        from map.seed import seed_components_from_ic

        driver, session = mock_driver

        with patch("urllib.request.urlopen", side_effect=ConnectionError("refused")):
            seed_components_from_ic(driver)

        # Should not crash, should not seed anything
        session.execute_write.assert_not_called()

    def test_fetches_components_from_api(self, mock_driver):
        from map.seed import seed_components_from_ic

        driver, session = mock_driver

        apps_response = MagicMock()
        apps_response.read.return_value = json.dumps([{"name": "rhoai-v3-5"}]).encode()
        apps_response.__enter__ = MagicMock(return_value=apps_response)
        apps_response.__exit__ = MagicMock(return_value=False)

        working_response = MagicMock()
        working_response.read.return_value = json.dumps({
            "components": [
                {"component_name": "odh-dashboard-v3-5", "source_repo": "https://github.com/opendatahub-io/odh-dashboard"},
                {"component_name": "odh-notebook-controller-v3-5", "source_repo": "https://github.com/opendatahub-io/kubeflow"},
            ]
        }).encode()
        working_response.__enter__ = MagicMock(return_value=working_response)
        working_response.__exit__ = MagicMock(return_value=False)

        def urlopen_side_effect(req, **kwargs):
            if "applications" in req.full_url and "working" not in req.full_url:
                return apps_response
            return working_response

        with patch("urllib.request.urlopen", side_effect=urlopen_side_effect):
            seed_components_from_ic(driver)

        # Should have seeded: 1 Application + 2 Components + 2 CONTAINS + 2 BUILDS_WITH = 7 writes
        assert session.execute_write.call_count == 7


class TestNudgeChains:
    def test_infers_operator_to_bundle_nudge(self, mock_driver):
        from map.seed import _seed_nudge_chains

        driver, session = mock_driver
        components = [
            {"id": "comp-odh-notebook-controller-v3-5", "name": "odh-notebook-controller-v3-5"},
            {"id": "comp-odh-notebook-controller-v3-5-bundle", "name": "odh-notebook-controller-v3-5-bundle"},
        ]

        _seed_nudge_chains(driver, components)
        assert session.execute_write.call_count >= 1

    def test_no_nudge_when_no_bundle(self, mock_driver):
        from map.seed import _seed_nudge_chains

        driver, session = mock_driver
        components = [
            {"id": "comp-odh-dashboard-v3-5", "name": "odh-dashboard-v3-5"},
        ]

        _seed_nudge_chains(driver, components)
        assert session.execute_write.call_count == 0
