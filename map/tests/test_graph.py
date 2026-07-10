"""Tests for graph.py functions (mocked Neo4j)."""

from unittest.mock import MagicMock, patch

import pytest


class TestGetImpact:
    @patch("map.backend.graph.session")
    def test_impact_downstream_returns_grouped_results(self, mock_session):
        mock_sess = MagicMock()
        # First call: check node exists
        mock_check = MagicMock()
        mock_check.single.return_value = {"id": "comp-kserve"}
        # Second call: impact query
        mock_result = MagicMock()
        mock_result.__iter__ = lambda self: iter([
            {"id": "comp-kserve-bundle", "type": "Component", "name": "kserve-bundle", "depth": 1, "via_relationships": ["NUDGES"]},
            {"id": "comp-fbc", "type": "Component", "name": "fbc-fragment", "depth": 2, "via_relationships": ["NUDGES", "NUDGES"]},
        ])
        mock_sess.run.side_effect = [mock_check, mock_result]
        mock_session.return_value.__enter__ = lambda self: mock_sess
        mock_session.return_value.__exit__ = lambda self, *a: None

        from map.backend.graph import get_impact
        result = get_impact("comp-kserve", max_depth=5, direction="downstream")

        assert result is not None
        assert result["total_affected"] == 2
        assert result["source"] == "comp-kserve"
        assert 1 in result["by_depth"]
        assert 2 in result["by_depth"]

    @patch("map.backend.graph.session")
    def test_impact_node_not_found(self, mock_session):
        mock_sess = MagicMock()
        mock_check = MagicMock()
        mock_check.single.return_value = None
        mock_sess.run.return_value = mock_check
        mock_session.return_value.__enter__ = lambda self: mock_sess
        mock_session.return_value.__exit__ = lambda self, *a: None

        from map.backend.graph import get_impact
        result = get_impact("nonexistent")
        assert result is None

    @patch("map.backend.graph.session")
    def test_impact_clamps_depth(self, mock_session):
        mock_sess = MagicMock()
        mock_check = MagicMock()
        mock_check.single.return_value = {"id": "n1"}
        mock_result = MagicMock()
        mock_result.__iter__ = lambda self: iter([])
        mock_sess.run.side_effect = [mock_check, mock_result]
        mock_session.return_value.__enter__ = lambda self: mock_sess
        mock_session.return_value.__exit__ = lambda self, *a: None

        from map.backend.graph import get_impact
        result = get_impact("n1", max_depth=100)
        assert result["max_depth"] == 10  # clamped

    @patch("map.backend.graph.session")
    def test_impact_upstream_direction(self, mock_session):
        mock_sess = MagicMock()
        mock_check = MagicMock()
        mock_check.single.return_value = {"id": "comp-fbc"}
        mock_result = MagicMock()
        mock_result.__iter__ = lambda self: iter([])
        mock_sess.run.side_effect = [mock_check, mock_result]
        mock_session.return_value.__enter__ = lambda self: mock_sess
        mock_session.return_value.__exit__ = lambda self, *a: None

        from map.backend.graph import get_impact
        result = get_impact("comp-fbc", direction="upstream")
        assert result["direction"] == "upstream"
        # Verify the upstream Cypher was used (arrows reversed)
        call_args = mock_sess.run.call_args_list[1]
        assert "]->(start)" in call_args[0][0]

    @patch("map.backend.graph.session")
    def test_impact_by_type_groups_correctly(self, mock_session):
        mock_sess = MagicMock()
        mock_check = MagicMock()
        mock_check.single.return_value = {"id": "policy-1"}
        mock_result = MagicMock()
        mock_result.__iter__ = lambda self: iter([
            {"id": "app-1", "type": "Application", "name": "app-1", "depth": 1, "via_relationships": ["VALIDATES"]},
            {"id": "app-2", "type": "Application", "name": "app-2", "depth": 1, "via_relationships": ["VALIDATES"]},
            {"id": "comp-1", "type": "Component", "name": "comp-1", "depth": 2, "via_relationships": ["VALIDATES", "CONTAINS"]},
        ])
        mock_sess.run.side_effect = [mock_check, mock_result]
        mock_session.return_value.__enter__ = lambda self: mock_sess
        mock_session.return_value.__exit__ = lambda self, *a: None

        from map.backend.graph import get_impact
        result = get_impact("policy-1")

        assert result["total_affected"] == 3
        assert result["by_type"]["Application"] == 2
        assert result["by_type"]["Component"] == 1
        assert len(result["by_depth"][1]) == 2
        assert len(result["by_depth"][2]) == 1

    @patch("map.backend.graph.session")
    def test_impact_clamps_depth_minimum(self, mock_session):
        mock_sess = MagicMock()
        mock_check = MagicMock()
        mock_check.single.return_value = {"id": "n1"}
        mock_result = MagicMock()
        mock_result.__iter__ = lambda self: iter([])
        mock_sess.run.side_effect = [mock_check, mock_result]
        mock_session.return_value.__enter__ = lambda self: mock_sess
        mock_session.return_value.__exit__ = lambda self, *a: None

        from map.backend.graph import get_impact
        result = get_impact("n1", max_depth=0)
        assert result["max_depth"] == 1  # clamped to minimum
