"""Conforma violation endpoints."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter

from mcp_server.models import ConformaViolationDetails
from repositories.conforma_repository import ConformaRepository
from repositories.repository_factory import get_repository
from repositories.triage_repository import TriageRepository

from shared_config import KONFLUX_UI_BASE, NAMESPACE

router = APIRouter(tags=["violations"])


def _conforma_repo():
    return get_repository(ConformaRepository)


def _triage_repo():
    return get_repository(TriageRepository)


def _triage_jira_map(application):
    """Build component→jira_key map from all active triage items (single query)."""
    jira_map = {}
    try:
        for item in _triage_repo().get_active_items(application):
            jk = item.get('jira_key')
            if jk:
                for c in item.get('components', []):
                    if c not in jira_map:
                        jira_map[c] = jk
    except Exception:
        pass
    return jira_map


def _konflux_url(pr_name: str) -> str:
    return f"{KONFLUX_UI_BASE}/ns/{NAMESPACE}/pipelinerun/{pr_name}/logs"


@router.get("/applications/{application}/violations")
def list_violations(application: str) -> List[Dict[str, Any]]:
    """List unresolved Conforma violations (summary only, no violation_details)."""
    from api.validators import validate_application_name
    application = validate_application_name(application)
    repo = _conforma_repo()
    violations = repo.get_violation_summaries(application)
    jira_map = _triage_jira_map(application)
    for v in violations:
        if not v.get('jira_key'):
            v['jira_key'] = jira_map.get(v.get('component_name'))
    return violations


@router.get("/applications/{application}/violations/report")
def get_conforma_report(application: str) -> Dict[str, Any]:
    """Conforma violations report (summary, no violation_details)."""
    from api.validators import validate_application_name
    application = validate_application_name(application)
    repo = _conforma_repo()
    violations = repo.get_violation_summaries(application)
    jira_map = _triage_jira_map(application)
    for v in violations:
        if not v.get('jira_key'):
            v['jira_key'] = jira_map.get(v.get('component_name'))
    return {
        'application': application,
        'total_violations': len(violations),
        'violations': violations,
    }


@router.get("/applications/{application}/violations/{component}",
            response_model=Optional[ConformaViolationDetails])
def get_violation(component: str, application: str, include_details: bool = True):
    """Full Conforma policy violation details."""
    row = _conforma_repo().get_violation_details(component, application)
    if not row:
        from api.errors import not_found
        not_found('Conforma violation', component,
                  suggestion="Run 'ic get conforma' to see current violations")

    details = row.get('violation_details') if include_details else None
    konflux = _konflux_url(row.get('pipelinerun_name', ''))

    return ConformaViolationDetails(
        component=row['component_name'],
        scenario=row['scenario'],
        violations_count=row['violations_count'],
        warnings_count=row['warnings_count'],
        successes_count=row['successes_count'],
        violation_summary=row.get('violation_summary', ''),
        violation_details=details,
        repository_url=row.get('repository_url'),
        commit_sha=row.get('commit_sha'),
        snapshot_name=row.get('snapshot_name'),
        konflux_url=konflux,
        first_detected_at=row.get('first_detected_at', datetime.utcnow()),
    )
