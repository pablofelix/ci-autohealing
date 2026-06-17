"""AI analysis endpoints."""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from mcp_server.models import AnalysisDetails
from repositories.ai_analysis_repository import AIAnalysisRepository
from repositories.repository_factory import get_repository


class AnalysisSubmission(BaseModel):
    component: str
    analysis_type: str = 'build'
    model_used: str = ''
    root_cause: str = ''
    failure_category: str = ''
    confidence_score: float = 0.0
    recommended_fix: str = ''
    recommended_files: List[str] = []
    can_auto_fix: bool = False
    requires_human_review: bool = False
    tokens_used: int = 0
    cost_usd: float = 0.0
    analysis_json: Optional[Dict[str, Any]] = None

router = APIRouter(tags=["analyses"])


def _ai_repo():
    return get_repository(AIAnalysisRepository)


def _row_to_analysis(row: Dict[str, Any]) -> AnalysisDetails:
    files = row.get('recommended_files') or []
    if isinstance(files, str):
        files = [f.strip() for f in files.split(",") if f.strip()]
    return AnalysisDetails(
        type=row['analysis_type'],
        component=row['component_name'],
        model_used=row.get('model_used', ''),
        root_cause=row.get('root_cause', ''),
        failure_category=row.get('failure_category', ''),
        confidence_score=float(row.get('confidence_score') or 0),
        recommended_fix=row.get('recommended_fix', ''),
        recommended_files=files,
        can_auto_fix=bool(row.get('can_auto_fix')),
        requires_human_review=bool(row.get('requires_human_review')),
        analyzed_at=row.get('analyzed_at', datetime.utcnow()),
        langfuse_trace_url=row.get('langfuse_trace_url'),
        tokens_used=row.get('tokens_used') or 0,
        cost_usd=float(row.get('cost_usd') or 0),
    )


@router.get("/applications/{application}/analyses")
def list_analyses(application: str, days: int = 30, limit: int = 20) -> List[Dict[str, Any]]:
    """Recent AI analyses for an application."""
    return _ai_repo().get_recent_analyses(application, days=days, limit=limit)


@router.get("/applications/{application}/analyses/stats")
def get_analysis_stats(application: str) -> Dict[str, Any]:
    """Analysis category statistics."""
    return _ai_repo().get_category_stats(application)


@router.get("/applications/{application}/analyses/{component}",
            response_model=Optional[AnalysisDetails])
def get_analysis(
    component: str,
    application: str,
    type: Literal["auto", "build", "conforma"] = "auto",
):
    """AI analysis for a specific component."""
    row = _ai_repo().get_analysis_by_component(component, application, type)
    if not row:
        return None
    return _row_to_analysis(row)


@router.post("/applications/{application}/analyses")
def submit_analysis(application: str, submission: AnalysisSubmission) -> Dict[str, Any]:
    """Store an AI analysis result (e.g. from local LLM proxy).

    Allows the CLI to run LLM analysis locally and upload the result
    to the cluster API for storage.
    """
    repo = _ai_repo()
    from repositories.build_failure_repository import BuildFailureRepository
    build_repo = get_repository(BuildFailureRepository)

    failure = build_repo.get_failure_details(submission.component, application)
    if not failure:
        from api.errors import not_found
        not_found('Component', submission.component,
                  suggestion="Run 'ic get alerts' to see current failures")

    failure_id = failure.get('id')
    if not failure_id:
        from api.errors import ICError
        raise ICError(400, 'no_failure_id', 'Cannot link analysis — failure has no ID')

    analysis_id = repo.insert_analysis(
        build_failure_id=failure_id if submission.analysis_type == 'build' else None,
        conforma_result_id=failure_id if submission.analysis_type == 'conforma' else None,
        model_used=submission.model_used,
        root_cause=submission.root_cause,
        failure_category=submission.failure_category,
        confidence_score=submission.confidence_score,
        recommended_fix=submission.recommended_fix,
        recommended_files=submission.recommended_files,
        can_auto_fix=submission.can_auto_fix,
        requires_human_review=submission.requires_human_review,
        tokens_used=submission.tokens_used,
        cost_usd=submission.cost_usd,
        analysis_duration=0,
        analysis_json=submission.analysis_json,
    )

    return {
        'action': 'stored',
        'analysis_id': analysis_id,
        'component': submission.component,
        'confidence': submission.confidence_score,
    }
