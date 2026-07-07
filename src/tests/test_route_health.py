"""Tests for the health check endpoint."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import health

app = FastAPI()
app.include_router(health.router)
client = TestClient(app)


@patch("api.routes.health.get_pool")
def test_health_returns_healthy_when_db_connected(mock_get_pool):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = (1,)
    mock_conn.cursor.return_value = mock_cursor
    mock_pool = MagicMock()
    mock_pool.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_pool.connection.return_value.__exit__ = MagicMock(return_value=False)
    mock_get_pool.return_value = mock_pool

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "database": "connected"}


@patch("api.routes.health.get_pool")
def test_health_returns_unhealthy_when_db_raises(mock_get_pool):
    mock_pool = MagicMock()
    mock_pool.connection.side_effect = Exception("connection refused")
    mock_get_pool.return_value = mock_pool

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "unhealthy"


@patch("api.routes.health.get_pool")
def test_health_returns_error_message_in_database_field(mock_get_pool):
    error_msg = "could not connect to server: timeout expired"
    mock_pool = MagicMock()
    mock_pool.connection.side_effect = Exception(error_msg)
    mock_get_pool.return_value = mock_pool

    response = client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "unhealthy"
    assert data["database"] == error_msg
