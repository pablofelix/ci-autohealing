"""Tests for map API routes — no Neo4j required (mocked graph module)."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


SAMPLE_NODES = [
    {"id": "app-rhoai-v3-5", "type": "Application", "props": {"id": "app-rhoai-v3-5", "name": "rhoai-v3-5"}},
    {"id": "comp-odh-dashboard", "type": "Component", "props": {"id": "comp-odh-dashboard", "name": "odh-dashboard", "source_repo": "https://github.com/opendatahub-io/odh-dashboard"}},
    {"id": "pipeline-container-build", "type": "Pipeline", "props": {"id": "pipeline-container-build", "name": "container-build"}},
]

SAMPLE_EDGES = [
    {"source": "app-rhoai-v3-5", "target": "comp-odh-dashboard", "label": "CONTAINS", "props": {}},
    {"source": "comp-odh-dashboard", "target": "pipeline-container-build", "label": "BUILDS_WITH", "props": {}},
]

SAMPLE_GAPS = [
    {
        "node_id": "comp-odh-dashboard",
        "node_name": "odh-dashboard",
        "type": "missing_pipeline",
        "severity": "warning",
        "message": "Component has no build pipeline linked",
    },
]

SAMPLE_STATS = {
    "nodes": [{"type": "Component", "count": 50}, {"type": "Application", "count": 2}],
    "edges": [{"type": "CONTAINS", "count": 50}, {"type": "BUILDS_WITH", "count": 48}],
}


@pytest.fixture
def client():
    with patch("map.backend.graph.get_driver"):
        from map.backend.main import app
        yield TestClient(app)


@pytest.fixture(autouse=True)
def mock_graph():
    with (
        patch("map.backend.graph.get_all_nodes", return_value=SAMPLE_NODES),
        patch("map.backend.graph.get_all_edges", return_value=SAMPLE_EDGES),
        patch("map.backend.graph.get_gaps", return_value=SAMPLE_GAPS),
        patch("map.backend.graph.get_stats", return_value=SAMPLE_STATS),
        patch("map.backend.graph.get_node_detail", return_value={
            "id": "comp-odh-dashboard", "type": "Component",
            "props": {"name": "odh-dashboard"},
            "neighbors": [{"id": "app-rhoai-v3-5", "type": "Application", "name": "rhoai-v3-5", "relationship": "CONTAINS", "direction": "incoming"}],
        }),
        patch("map.backend.graph.search_nodes", return_value=[
            {"id": "comp-odh-dashboard", "type": "Component", "name": "odh-dashboard", "description": None},
        ]),
        patch("map.backend.graph.find_path", return_value={
            "nodes": [{"id": "app-rhoai-v3-5", "type": "Application", "name": "rhoai-v3-5"}, {"id": "comp-odh-dashboard", "type": "Component", "name": "odh-dashboard"}],
            "edges": [{"source": "app-rhoai-v3-5", "target": "comp-odh-dashboard", "label": "CONTAINS"}],
        }),
    ):
        yield


class TestGraphEndpoint:
    def test_full_graph_returns_react_flow_format(self, client):
        resp = client.get("/api/map/graph")
        assert resp.status_code == 200
        data = resp.json()
        assert "nodes" in data
        assert "edges" in data
        assert len(data["nodes"]) == 3
        assert len(data["edges"]) == 2

        node = data["nodes"][0]
        assert "id" in node
        assert "data" in node
        assert "position" in node
        assert "label" in node["data"]
        assert "nodeType" in node["data"]

    def test_graph_nodes_include_gap_overlay(self, client):
        resp = client.get("/api/map/graph")
        data = resp.json()
        dashboard = next(n for n in data["nodes"] if n["id"] == "comp-odh-dashboard")
        assert dashboard["data"]["hasGaps"] is True
        assert len(dashboard["data"]["gaps"]) == 1

    def test_graph_edges_have_animation(self, client):
        resp = client.get("/api/map/graph")
        data = resp.json()
        contains_edge = next(e for e in data["edges"] if e["label"] == "CONTAINS")
        assert contains_edge["animated"] is False


class TestNodesEndpoint:
    def test_list_all_nodes(self, client):
        resp = client.get("/api/map/nodes")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 3

    def test_filter_by_type(self, client):
        resp = client.get("/api/map/nodes?type=Component")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["nodes"][0]["type"] == "Component"


class TestEdgesEndpoint:
    def test_list_all_edges(self, client):
        resp = client.get("/api/map/edges")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2


class TestNodeDetailEndpoint:
    def test_existing_node(self, client):
        resp = client.get("/api/map/node/comp-odh-dashboard")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "comp-odh-dashboard"
        assert len(data["neighbors"]) == 1
        assert "gaps" in data

    def test_nonexistent_node(self, client):
        with patch("map.backend.graph.get_node_detail", return_value=None):
            resp = client.get("/api/map/node/nonexistent")
            assert resp.status_code == 404


class TestSearchEndpoint:
    def test_search_returns_results(self, client):
        resp = client.get("/api/map/search?q=dashboard")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["query"] == "dashboard"

    def test_search_requires_query(self, client):
        resp = client.get("/api/map/search")
        assert resp.status_code == 422


class TestPathEndpoint:
    def test_path_found(self, client):
        resp = client.get("/api/map/path/app-rhoai-v3-5/comp-odh-dashboard")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["nodes"]) == 2
        assert len(data["edges"]) == 1

    def test_path_not_found(self, client):
        with patch("map.backend.graph.find_path", return_value=None):
            resp = client.get("/api/map/path/a/b")
            assert resp.status_code == 404


class TestStatsEndpoint:
    def test_stats(self, client):
        resp = client.get("/api/map/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "nodes" in data
        assert "edges" in data


class TestGapsEndpoint:
    def test_gaps_returns_grouped(self, client):
        resp = client.get("/api/map/gaps")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert "by_type" in data
        assert "missing_pipeline" in data["by_type"]


class TestHealthEndpoint:
    def test_healthy(self, client):
        resp = client.get("/api/map/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_degraded_when_neo4j_down(self, client):
        with patch("map.backend.graph.get_stats", side_effect=ConnectionError("Neo4j down")):
            resp = client.get("/api/map/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "degraded"
