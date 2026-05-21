"""AI analysis endpoints."""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter

from mcp_server.models import AnalysisDetails
from repositories.ai_analysis_repository import AIAnalysisRepository
from repositories.repository_factory import get_repository

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
