"""Tests for ClusterSeeder (map.cluster_seeder)."""

from unittest.mock import MagicMock, patch

import pytest


class TestClusterSeeder:
    @pytest.fixture
    def mock_driver(self):
        driver = MagicMock()
        mock_session = MagicMock()
        driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        return driver, mock_session

    @pytest.fixture
    def seeder(self, mock_driver):
        from map.cluster_seeder import ClusterSeeder
        driver, _ = mock_driver
        return ClusterSeeder(driver, ic_url="http://test:8080/api/v1")

    @patch("map.cluster_seeder.requests.get")
    def test_check_ic_health_ok(self, mock_get, seeder):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"status": "ok"}
        mock_get.return_value.raise_for_status = MagicMock()
        assert seeder.check_ic_health() is True

    @patch("map.cluster_seeder.requests.get")
    def test_check_ic_health_down(self, mock_get, seeder):
        mock_get.side_effect = Exception("connection refused")
        assert seeder.check_ic_health() is False

    @patch("map.cluster_seeder.requests.get")
    def test_seed_applications(self, mock_get, seeder, mock_driver):
        driver, mock_session = mock_driver
        mock_get.return_value.status_code = 200
        mock_get.return_value.raise_for_status = MagicMock()
        mock_get.return_value.json.return_value = {
            "applications": [{"name": "rhoai-v3-5"}, {"name": "rhoai-v3-5-ea-2"}]
        }
        mock_session.execute_write = MagicMock()

        result = seeder.seed_applications()
        assert len(result) == 2
        assert "app-rhoai-v3-5" in result
        assert "app-rhoai-v3-5-ea-2" in result

    @patch("map.cluster_seeder.requests.get")
    def test_seed_applications_empty(self, mock_get, seeder):
        mock_get.side_effect = Exception("connection refused")
        result = seeder.seed_applications()
        assert result == []

    @patch("map.cluster_seeder.requests.get")
    def test_seed_components(self, mock_get, seeder, mock_driver):
        driver, mock_session = mock_driver
        mock_get.return_value.status_code = 200
        mock_get.return_value.raise_for_status = MagicMock()
        mock_get.return_value.json.return_value = [
            {
                "name": "odh-dashboard",
                "repo": "https://github.com/red-hat-data-services/odh-dashboard",
                "branch": "rhoai-3.5",
            },
        ]
        mock_session.execute_write = MagicMock()

        result = seeder.seed_components("rhoai-v3-5")
        assert len(result) == 1
        assert "comp-odh-dashboard" in result
        # 1 merge_node + 1 merge_relationship = 2 execute_write calls
        assert mock_session.execute_write.call_count == 2

    @patch("map.cluster_seeder.requests.get")
    def test_seed_components_empty(self, mock_get, seeder, mock_driver):
        driver, mock_session = mock_driver
        mock_get.return_value.status_code = 200
        mock_get.return_value.raise_for_status = MagicMock()
        # Both API paths return None
        mock_get.return_value.json.return_value = None

        result = seeder.seed_components("rhoai-v3-5")
        assert result == []

    @patch("map.cluster_seeder.requests.get")
    def test_seed_components_string_list(self, mock_get, seeder, mock_driver):
        """Test handling of component list as plain strings."""
        driver, mock_session = mock_driver
        mock_get.return_value.status_code = 200
        mock_get.return_value.raise_for_status = MagicMock()
        mock_get.return_value.json.return_value = ["odh-dashboard", "kserve"]
        mock_session.execute_write = MagicMock()

        result = seeder.seed_components("rhoai-v3-5")
        assert len(result) == 2
        assert "comp-odh-dashboard" in result
        assert "comp-kserve" in result

    def test_seed_nudge_chains(self, seeder, mock_driver):
        driver, mock_session = mock_driver
        mock_session.execute_write = MagicMock()

        comp_ids = ["comp-kserve", "comp-kserve-bundle", "comp-rhoai-fbc-fragment"]
        count = seeder.seed_nudge_chains(comp_ids)
        # kserve -> kserve-bundle (1), kserve-bundle -> rhoai-fbc-fragment (1)
        assert count >= 2

    def test_seed_nudge_chains_no_bundles(self, seeder, mock_driver):
        driver, mock_session = mock_driver
        mock_session.execute_write = MagicMock()

        comp_ids = ["comp-odh-dashboard", "comp-kserve"]
        count = seeder.seed_nudge_chains(comp_ids)
        assert count == 0

    @patch("map.cluster_seeder.requests.get")
    def test_detect_drift_finds_stale(self, mock_get, seeder, mock_driver):
        driver, mock_session = mock_driver
        # IC returns 2 components
        mock_get.return_value.status_code = 200
        mock_get.return_value.raise_for_status = MagicMock()
        mock_get.return_value.json.return_value = [
            {"name": "comp-a"}, {"name": "comp-b"}
        ]
        # Graph has 3 components (comp-comp-c is stale)
        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([
            {"id": "comp-comp-a"}, {"id": "comp-comp-b"}, {"id": "comp-comp-c"}
        ]))
        mock_session.run.return_value = mock_result

        result = seeder.detect_drift()
        assert result["drift_detected"] is True
        assert "comp-comp-c" in result["stale"]

    @patch("map.cluster_seeder.requests.get")
    def test_detect_drift_finds_missing(self, mock_get, seeder, mock_driver):
        driver, mock_session = mock_driver
        # IC returns 2 components
        mock_get.return_value.status_code = 200
        mock_get.return_value.raise_for_status = MagicMock()
        mock_get.return_value.json.return_value = [
            {"name": "comp-a"}, {"name": "comp-b"}
        ]
        # Graph has only 1 component (comp-comp-b is missing from graph)
        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([
            {"id": "comp-comp-a"}
        ]))
        mock_session.run.return_value = mock_result

        result = seeder.detect_drift()
        assert result["drift_detected"] is True
        assert "comp-comp-b" in result["missing"]

    @patch("map.cluster_seeder.requests.get")
    def test_detect_drift_no_drift(self, mock_get, seeder, mock_driver):
        driver, mock_session = mock_driver
        mock_get.return_value.status_code = 200
        mock_get.return_value.raise_for_status = MagicMock()
        mock_get.return_value.json.return_value = [{"name": "comp-a"}]
        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([{"id": "comp-comp-a"}]))
        mock_session.run.return_value = mock_result

        result = seeder.detect_drift()
        assert result["drift_detected"] is False
        assert result["in_sync"] == 1

    @patch("map.cluster_seeder.requests.get")
    def test_detect_drift_api_down(self, mock_get, seeder):
        mock_get.side_effect = Exception("connection refused")
        result = seeder.detect_drift()
        assert "error" in result

    @patch("map.cluster_seeder.requests.get")
    def test_seed_all_ic_down(self, mock_get, seeder):
        mock_get.side_effect = Exception("connection refused")
        result = seeder.seed_all()
        assert result["seeded"] is False
        assert "error" in result

    @patch("map.cluster_seeder.requests.get")
    def test_seed_all_success(self, mock_get, seeder, mock_driver):
        driver, mock_session = mock_driver
        mock_session.execute_write = MagicMock()

        def side_effect(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            # Check most specific paths first to avoid substring false matches
            if "/applications/rhoai-v3-5/components" in url:
                resp.json.return_value = [{"name": "odh-dashboard"}]
            elif "/health" in url:
                resp.json.return_value = {"status": "ok"}
            elif "/applications" in url:
                resp.json.return_value = {"applications": [{"name": "rhoai-v3-5"}]}
            else:
                resp.json.return_value = None
            return resp

        mock_get.side_effect = side_effect
        result = seeder.seed_all("rhoai-v3-5")
        assert result["seeded"] is True
        assert result["applications"] >= 1
        assert result["components"] >= 1
        assert "timestamp" in result
