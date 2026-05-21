"""Conforma violation endpoints."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter

from mcp_server.models import ConformaViolationDetails
from repositories.conforma_repository import ConformaRepository
from repositories.repository_factory import get_repository

from cli.config import KONFLUX_UI_BASE, NAMESPACE

router = APIRouter(tags=["violations"])


def _conforma_repo():
    return get_repository(ConformaRepository)


def _konflux_url(pr_name: str) -> str:
    return f"{KONFLUX_UI_BASE}/ns/{NAMESPACE}/pipelinerun/{pr_name}/logs"


@router.get("/applications/{application}/violations")
def list_violations(application: str) -> List[Dict[str, Any]]:
    """List unresolved Conforma violations."""
    repo = _conforma_repo()
    failing = repo.find_unresolved_component_names(application)
    violations = []
    for comp_name in sorted(failing):
        details = repo.get_violation_details(comp_name, application)
        if details:
            violations.append(details)
    return violations


@router.get("/applications/{application}/violations/report")
def get_conforma_report(application: str) -> Dict[str, Any]:
    """Full Conforma violations report."""
    repo = _conforma_repo()
    failing = repo.find_unresolved_component_names(application)
    violations = []
    for comp_name in sorted(failing):
        details = repo.get_violation_details(comp_name, application)
        if details:
            violations.append(details)
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
        return None

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
