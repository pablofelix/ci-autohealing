"""Pydantic response models for MCP tools and REST API.

Shared by both MCP server and FastAPI routes.
"""

from datetime import datetime
from typing import Dict, Any, List, Literal, Optional
from pydantic import BaseModel, Field


class ApplicationInfo(BaseModel):
    name: str = Field(..., description="Application name (e.g., acme-v2-0)")
    component_count: int
    failure_count: int
    conforma_count: int = 0
    last_sync: Optional[datetime] = None


class FailureSummary(BaseModel):
    component: str
    status: str
    error_type: Optional[str] = None
    first_seen: datetime
    last_seen: datetime
    occurrence_count: int
    has_logs: bool
    has_context: bool = False
    has_analysis: bool
    possible_cause: Optional[str] = None


class BuildFailureDetails(BaseModel):
    component: str
    pipelinerun_name: Optional[str] = None
    error_message: Optional[str] = None
    error_type: Optional[str] = None
    failed_task: Optional[str] = None
    failed_step: Optional[str] = None
    build_logs: Optional[str] = Field(None, description="Truncated to 50K chars")
    commit_sha: Optional[str] = None
    commit_message: Optional[str] = None
    commit_author: Optional[str] = None
    commit_url: Optional[str] = None
    repository_url: Optional[str] = None
    branch: Optional[str] = None
    commit_context: Optional[Dict[str, Any]] = None
    konflux_url: Optional[str] = None
    first_detected_at: datetime


class ConformaViolationDetails(BaseModel):
    component: str
    scenario: str
    violations_count: int
    warnings_count: int
    successes_count: int
    violation_summary: str
    violation_details: Optional[Dict[str, Any]] = None
    repository_url: Optional[str] = None
    commit_sha: Optional[str] = None
    snapshot_name: Optional[str] = None
    konflux_url: str
    first_detected_at: datetime


class AnalysisDetails(BaseModel):
    type: Literal["build", "conforma"]
    component: str
    model_used: str
    root_cause: str
    failure_category: str
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    recommended_fix: str
    recommended_files: List[str]
    can_auto_fix: bool
    requires_human_review: bool
    analyzed_at: datetime
    langfuse_trace_url: Optional[str] = None
    tokens_used: int
    cost_usd: float


class AlertsSummary(BaseModel):
    application: str
    build_failures: List[FailureSummary]
    conforma_violations: List[FailureSummary]
    total_count: int
    last_sync: datetime


class StatsResponse(BaseModel):
    application: str
    build_failures: Dict[str, int]
    conforma_violations: Dict[str, int]
    total_cost_30d: float
    recent_analyses: List[Dict[str, Any]]


class TriageResponse(BaseModel):
    application: str
    total: int
    failing: int
    working: int
    failing_components: List[Dict[str, Any]]


class ComponentHistoryResponse(BaseModel):
    component: str
    application: str
    summary: Dict[str, Any]
    builds: List[Dict[str, Any]]


class DashboardResponse(BaseModel):
    application: str
    overview: Dict[str, Any]
    ai_status: Dict[str, Any]
    patterns: Dict[str, Any]
    fixes: Dict[str, Any]


class HealthWarning(BaseModel):
    type: str
    component: str
    message: str
    severity: str
