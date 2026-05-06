"""Pydantic response models for MCP tools.

All responses use Pydantic for validation and serialization.
"""

from datetime import datetime
from typing import List, Literal, Optional, Dict, Any
from pydantic import BaseModel, Field


class ApplicationInfo(BaseModel):
    """Available RHOAI version/application."""

    name: str = Field(..., description="Application name (e.g., acme-v2-0)")
    component_count: int = Field(..., description="Total components tracked")
    failure_count: int = Field(..., description="Unresolved build failures")
    conforma_count: int = Field(default=0, description="Unresolved Conforma violations")
    last_sync: Optional[datetime] = Field(None, description="Last data collection")


class FailureSummary(BaseModel):
    """Brief failure info for list views."""

    component: str
    status: str
    error_type: Optional[str] = None
    first_seen: datetime
    last_seen: datetime
    occurrence_count: int
    has_logs: bool
    has_analysis: bool


class BuildFailureDetails(BaseModel):
    """Full build failure context."""

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
    commit_context: Optional[Dict[str, Any]] = Field(None, description="Full JSONB")
    konflux_url: Optional[str] = None
    first_detected_at: datetime


class ConformaViolationDetails(BaseModel):
    """Full Conforma violation context."""

    component: str
    scenario: str
    violations_count: int
    warnings_count: int
    successes_count: int
    violation_summary: str
    violation_details: Optional[Dict[str, Any]] = Field(None, description="JSONB")
    repository_url: Optional[str] = None
    commit_sha: Optional[str] = None
    snapshot_name: Optional[str] = None
    konflux_url: str
    first_detected_at: datetime


class AnalysisDetails(BaseModel):
    """AI analysis result."""

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
    """Unified view like `ic get alerts`."""

    application: str
    build_failures: List[FailureSummary]
    conforma_violations: List[FailureSummary]
    total_count: int
    last_sync: datetime


class StatsResponse(BaseModel):
    """Stats like `ic ai status`."""

    application: str
    build_failures: Dict[str, int] = Field(
        ...,
        description="pending, analyzed, autofixable counts"
    )
    conforma_violations: Dict[str, int] = Field(
        ...,
        description="pending, analyzed, autofixable counts"
    )
    total_cost_30d: float
    recent_analyses: List[Dict[str, Any]]
