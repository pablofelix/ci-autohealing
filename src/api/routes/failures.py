"""Build failure endpoints."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter

from mcp_server.models import (
    AlertsSummary,
    BuildFailureDetails,
    ComponentHistoryResponse,
    FailureSummary,
    NightlyWarning,
)
from repositories.build_failure_repository import BuildFailureRepository
from repositories.conforma_repository import ConformaRepository
from repositories.triage_repository import TriageRepository
from repositories.repository_factory import get_repository, get_pool

from shared_config import KONFLUX_UI_BASE, NAMESPACE

router = APIRouter(tags=["failures"])


def _build_repo():
    return get_repository(BuildFailureRepository)


def _conforma_repo():
    return get_repository(ConformaRepository)


def _triage_repo():
    return get_repository(TriageRepository)


def _konflux_url(pr_name: str) -> str:
    return f"{KONFLUX_UI_BASE}/ns/{NAMESPACE}/pipelinerun/{pr_name}/logs"


@router.get("/applications/{application}/alerts", response_model=AlertsSummary)
def list_alerts(application: str):
    """All unresolved alerts (build failures + Conforma violations)."""
    from api.validators import validate_application_name
    application = validate_application_name(application)
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

    from utils.conforma_utils import (
        extract_violation_rules, fetch_exceptions_by_policy,
        policy_url as _policy_url, categorize_policy,
        count_unique_violations, compute_exception_coverage_details,
    )
    exceptions_by_policy = fetch_exceptions_by_policy(NAMESPACE)
    conforma_violations = []
    conforma_failing = _conforma_repo().find_unresolved_component_names(application)
    triage_jira = _triage_repo().build_jira_map(application)
    for comp_name in sorted(conforma_failing):
        details = _conforma_repo().get_violation_details(comp_name, application)
        if details:
            scenario = details.get('scenario', '')
            jira_key = details.get('jira_key') or triage_jira.get(comp_name)
            rules = extract_violation_rules(details.get('violation_summary', ''))
            cov = compute_exception_coverage_details(
                rules, scenario, exceptions_by_policy)
            uv_count, _ = count_unique_violations(
                details.get('violation_summary', ''))
            conforma_violations.append(FailureSummary(
                component=comp_name,
                status='Conforma Violation',
                error_type=scenario,
                first_seen=details.get('first_detected_at', datetime.utcnow()),
                last_seen=details.get('last_updated_at', datetime.utcnow()),
                occurrence_count=1,
                has_logs=details.get('violation_summary') is not None,
                has_analysis=details.get('ai_analyzed', False),
                violations_count=details.get('violations_count', 0),
                unique_violations=uv_count or None,
                warnings_count=details.get('warnings_count', 0),
                scenario=scenario,
                category=categorize_policy(scenario),
                policy_url=_policy_url(scenario),
                jira_key=jira_key,
                exception_coverage=cov['coverage'],
                exception_coverage_stage=cov['stage'],
                exception_coverage_prod=cov['prod'],
                exception_env_tag=cov['env_tag'],
                policy_url_stage=cov['policy_url_stage'],
                policy_url_prod=cov['policy_url_prod'],
                uncovered_rules_stage=cov['uncovered_rules_stage'],
                uncovered_rules_prod=cov['uncovered_rules_prod'],
            ))

    nightly_warnings = []
    try:
        from proactive.health_monitor import HealthMonitor
        from repositories.connection import PooledDatabaseConnection
        db = PooledDatabaseConnection(get_pool())
        monitor = HealthMonitor(db)
        for w in monitor.get_stale_nightly_builds():
            nightly_warnings.append(NightlyWarning(
                component_name=w.component_name,
                severity=w.severity,
                message=w.message,
            ))
    except Exception:
        pass

    all_dates = ([f.last_seen for f in build_failures] +
                 [v.last_seen for v in conforma_violations])
    last_sync = max(all_dates) if all_dates else datetime.utcnow()

    schedule = None
    try:
        from api.routes.releases import get_schedule
        schedule = get_schedule(application)
    except Exception:
        pass

    return AlertsSummary(
        application=application,
        build_failures=build_failures,
        conforma_violations=conforma_violations,
        nightly_warnings=nightly_warnings,
        total_count=len(build_failures) + len(conforma_violations) + len(nightly_warnings),
        last_sync=last_sync,
        release_schedule=schedule,
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
        from api.errors import not_found
        not_found('Component', component,
                  suggestion="Run 'ic get alerts' to see current failures")

    konflux = row.get('konflux_url') or _konflux_url(row.get('pipelinerun_name', ''))
    logs = row.get('build_logs') if include_logs else None
    context = row.get('commit_context') if include_commit_context else None

    history_data = _build_repo().get_component_history(component, application, limit=10)
    builds = history_data.get('builds', [])

    return BuildFailureDetails(
        component=row['component_name'],
        pipelinerun_name=row.get('pipelinerun_name'),
        status=row.get('status'),
        error_message=row.get('error_message'),
        error_type=row.get('error_type'),
        failed_task=row.get('failed_task_name'),
        failed_step=row.get('failed_step_name'),
        task_summary=row.get('task_summary'),
        build_logs=logs,
        commit_sha=row.get('commit_sha'),
        commit_message=row.get('commit_message'),
        commit_author=row.get('commit_author'),
        commit_url=row.get('commit_url'),
        repository_url=row.get('repository_url'),
        branch=row.get('branch'),
        output_image=row.get('output_image'),
        jira_key=row.get('jira_key'),
        build_duration_seconds=row.get('build_duration_seconds'),
        ai_analyzed=row.get('ai_analyzed'),
        is_resolved=row.get('is_resolved'),
        commit_context=context,
        konflux_url=konflux,
        first_detected_at=row.get('first_detected_at', datetime.utcnow()),
        build_history=builds,
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


@router.get("/lookup")
def lookup_image(image: str) -> Dict[str, Any]:
    """Find which component produced a given image or digest."""
    db_matches = _build_repo().find_by_image(image)

    cluster_matches = []
    try:
        from clients.kubernetes import KubernetesClient
        kc = KubernetesClient(namespace=NAMESPACE)
        all_comps = kc.list_components()
        term = image.lower()
        cluster_matches = [c for c in all_comps if term in (c.get('container_image') or '').lower()]
    except Exception:
        pass

    return {
        'query': image,
        'db_matches': db_matches,
        'cluster_matches': cluster_matches,
        'total_matches': len(db_matches) + len(cluster_matches),
    }
