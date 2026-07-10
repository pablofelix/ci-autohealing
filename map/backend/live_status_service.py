"""Live status service — business logic and caching for IC overlay.

Fetches IC data via ICClient, computes per-node status (green/yellow/red),
and builds an activity feed from recent alerts. Caches results to avoid
hammering the IC API on every frontend poll.
"""

import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime

from .ic_client import ICClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NodeStatus:
    node_id: str
    node_type: str  # "Component" or "Application"
    status: str  # "healthy", "degraded", "failing", "unknown"
    health_score: int | None  # 0-100 or None
    border_color: str  # hex color for frontend
    detail: str = ""


@dataclass(frozen=True)
class ActivityEvent:
    timestamp: str  # ISO 8601
    component: str
    event_type: str  # "build_failure", "conforma_violation"
    severity: str  # "error", "warning", "info"
    message: str


STATUS_COLORS = {
    "healthy": "#10b981",
    "degraded": "#f59e0b",
    "failing": "#ef4444",
    "unknown": "#9ca3af",
}


def _compute_status(health_score: int | None) -> str:
    if health_score is None:
        return "unknown"
    if health_score >= 80:
        return "healthy"
    if health_score >= 50:
        return "degraded"
    return "failing"


class LiveStatusService:
    """Orchestrates IC data fetching, status computation, and caching."""

    def __init__(self, client: ICClient | None = None, cache_ttl: int = 55):
        self._client = client or ICClient()
        self._cache_ttl = cache_ttl
        self._cache: dict = {}  # {app: {data: ..., ts: float}}

    def get_live_status(self, application: str) -> dict:
        cached = self._cache.get(application)
        if cached and (time.time() - cached["ts"]) < self._cache_ttl:
            return cached["data"]

        result = self._fetch_and_compute(application)
        self._cache[application] = {"data": result, "ts": time.time()}
        return result

    def _fetch_and_compute(self, application: str) -> dict:
        alerts = self._client.get_alerts(application)
        health_list = self._client.get_health(application)
        readiness = self._client.get_readiness(application)

        ic_available = any(x is not None for x in (alerts, health_list, readiness))

        nodes = self._build_node_statuses(alerts, health_list, readiness, application)
        activity = self._build_activity_feed(alerts)

        return {
            "application": application,
            "nodes": [asdict(n) for n in nodes],
            "activity": [asdict(e) for e in activity],
            "ic_available": ic_available,
            "last_updated": datetime.utcnow().isoformat() + "Z",
        }

    def _build_node_statuses(
        self, alerts, health_list, readiness, application,
    ) -> list[NodeStatus]:
        statuses: dict[str, NodeStatus] = {}

        # 1. Health scores (most authoritative per-component data)
        if health_list:
            for comp in health_list:
                name = comp.get("component_name") or comp.get("name", "")
                score = comp.get("health_score")
                if name and score is not None:
                    node_id = f"comp-{name}"
                    status = _compute_status(score)
                    statuses[node_id] = NodeStatus(
                        node_id=node_id,
                        node_type="Component",
                        status=status,
                        health_score=int(score),
                        border_color=STATUS_COLORS[status],
                    )

        # 2. Alerts override — failures are worse than health degradation
        if alerts:
            for failure in alerts.get("build_failures", []):
                comp = failure.get("component", "")
                if comp:
                    node_id = f"comp-{comp}"
                    existing = statuses.get(node_id)
                    statuses[node_id] = NodeStatus(
                        node_id=node_id,
                        node_type="Component",
                        status="failing",
                        health_score=existing.health_score if existing else None,
                        border_color=STATUS_COLORS["failing"],
                        detail=failure.get("error_type", "Build failure"),
                    )

            for violation in alerts.get("conforma_violations", []):
                comp = violation.get("component", "")
                blocks = violation.get("blocks", "none")
                if comp and blocks not in ("none", ""):
                    node_id = f"comp-{comp}"
                    existing = statuses.get(node_id)
                    if not existing or existing.status != "failing":
                        statuses[node_id] = NodeStatus(
                            node_id=node_id,
                            node_type="Component",
                            status="degraded",
                            health_score=existing.health_score if existing else None,
                            border_color=STATUS_COLORS["degraded"],
                            detail=violation.get("error_type", "Conforma violation"),
                        )

        # 3. Application-level status from readiness verdict
        if readiness:
            verdict = readiness.get("verdict", "UNKNOWN")
            app_status = (
                "healthy" if verdict == "READY"
                else "degraded" if verdict == "AT_RISK"
                else "failing" if verdict == "NOT_READY"
                else "unknown"
            )
            app_id = f"app-{application}"
            statuses[app_id] = NodeStatus(
                node_id=app_id,
                node_type="Application",
                status=app_status,
                health_score=100 if verdict == "READY" else 50 if verdict == "AT_RISK" else 20,
                border_color=STATUS_COLORS[app_status],
                detail=f"Release: {verdict}",
            )

        return list(statuses.values())

    def _build_activity_feed(self, alerts, limit: int = 20) -> list[ActivityEvent]:
        if not alerts:
            return []
        events = []

        for f in alerts.get("build_failures", []):
            ts = f.get("last_seen") or f.get("first_seen", "")
            if isinstance(ts, datetime):
                ts = ts.isoformat() + "Z"
            events.append(ActivityEvent(
                timestamp=str(ts),
                component=f.get("component", "unknown"),
                event_type="build_failure",
                severity="error",
                message=f.get("error_type") or f.get("status", "Build failed"),
            ))

        for v in alerts.get("conforma_violations", []):
            ts = v.get("last_seen") or v.get("first_seen", "")
            if isinstance(ts, datetime):
                ts = ts.isoformat() + "Z"
            blocks = v.get("blocks", "none")
            severity = "warning" if blocks not in ("none", "") else "info"
            events.append(ActivityEvent(
                timestamp=str(ts),
                component=v.get("component", "unknown"),
                event_type="conforma_violation",
                severity=severity,
                message=v.get("error_type") or "Policy violation",
            ))

        events.sort(key=lambda e: e.timestamp, reverse=True)
        return events[:limit]
