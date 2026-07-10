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
class OnboardingStatus:
    node_id: str
    score: int  # 0-100
    overall: str  # "complete", "partial", "incomplete"
    badge_color: str  # hex color for badge
    checks: dict  # {check_name: {status, detail, fix}}
    failing: list  # check names that are failing
    warnings: list  # check names with warnings
    jira_key: str = ""


@dataclass(frozen=True)
class ActivityEvent:
    timestamp: str  # ISO 8601
    component: str
    event_type: str  # "build_failure", "conforma_violation"
    severity: str  # "error", "warning", "info"
    message: str


ONBOARDING_COLORS = {
    "complete": "#10b981",   # green
    "partial": "#f59e0b",    # yellow
    "incomplete": "#ef4444", # red
}

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


def _build_health_detail(comp: dict, status: str) -> str:
    """Build a human-readable detail string from health data."""
    parts = []
    if status == "failing":
        consecutive = comp.get("consecutive_failures", 0)
        if consecutive:
            parts.append(f"{consecutive} consecutive failures")
        last_fail = comp.get("last_failed_build")
        if last_fail:
            parts.append(f"last fail: {str(last_fail)[:10]}")
    elif status == "degraded":
        score = comp.get("health_score", 0)
        parts.append(f"health {score}/100")
        rate = comp.get("success_rate_last_7d")
        if rate is not None:
            parts.append(f"7d success: {rate}%")
    else:
        last_ok = comp.get("last_successful_build")
        if last_ok:
            parts.append(f"last build: {str(last_ok)[:10]}")
        else:
            parts.append("last build: success")
    return " | ".join(parts) if parts else ""


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
        onboarding_data = self._client.get_onboarding(application)
        pr_data = self._client.get_pr_activity(application)

        ic_available = any(x is not None for x in (alerts, health_list, readiness))

        nodes = self._build_node_statuses(alerts, health_list, readiness, application)
        activity = self._build_activity_feed(alerts)
        pr_events = self._build_pr_events(pr_data)
        activity = sorted(activity + pr_events, key=lambda e: e.timestamp, reverse=True)[:30]
        onboarding = self._build_onboarding_statuses(onboarding_data)

        return {
            "application": application,
            "nodes": [asdict(n) for n in nodes],
            "activity": [asdict(e) for e in activity],
            "onboarding": [asdict(o) for o in onboarding],
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
                    detail = _build_health_detail(comp, status)
                    statuses[node_id] = NodeStatus(
                        node_id=node_id,
                        node_type="Component",
                        status=status,
                        health_score=int(score),
                        border_color=STATUS_COLORS[status],
                        detail=detail,
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

    def _build_pr_events(self, pr_data) -> list[ActivityEvent]:
        if not pr_data:
            return []
        events = []
        for pr in pr_data.get("prs", []):
            comp = pr.get("component_name", "unknown")
            ts = pr.get("attempted_at", "")
            if isinstance(ts, datetime):
                ts = ts.isoformat() + "Z"
            pr_num = pr.get("pr_number", "")
            label = f"PR #{pr_num}" if pr_num else "PR"

            merged = pr.get("pr_merged")
            success = pr.get("was_successful")
            if merged and success:
                severity = "pr_merged"
                message = f"{label} merged and verified"
            elif merged and success is False:
                severity = "pr_stale"
                message = f"{label} merged but fix failed"
            elif merged:
                severity = "pr_merged"
                message = f"{label} merged"
            else:
                severity = "pr_open"
                message = f"{label} opened"

            events.append(ActivityEvent(
                timestamp=str(ts),
                component=comp,
                event_type="fix_pr",
                severity=severity,
                message=message,
            ))
        return events

    def _build_onboarding_statuses(self, onboarding_data) -> list[OnboardingStatus]:
        if not onboarding_data:
            return []
        components = onboarding_data.get("components", [])
        statuses = []
        for comp in components:
            name = comp.get("component", "")
            if not name:
                continue
            overall = comp.get("overall", "incomplete")
            statuses.append(OnboardingStatus(
                node_id=f"comp-{name}",
                score=comp.get("score", 0),
                overall=overall,
                badge_color=ONBOARDING_COLORS.get(overall, ONBOARDING_COLORS["incomplete"]),
                checks=comp.get("checks", {}),
                failing=comp.get("failing", []),
                warnings=comp.get("warnings", []),
                jira_key=comp.get("jira_key", ""),
            ))
        return statuses
