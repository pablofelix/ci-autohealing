"""Fixtures for System Map E2E BDD tests.

Uses the real map backend + live Neo4j instance.
Tests are marked as e2e and require Neo4j to be running.
"""

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="session")
def map_client():
    """Create a TestClient for the map backend, backed by real Neo4j."""
    neo4j_password = os.environ.get("SLK_NEO4J_PASSWORD", "")
    if not neo4j_password:
        pytest.skip("SLK_NEO4J_PASSWORD not set — skipping map E2E tests")

    from map.backend.main import app

    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/api/map/health")
        if resp.status_code != 200 or resp.json().get("status") != "ok":
            pytest.skip("Neo4j not reachable — skipping map E2E tests")
        yield client
