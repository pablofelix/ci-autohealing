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
        s = ClusterSeeder(driver, ic_url="http://test:8080/api/v1")
        s._session = MagicMock()
        return s

    def test_check_ic_health_ok(self, seeder):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"status": "ok"}
        resp.raise_for_status = MagicMock()
        seeder._session.get.return_value = resp
        assert seeder.check_ic_health() is True

    def test_check_ic_health_down(self, seeder):
        seeder._session.get.side_effect = Exception("connection refused")
        assert seeder.check_ic_health() is False

    def test_seed_applications(self, seeder, mock_driver):
        driver, mock_session = mock_driver
        resp = MagicMock(status_code=200)
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "applications": [{"name": "rhoai-v3-5"}, {"name": "rhoai-v3-5-ea-2"}]
        }
        seeder._session.get.return_value = resp
        mock_session.execute_write = MagicMock()

        result = seeder.seed_applications()
        assert len(result) == 2
        assert "app-rhoai-v3-5" in result
        assert "app-rhoai-v3-5-ea-2" in result

    def test_seed_applications_empty(self, seeder):
        seeder._session.get.side_effect = Exception("connection refused")
        result = seeder.seed_applications()
        assert result == []

    def test_seed_components(self, seeder, mock_driver):
        driver, mock_session = mock_driver
        resp = MagicMock(status_code=200)
        resp.raise_for_status = MagicMock()
        resp.json.return_value = [
            {
                "name": "odh-dashboard",
                "repo": "https://github.com/red-hat-data-services/odh-dashboard",
                "branch": "rhoai-3.5",
            },
        ]
        seeder._session.get.return_value = resp
        mock_session.execute_write = MagicMock()

        result = seeder.seed_components("rhoai-v3-5")
        assert len(result) == 1
        assert "comp-odh-dashboard" in result
        # 1 merge_node + 1 merge_relationship = 2 execute_write calls
        assert mock_session.execute_write.call_count == 2

    def test_seed_components_empty(self, seeder, mock_driver):
        driver, mock_session = mock_driver
        resp = MagicMock(status_code=200)
        resp.raise_for_status = MagicMock()
        resp.json.return_value = None
        seeder._session.get.return_value = resp

        result = seeder.seed_components("rhoai-v3-5")
        assert result == []

    def test_seed_components_string_list(self, seeder, mock_driver):
        """Test handling of component list as plain strings."""
        driver, mock_session = mock_driver
        resp = MagicMock(status_code=200)
        resp.raise_for_status = MagicMock()
        resp.json.return_value = ["odh-dashboard", "kserve"]
        seeder._session.get.return_value = resp
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

    def test_detect_drift_finds_stale(self, seeder, mock_driver):
        driver, mock_session = mock_driver
        resp = MagicMock(status_code=200)
        resp.raise_for_status = MagicMock()
        resp.json.return_value = [
            {"name": "comp-a"}, {"name": "comp-b"}
        ]
        seeder._session.get.return_value = resp
        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([
            {"id": "comp-comp-a"}, {"id": "comp-comp-b"}, {"id": "comp-comp-c"}
        ]))
        mock_session.run.return_value = mock_result

        result = seeder.detect_drift()
        assert result["drift_detected"] is True
        assert "comp-comp-c" in result["stale"]

    def test_detect_drift_finds_missing(self, seeder, mock_driver):
        driver, mock_session = mock_driver
        resp = MagicMock(status_code=200)
        resp.raise_for_status = MagicMock()
        resp.json.return_value = [
            {"name": "comp-a"}, {"name": "comp-b"}
        ]
        seeder._session.get.return_value = resp
        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([
            {"id": "comp-comp-a"}
        ]))
        mock_session.run.return_value = mock_result

        result = seeder.detect_drift()
        assert result["drift_detected"] is True
        assert "comp-comp-b" in result["missing"]

    def test_detect_drift_no_drift(self, seeder, mock_driver):
        driver, mock_session = mock_driver
        resp = MagicMock(status_code=200)
        resp.raise_for_status = MagicMock()
        resp.json.return_value = [{"name": "comp-a"}]
        seeder._session.get.return_value = resp
        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([{"id": "comp-comp-a"}]))
        mock_session.run.return_value = mock_result

        result = seeder.detect_drift()
        assert result["drift_detected"] is False
        assert result["in_sync"] == 1

    def test_detect_drift_api_down(self, seeder):
        seeder._session.get.side_effect = Exception("connection refused")
        result = seeder.detect_drift()
        assert "error" in result

    def test_seed_all_ic_down(self, seeder):
        seeder._session.get.side_effect = Exception("connection refused")
        result = seeder.seed_all()
        assert result["seeded"] is False
        assert "error" in result

    def test_seed_all_success(self, seeder, mock_driver):
        driver, mock_session = mock_driver
        mock_session.execute_write = MagicMock()

        def side_effect(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            if "/applications/rhoai-v3-5/health" in url:
                resp.json.return_value = [{"component_name": "odh-dashboard"}]
            elif "/health" in url:
                resp.json.return_value = {"status": "healthy"}
            elif "/applications" in url:
                resp.json.return_value = {"applications": [{"name": "rhoai-v3-5"}]}
            else:
                resp.json.return_value = None
            return resp

        seeder._session.get.side_effect = side_effect
        result = seeder.seed_all("rhoai-v3-5")
        assert result["seeded"] is True
        assert result["applications"] >= 1
        assert result["components"] >= 1
        assert "timestamp" in result
