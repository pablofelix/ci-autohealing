"""Application endpoints."""

from typing import Any, Dict, List

from fastapi import APIRouter

from mcp_server.models import (
    ApplicationInfo,
    DashboardResponse,
    StatsResponse,
    TriageResponse,
)
from repositories.ai_analysis_repository import AIAnalysisRepository
from repositories.build_failure_repository import BuildFailureRepository
from repositories.conforma_repository import ConformaRepository
from repositories.error_pattern_repository import ErrorPatternRepository
from repositories.repository_factory import get_repository, get_pool
from repositories.resolution_attempt_repository import ResolutionAttemptRepository

router = APIRouter(tags=["applications"])


def _build_repo():
    return get_repository(BuildFailureRepository)


def _conforma_repo():
    return get_repository(ConformaRepository)


def _ai_repo():
    return get_repository(AIAnalysisRepository)


@router.get("/applications", response_model=List[ApplicationInfo])
def list_applications():
    """List all available RHOAI versions/applications."""
    repo = _build_repo()
    apps = repo.get_applications()
    conforma_repo = _conforma_repo()
    results = []
    for app_row in apps:
        app_name = app_row['application']
        conforma_failing = conforma_repo.find_unresolved_component_names(app_name)
        stats = repo.get_overview_stats(app_name)
        results.append(ApplicationInfo(
            name=app_name,
            component_count=stats['components'],
            failure_count=stats['total'],
            conforma_count=len(conforma_failing),
        ))
    return results


@router.get("/applications/{application}/stats", response_model=StatsResponse)
def get_stats(application: str):
    """Summary statistics: pending counts, analysis stats, costs."""
    ai_repo = _ai_repo()
    extended = ai_repo.get_extended_status(application)
    recent = ai_repo.get_recent_analyses(application, days=7, limit=10)
    build_stats = extended.get('build', {})
    conforma_stats = extended.get('conforma', {})
    return StatsResponse(
        application=application,
        build_failures={
            'pending': build_stats.get('pending', 0),
            'analyzed': build_stats.get('analyzed', 0),
            'autofixable': build_stats.get('auto_fixable', 0),
        },
        conforma_violations={
            'pending': conforma_stats.get('pending', 0),
            'analyzed': conforma_stats.get('analyzed', 0),
            'autofixable': conforma_stats.get('auto_fixable', 0),
        },
        total_cost_30d=extended.get('total_cost', 0),
        recent_analyses=recent,
    )


@router.get("/applications/{application}/stats/daily")
def get_daily_stats(application: str, days: int = 7) -> List[Dict[str, Any]]:
    """Failure counts by day (time series)."""
    return _build_repo().get_daily_stats(application, days)


@router.get("/applications/{application}/triage-summary", response_model=TriageResponse)
def get_triage(application: str):
    """Triage summary: failing vs working components (build status)."""
    triage = _build_repo().get_triage_summary(application)
    return TriageResponse(
        application=application,
        total=triage.get('total', 0),
        failing=triage.get('failing', 0),
        working=triage.get('working', 0),
        failing_components=triage.get('failing_components', []),
    )


@router.get("/applications/{application}/dashboard", response_model=DashboardResponse)
def get_dashboard(application: str):
    """Full dashboard: analysis queue, patterns, costs."""
    build_repo = _build_repo()
    ai_repo = _ai_repo()

    overview = build_repo.get_overview_stats(application)
    enrichment = build_repo.get_enrichment_coverage(application)
    overview['enrichment'] = enrichment

    ai_status = ai_repo.get_extended_status(application)
    cost = ai_repo.get_cost_summary()

    patterns = get_repository(ErrorPatternRepository).get_library_summary()
    fixes = get_repository(ResolutionAttemptRepository).get_outcome_summary()

    return DashboardResponse(
        application=application,
        overview=overview,
        ai_status={**ai_status, 'cost_30d': cost},
        patterns=patterns,
        fixes=fixes,
    )


@router.get("/applications/{application}/health")
def get_health(application: str) -> List[Dict[str, Any]]:
    """Component health scores and status."""
    from proactive.health_monitor import HealthMonitor
    monitor = HealthMonitor(get_pool())
    return monitor.get_component_health_summary() or []


@router.get("/applications/{application}/health/warnings")
def get_health_warnings(application: str):
    """Proactive warnings: trending issues, degradation signals."""
    from mcp_server.models import HealthWarning
    from proactive.health_monitor import HealthMonitor
    monitor = HealthMonitor(get_pool())
    checks = monitor.run_checks()
    return [
        HealthWarning(
            type=w.signal_type,
            component=w.component_name,
            message=w.message,
            severity=w.severity,
        )
        for w in checks
    ]
