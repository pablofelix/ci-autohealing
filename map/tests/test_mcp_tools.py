"""Tests for System Map MCP tools."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest


def run_async(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


SAMPLE_NODES = [
    {"id": "comp-odh-dashboard", "type": "Component", "props": {"name": "odh-dashboard", "description": "UI"}},
    {"id": "app-rhoai-v3-5", "type": "Application", "props": {"name": "rhoai-v3-5", "description": ""}},
]

SAMPLE_EDGES = [
    {"source": "app-rhoai-v3-5", "target": "comp-odh-dashboard", "label": "CONTAINS", "props": {}},
]

SAMPLE_GAPS = [
    {"node_id": "comp-odh-dashboard", "node_name": "odh-dashboard", "type": "missing_pipeline",
     "severity": "warning", "message": "Component has no build pipeline linked"},
]


class TestGetMapGraph:
    @patch("map.mcp_server.tools._graph")
    def test_returns_nodes_and_edges(self, mock_graph):
        g = MagicMock()
        g.get_all_nodes.return_value = SAMPLE_NODES
        g.get_all_edges.return_value = SAMPLE_EDGES
        g.get_gaps.return_value = []
        mock_graph.return_value = g

        from map.mcp_server.tools import get_map_graph
        result = run_async(get_map_graph())

        assert result["total_nodes"] == 2
        assert result["total_edges"] == 1
        assert result["nodes"][0]["id"] == "comp-odh-dashboard"
        assert result["edges"][0]["label"] == "CONTAINS"

    @patch("map.mcp_server.tools._graph")
    def test_includes_gaps_in_nodes(self, mock_graph):
        g = MagicMock()
        g.get_all_nodes.return_value = SAMPLE_NODES
        g.get_all_edges.return_value = []
        g.get_gaps.return_value = SAMPLE_GAPS
        mock_graph.return_value = g

        from map.mcp_server.tools import get_map_graph
        result = run_async(get_map_graph())

        dashboard = next(n for n in result["nodes"] if n["id"] == "comp-odh-dashboard")
        assert len(dashboard["gaps"]) == 1
        assert result["total_gaps"] == 1

    @patch("map.mcp_server.tools._graph")
    def test_strips_internal_properties(self, mock_graph):
        g = MagicMock()
        nodes = [{"id": "x", "type": "Component", "props": {
            "name": "x", "_source": "seed", "_seeded_at": "2026-01-01", "repo": "https://..."}}]
        g.get_all_nodes.return_value = nodes
        g.get_all_edges.return_value = []
        g.get_gaps.return_value = []
        mock_graph.return_value = g

        from map.mcp_server.tools import get_map_graph
        result = run_async(get_map_graph())

        props = result["nodes"][0]["properties"]
        assert "_source" not in props
        assert "_seeded_at" not in props
        assert props["repo"] == "https://..."


class TestSearchMapNodes:
    @patch("map.mcp_server.tools._graph")
    def test_search_returns_results(self, mock_graph):
        g = MagicMock()
        g.search_nodes.return_value = [
            {"id": "comp-odh-dashboard", "type": "Component", "name": "odh-dashboard", "description": None}
        ]
        mock_graph.return_value = g

        from map.mcp_server.tools import search_map_nodes
        result = run_async(search_map_nodes("dashboard"))

        assert result["count"] == 1
        assert result["query"] == "dashboard"
        g.search_nodes.assert_called_once_with("dashboard", node_type=None)

    @patch("map.mcp_server.tools._graph")
    def test_search_with_type_filter(self, mock_graph):
        g = MagicMock()
        g.search_nodes.return_value = []
        mock_graph.return_value = g

        from map.mcp_server.tools import search_map_nodes
        result = run_async(search_map_nodes("test", type="Pipeline"))

        assert result["count"] == 0
        g.search_nodes.assert_called_once_with("test", node_type="Pipeline")


class TestGetMapNode:
    @patch("map.mcp_server.tools._graph")
    def test_returns_detail_with_gaps(self, mock_graph):
        g = MagicMock()
        g.get_node_detail.return_value = {
            "id": "comp-odh-dashboard", "type": "Component",
            "props": {"name": "odh-dashboard", "repo": "https://..."},
            "neighbors": [{"id": "app-rhoai-v3-5", "type": "Application", "relationship": "CONTAINS"}],
        }
        g.get_gaps.return_value = SAMPLE_GAPS
        mock_graph.return_value = g

        from map.mcp_server.tools import get_map_node
        result = run_async(get_map_node("comp-odh-dashboard"))

        assert result["id"] == "comp-odh-dashboard"
        assert len(result["neighbors"]) == 1
        assert len(result["gaps"]) == 1

    @patch("map.mcp_server.tools._graph")
    def test_not_found_returns_error(self, mock_graph):
        g = MagicMock()
        g.get_node_detail.return_value = None
        mock_graph.return_value = g

        from map.mcp_server.tools import get_map_node
        result = run_async(get_map_node("nonexistent"))

        assert "error" in result

    @patch("map.mcp_server.tools._graph")
    def test_strips_internal_props(self, mock_graph):
        g = MagicMock()
        g.get_node_detail.return_value = {
            "id": "x", "type": "Component",
            "props": {"name": "x", "_source": "seed", "_seeded_at": "ts", "url": "https://..."},
            "neighbors": [],
        }
        g.get_gaps.return_value = []
        mock_graph.return_value = g

        from map.mcp_server.tools import get_map_node
        result = run_async(get_map_node("x"))

        assert "_source" not in result["props"]
        assert result["props"]["url"] == "https://..."


class TestFindMapPath:
    @patch("map.mcp_server.tools._graph")
    def test_returns_path(self, mock_graph):
        g = MagicMock()
        g.find_path.return_value = {
            "nodes": [
                {"id": "app-rhoai-v3-5", "type": "Application", "name": "rhoai-v3-5"},
                {"id": "comp-odh-dashboard", "type": "Component", "name": "odh-dashboard"},
            ],
            "edges": [{"source": "app-rhoai-v3-5", "target": "comp-odh-dashboard", "label": "CONTAINS"}],
        }
        mock_graph.return_value = g

        from map.mcp_server.tools import find_map_path
        result = run_async(find_map_path("app-rhoai-v3-5", "comp-odh-dashboard"))

        assert len(result["nodes"]) == 2
        assert len(result["edges"]) == 1

    @patch("map.mcp_server.tools._graph")
    def test_no_path_returns_error(self, mock_graph):
        g = MagicMock()
        g.find_path.return_value = None
        mock_graph.return_value = g

        from map.mcp_server.tools import find_map_path
        result = run_async(find_map_path("a", "b"))

        assert "error" in result


class TestGetMapGaps:
    @patch("map.mcp_server.tools._graph")
    def test_returns_gaps_with_counts(self, mock_graph):
        g = MagicMock()
        g.get_gaps.return_value = SAMPLE_GAPS
        mock_graph.return_value = g

        from map.mcp_server.tools import get_map_gaps
        result = run_async(get_map_gaps())

        assert result["count"] == 1
        assert result["by_type"]["missing_pipeline"] == 1


class TestGetMapStats:
    @patch("map.mcp_server.tools._graph")
    def test_returns_stats(self, mock_graph):
        g = MagicMock()
        g.get_stats.return_value = {
            "nodes": [{"type": "Component", "count": 50}],
            "edges": [{"type": "CONTAINS", "count": 50}],
        }
        mock_graph.return_value = g

        from map.mcp_server.tools import get_map_stats
        result = run_async(get_map_stats())

        assert result["nodes"][0]["type"] == "Component"
        assert result["edges"][0]["count"] == 50


class TestGetMapLiveStatus:
    @patch("map.mcp_server.tools.LiveStatusService", create=True)
    def test_returns_live_status(self, _mock):
        from map.mcp_server.tools import get_map_live_status

        mock_data = {
            "application": "rhoai-v3-5",
            "nodes": [{"node_id": "comp-x", "status": "healthy"}],
            "activity": [],
            "ic_available": True,
            "last_updated": "2026-07-10T00:00:00Z",
        }

        with patch("map.backend.live_status_service.LiveStatusService") as mock_cls:
            mock_cls.return_value.get_live_status.return_value = mock_data
            result = run_async(get_map_live_status("rhoai-v3-5"))

        assert result["ic_available"] is True
        assert len(result["nodes"]) == 1

    def test_graceful_degradation_on_error(self):
        from map.mcp_server.tools import get_map_live_status

        with patch("map.backend.live_status_service.LiveStatusService", side_effect=ConnectionError("down")):
            result = run_async(get_map_live_status())

        assert result["ic_available"] is False
        assert result["nodes"] == []
