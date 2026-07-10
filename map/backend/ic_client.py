"""IC API client for live status overlay.

HTTP-only, no database imports. Fetches component health data from
the IC API server and returns structured dicts.
"""

import logging
import os

import requests

logger = logging.getLogger(__name__)

_DEFAULT_URL = "http://localhost:8000"
_TIMEOUT = 10


class ICClient:
    """Thin HTTP client for IC API endpoints."""

    def __init__(self, base_url: str | None = None, timeout: int = _TIMEOUT):
        self.base_url = (base_url or os.environ.get("IC_API_URL", _DEFAULT_URL)).rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers["Accept"] = "application/json"

    def get_alerts(self, application: str) -> dict | None:
        """GET /api/v1/applications/{app}/alerts → build failures + conforma violations."""
        return self._get(f"/api/v1/applications/{application}/alerts")

    def get_health(self, application: str) -> list | None:
        """GET /api/v1/applications/{app}/health → per-component health scores."""
        return self._get(f"/api/v1/applications/{application}/health")

    def get_readiness(self, application: str) -> dict | None:
        """GET /api/v1/applications/{app}/readiness → release verdict."""
        return self._get(f"/api/v1/applications/{application}/readiness")

    def is_available(self) -> bool:
        """Quick reachability check against IC API health endpoint."""
        try:
            resp = self._session.get(f"{self.base_url}/health", timeout=3)
            return resp.status_code == 200
        except Exception:
            return False

    def _get(self, path: str):
        try:
            resp = self._session.get(
                f"{self.base_url}{path}", timeout=self.timeout,
            )
            if resp.status_code == 200:
                return resp.json()
            logger.warning("IC API %s returned %s", path, resp.status_code)
            return None
        except requests.RequestException as exc:
            logger.warning("IC API %s unreachable: %s", path, exc)
            return None
