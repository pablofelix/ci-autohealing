"""Tests for the request timeout middleware."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.middleware import RequestTimeoutMiddleware


def _make_app(timeout: float) -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestTimeoutMiddleware, timeout=timeout)

    @app.get("/fast")
    def fast():
        return {"status": "ok"}

    @app.get("/slow")
    def slow():
        time.sleep(5)
        return {"status": "eventually"}

    return app


def test_fast_endpoint_succeeds():
    app = _make_app(timeout=2.0)
    client = TestClient(app)
    response = client.get("/fast")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_slow_endpoint_returns_504():
    app = _make_app(timeout=0.5)
    client = TestClient(app)
    response = client.get("/slow")
    assert response.status_code == 504
    body = response.json()
    assert body["error"] == "Request timeout"
    assert "0s" in body["detail"]
    assert "suggestion" in body


def test_timeout_response_has_correct_detail():
    app = _make_app(timeout=0.1)
    client = TestClient(app)
    response = client.get("/slow")
    assert response.status_code == 504
    body = response.json()
    assert body["detail"] == "Request exceeded 0s limit"


def test_default_timeout_is_30s():
    middleware = RequestTimeoutMiddleware(FastAPI(), timeout=30.0)
    assert middleware.timeout == 30.0
