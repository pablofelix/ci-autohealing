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

    def get_onboarding(self, application: str) -> dict | None:
        """GET /api/v1/applications/{app}/onboarding → per-component onboarding scores."""
        return self._get(f"/api/v1/applications/{application}/onboarding")

    def get_pr_activity(self, application: str) -> dict | None:
        """GET /api/v1/applications/{app}/pr-activity → recent fix PRs."""
        return self._get(f"/api/v1/applications/{application}/pr-activity")

    def get_failure(self, component: str, application: str) -> dict | None:
        """GET /api/v1/applications/{app}/failures/{comp} → build failure details (no logs)."""
        return self._get(
            f"/api/v1/applications/{application}/failures/{component}"
            "?include_logs=false&include_commit_context=false"
        )

    def get_analysis(self, component: str, application: str) -> dict | None:
        """GET /api/v1/applications/{app}/analyses/{comp} → AI root cause analysis."""
        return self._get(f"/api/v1/applications/{application}/analyses/{component}")

    def get_triage(self, application: str) -> dict | None:
        """GET /api/v1/applications/{app}/triage → active triage items."""
        return self._get(f"/api/v1/applications/{application}/triage")

    def get_violation(self, component: str, application: str) -> dict | None:
        """GET /api/v1/applications/{app}/violations/{comp} → conforma violations."""
        return self._get(f"/api/v1/applications/{application}/violations/{component}")

    def get_blockers(self, application: str) -> dict | None:
        """GET /api/v1/applications/{app}/blockers → Jira blocker analysis."""
        return self._get(f"/api/v1/applications/{application}/blockers")

    def get_stats(self, application: str) -> dict | None:
        """GET /api/v1/applications/{app}/stats → build/conforma summary counts."""
        return self._get(f"/api/v1/applications/{application}/stats")

    def get_daily_stats(self, application: str, days: int = 7) -> list | None:
        """GET /api/v1/applications/{app}/stats/daily → failure counts by day."""
        return self._get(f"/api/v1/applications/{application}/stats/daily?days={days}")

    def get_resolved(self, application: str, days: int = 7) -> list | None:
        """GET /api/v1/applications/{app}/resolved → recently fixed failures."""
        return self._get(f"/api/v1/applications/{application}/resolved?days={days}")

    def get_nightly(self, application: str) -> dict | None:
        """GET /api/v1/applications/{app}/nightly → nightly build + FBC health."""
        return self._get(f"/api/v1/applications/{application}/nightly")

    def get_schedule(self, application: str) -> dict | None:
        """GET /api/v1/applications/{app}/schedule → release milestone dates."""
        return self._get(f"/api/v1/applications/{application}/schedule")

    def get_health_warnings(self, application: str) -> list | None:
        """GET /api/v1/applications/{app}/health/warnings → proactive warnings."""
        return self._get(f"/api/v1/applications/{application}/health/warnings")

    def get_stale(self, application: str) -> list | None:
        """GET /api/v1/applications/{app}/stale → components with untriggered commits."""
        return self._get(f"/api/v1/applications/{application}/stale")

    def get_triage_summary(self, application: str) -> dict | None:
        """GET /api/v1/applications/{app}/triage-summary → failing/working counts."""
        return self._get(f"/api/v1/applications/{application}/triage-summary")

    def trigger_rebuild(self, component: str, application: str) -> dict | None:
        """POST /api/v1/applications/{app}/rebuild/{comp} → trigger a new build."""
        return self._post(
            f"/api/v1/applications/{application}/rebuild/{component}",
            {},
        )

    def create_triage(self, application: str, data: dict) -> dict | None:
        """POST /api/v1/applications/{app}/triage → create triage item."""
        return self._post(f"/api/v1/applications/{application}/triage", data)

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

    def _post(self, path: str, data: dict):
        try:
            resp = self._session.post(
                f"{self.base_url}{path}", json=data, timeout=self.timeout,
            )
            if resp.status_code in (200, 201):
                return resp.json()
            logger.warning("IC API POST %s returned %s: %s",
                           path, resp.status_code, resp.text[:200])
            return None
        except requests.RequestException as exc:
            logger.warning("IC API POST %s unreachable: %s", path, exc)
            return None
