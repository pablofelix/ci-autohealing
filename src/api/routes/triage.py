"""Triage tracking endpoints."""

from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from api.errors import not_found, validation_error
from repositories.repository_factory import get_repository
from repositories.triage_repository import TriageRepository

router = APIRouter(tags=["triage"])


def _triage_repo():
    return get_repository(TriageRepository)


class TrackRequest(BaseModel):
    component: str
    group_label: Optional[str] = None
    issue_type: str = 'build'
    root_cause: Optional[str] = None
    failed_step: Optional[str] = None
    slack_thread_url: Optional[str] = None
    jira_key: Optional[str] = None
    notes: Optional[str] = None
    add_to_id: Optional[int] = None
    reference_url: Optional[str] = None


class UpdateRequest(BaseModel):
    slack_thread_url: Optional[str] = None
    jira_key: Optional[str] = None
    notes: Optional[str] = None
    root_cause: Optional[str] = None
    group_label: Optional[str] = None
    status: Optional[str] = None
    reference_url: Optional[str] = None

    model_config = {"json_schema_extra": {
        "description": "slack_thread_url appends to the list (does not replace)"
    }}


class ResolveRequest(BaseModel):
    resolution: Optional[str] = None
    resolution_pr_url: Optional[str] = None
    verdict: Optional[str] = None


@router.get("/applications/{application}/triage")
def get_triage_report(application: str, date: Optional[str] = None) -> Dict[str, Any]:
    """Get triage tracking report with active and resolved items."""
    repo = _triage_repo()
    items = repo.get_report(application, date=date)
    summary = repo.get_summary(application)
    return {"application": application, "summary": summary, "items": items}


@router.get("/applications/{application}/triage/report")
def get_triage_report_alt(application: str, date: Optional[str] = None) -> Dict[str, Any]:
    """Triage report (alternative path to avoid route collision)."""
    return get_triage_report(application, date=date)


@router.get("/applications/{application}/triage/{item_id}")
def get_triage_item(application: str, item_id: int) -> Dict[str, Any]:
    """Get a single triage item by ID."""
    repo = _triage_repo()
    item = repo.get_by_id(item_id)
    if not item:
        not_found("Triage item", f"#{item_id}")
    return item


@router.post("/applications/{application}/triage")
def track_triage_item(application: str, req: TrackRequest) -> Dict[str, Any]:
    """Track a build failure or violation in triage."""
    repo = _triage_repo()

    if req.add_to_id:
        existing = repo.get_by_id(req.add_to_id)
        if not existing:
            not_found("Triage item", f"#{req.add_to_id}")
        components = list(existing['components'])
        if req.component not in components:
            components.append(req.component)
        updates = {'components': components}
        if req.jira_key:
            updates['jira_key'] = req.jira_key
        if req.notes:
            updates['notes'] = req.notes
        repo.update_item(req.add_to_id, **updates)
        if req.slack_thread_url:
            repo.add_slack_url(req.add_to_id, req.slack_thread_url)
        if req.reference_url:
            repo.add_reference_url(req.add_to_id, req.reference_url)
        return {"action": "added", "item_id": req.add_to_id, "component": req.component}

    existing = repo.find_by_component(req.component, application, issue_type=req.issue_type)
    if existing:
        return {"action": "exists", "item": existing}

    item_id = repo.create_item(
        application=application,
        components=[req.component],
        group_label=req.group_label,
        issue_type=req.issue_type,
        root_cause=req.root_cause,
        failed_step=req.failed_step,
        slack_thread_urls=[req.slack_thread_url] if req.slack_thread_url else None,
        reference_urls=[req.reference_url] if req.reference_url else None,
        jira_key=req.jira_key,
        notes=req.notes,
    )
    return {"action": "created", "item_id": item_id, "component": req.component}


@router.put("/applications/{application}/triage/{item_id}")
def update_triage_item(application: str, item_id: int, req: UpdateRequest) -> Dict[str, Any]:
    """Update a triage item's tracking info."""
    repo = _triage_repo()
    existing = repo.get_by_id(item_id)
    if not existing:
        not_found("Triage item", f"#{item_id}")

    updates = {k: v for k, v in req.model_dump().items()
               if v is not None and k not in ('slack_thread_url', 'reference_url')}
    if not updates and not req.slack_thread_url and not req.reference_url:
        validation_error("No updates provided")

    if updates:
        repo.update_item(item_id, **updates)
    if req.slack_thread_url:
        added = repo.add_slack_url(item_id, req.slack_thread_url)
        if not added and 'slack.com/archives/' not in req.slack_thread_url:
            validation_error("URL doesn't look like a Slack thread (expected slack.com/archives/...). Use reference_url for PRs and other links.")
        updates['slack_thread_urls'] = 'added'
    if req.reference_url:
        repo.add_reference_url(item_id, req.reference_url)
        updates['reference_urls'] = 'added'
    return {"action": "updated", "item_id": item_id, "updates": list(updates.keys())}


@router.post("/applications/{application}/triage/{item_id}/resolve")
def resolve_triage_item(application: str, item_id: int, req: ResolveRequest) -> Dict[str, Any]:
    """Mark a triage item as resolved."""
    repo = _triage_repo()
    existing = repo.get_by_id(item_id)
    if not existing:
        not_found("Triage item", f"#{item_id}")

    repo.resolve_item(item_id, resolution=req.resolution, pr_url=req.resolution_pr_url)

    if req.verdict and req.verdict in ('correct', 'partial', 'incorrect'):
        from repositories.ai_analysis_repository import AIAnalysisRepository
        from repositories.repository_factory import get_repository
        ai_repo = get_repository(AIAnalysisRepository)
        components = existing.get('components', [])
        if components:
            ai_repo.record_verdict(components[0], application, req.verdict, verdict_by='api')

    return {"action": "resolved", "item_id": item_id}
