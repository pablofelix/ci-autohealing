"""Build failure endpoints."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter

from mcp_server.models import (
    AlertsSummary,
    BuildFailureDetails,
    ComponentHistoryResponse,
    FailureSummary,
)
from repositories.build_failure_repository import BuildFailureRepository
from repositories.conforma_repository import ConformaRepository
from repositories.repository_factory import get_repository

from cli.config import KONFLUX_UI_BASE, NAMESPACE

router = APIRouter(tags=["failures"])


def _build_repo():
    return get_repository(BuildFailureRepository)


def _conforma_repo():
    return get_repository(ConformaRepository)


def _konflux_url(pr_name: str) -> str:
    return f"{KONFLUX_UI_BASE}/ns/{NAMESPACE}/pipelinerun/{pr_name}/logs"


@router.get("/applications/{application}/alerts", response_model=AlertsSummary)
def list_alerts(application: str):
    """All unresolved alerts (build failures + Conforma violations)."""
    triage = _build_repo().get_triage_summary(application)
    build_failures = []
    for comp in triage.get('failing_components', []):
        build_failures.append(FailureSummary(
            component=comp['component'],
            status=comp.get('status', 'Failed'),
            error_type=comp.get('error_type'),
            first_seen=comp.get('first_detected_at', datetime.utcnow()),
            last_seen=comp.get('last_updated_at', datetime.utcnow()),
            occurrence_count=comp.get('failure_count', 1),
            has_logs=comp.get('has_logs', False),
            has_analysis=comp.get('ai_analyzed', False),
        ))

    conforma_violations = []
    conforma_failing = _conforma_repo().find_unresolved_component_names(application)
    for comp_name in sorted(conforma_failing):
        details = _conforma_repo().get_violation_details(comp_name, application)
        if details:
            conforma_violations.append(FailureSummary(
                component=comp_name,
                status='Conforma Violation',
                error_type=details.get('scenario'),
                first_seen=details.get('first_detected_at', datetime.utcnow()),
                last_seen=details.get('last_updated_at', datetime.utcnow()),
                occurrence_count=1,
                has_logs=details.get('violation_summary') is not None,
                has_analysis=details.get('ai_analyzed', False),
            ))

    all_dates = ([f.last_seen for f in build_failures] +
                 [v.last_seen for v in conforma_violations])
    last_sync = max(all_dates) if all_dates else datetime.utcnow()

    return AlertsSummary(
        application=application,
        build_failures=build_failures,
        conforma_violations=conforma_violations,
        total_count=len(build_failures) + len(conforma_violations),
        last_sync=last_sync,
    )


@router.get("/applications/{application}/failures")
def list_failures(
    application: str,
    has_analysis: Optional[bool] = None,
    limit: int = 50,
) -> List[FailureSummary]:
    """List active build failures."""
    triage = _build_repo().get_triage_summary(application)
    results = []
    for comp in triage.get('failing_components', []):
        if has_analysis is True and not comp.get('ai_analyzed'):
            continue
        if has_analysis is False and comp.get('ai_analyzed'):
            continue
        results.append(FailureSummary(
            component=comp['component'],
            status=comp.get('status', 'Failed'),
            error_type=comp.get('error_type'),
            first_seen=comp.get('first_detected_at', datetime.utcnow()),
            last_seen=comp.get('last_updated_at', datetime.utcnow()),
            occurrence_count=comp.get('failure_count', 1),
            has_logs=comp.get('has_logs', False),
            has_analysis=comp.get('ai_analyzed', False),
        ))
    results.sort(key=lambda f: f.last_seen, reverse=True)
    return results[:limit]


@router.get("/applications/{application}/failures/{component}",
            response_model=Optional[BuildFailureDetails])
def get_failure(
    component: str,
    application: str,
    include_logs: bool = True,
    include_commit_context: bool = True,
):
    """Full build failure details for a component."""
    row = _build_repo().get_failure_details(component, application)
    if not row:
        return None

    konflux = row.get('konflux_url') or _konflux_url(row.get('pipelinerun_name', ''))
    logs = row.get('build_logs') if include_logs else None
    context = row.get('commit_context') if include_commit_context else None

    return BuildFailureDetails(
        component=row['component_name'],
        pipelinerun_name=row.get('pipelinerun_name'),
        error_message=row.get('error_message'),
        error_type=row.get('error_type'),
        failed_task=row.get('failed_task_name'),
        failed_step=row.get('failed_step_name'),
        build_logs=logs,
        commit_sha=row.get('commit_sha'),
        commit_message=row.get('commit_message'),
        commit_author=row.get('commit_author'),
        commit_url=row.get('commit_url'),
        repository_url=row.get('repository_url'),
        branch=row.get('branch'),
        commit_context=context,
        konflux_url=konflux,
        first_detected_at=row.get('first_detected_at', datetime.utcnow()),
    )


@router.get("/applications/{application}/failures/{component}/history",
            response_model=ComponentHistoryResponse)
def get_component_history(component: str, application: str, limit: int = 20):
    """Build history for a component (successes + failures)."""
    data = _build_repo().get_component_history(component, application, limit)
    return ComponentHistoryResponse(
        component=component,
        application=application,
        summary=data.get('summary', {}),
        builds=data.get('builds', []),
    )


@router.get("/applications/{application}/working")
def get_working(application: str) -> List[Dict[str, Any]]:
    """Components currently working (last build = success)."""
    return _build_repo().get_working_components(application)


@router.get("/applications/{application}/resolved")
def get_resolved(application: str) -> List[Dict[str, Any]]:
    """Components resolved (previously failing, now fixed)."""
    return _build_repo().get_resolved_components(application)
