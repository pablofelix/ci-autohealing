"""Pydantic response models for MCP tools and REST API.

Shared by both MCP server and FastAPI routes.
"""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

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
    violations_count: Optional[int] = None
    unique_violations: Optional[int] = None
    warnings_count: Optional[int] = None
    scenario: Optional[str] = None
    category: Optional[str] = None
    policy_url: Optional[str] = None
    jira_key: Optional[str] = None
    exception_coverage: Optional[str] = None
    exception_coverage_stage: Optional[str] = None
    exception_coverage_prod: Optional[str] = None
    exception_env_tag: Optional[str] = None
    policy_env: Optional[str] = None
    blocks: Optional[str] = None
    policy_url_stage: Optional[str] = None
    policy_url_prod: Optional[str] = None
    uncovered_rules_stage: Optional[List[str]] = None
    uncovered_rules_prod: Optional[List[str]] = None
    age_hours: Optional[float] = Field(None, description="Hours since first_seen")
    is_new: bool = Field(False, description="First seen in last 24h")
    status_changed: bool = Field(False, description="Status or error_type changed in last 24h")
    is_nightly: bool = Field(False, description="Most recent build was a nightly (trigger_type=nightly)")
    failed_step: Optional[str] = Field(None, description="Pipeline step that failed (e.g. build-images, fips-check)")
    konflux_url: Optional[str] = Field(None, description="Link to PipelineRun in Konflux")


class BuildFailureDetails(BaseModel):
    component: str
    pipelinerun_name: Optional[str] = None
    status: Optional[str] = None
    error_message: Optional[str] = None
    error_type: Optional[str] = None
    failed_task: Optional[str] = None
    failed_step: Optional[str] = None
    task_summary: Optional[str] = None
    build_logs: Optional[str] = Field(None, description="Truncated to 50K chars")
    commit_sha: Optional[str] = None
    commit_message: Optional[str] = None
    commit_author: Optional[str] = None
    commit_url: Optional[str] = None
    repository_url: Optional[str] = None
    branch: Optional[str] = None
    output_image: Optional[str] = None
    jira_key: Optional[str] = None
    build_duration_seconds: Optional[int] = None
    ai_analyzed: Optional[bool] = None
    is_resolved: Optional[bool] = None
    commit_context: Optional[Dict[str, Any]] = None
    konflux_url: Optional[str] = None
    first_detected_at: datetime
    build_history: Optional[List[Dict[str, Any]]] = None


class ConformaViolationDetails(BaseModel):
    component: str
    scenario: str
    policy_name: Optional[str] = None
    policy_env: Optional[str] = None
    blocks: Optional[str] = None
    policy_url: Optional[str] = None
    violations_count: int
    unique_violations: Optional[int] = None
    violation_rules: Optional[List[str]] = None
    warnings_count: int
    successes_count: int
    violation_summary: str
    violation_details: Optional[Dict[str, Any]] = None
    exception_coverage: Optional[str] = None
    exception_coverage_stage: Optional[str] = None
    exception_coverage_prod: Optional[str] = None
    exception_env_tag: Optional[str] = None
    covered_rules: Optional[List[str]] = None
    uncovered_rules: Optional[List[str]] = None
    uncovered_rules_stage: Optional[List[str]] = None
    uncovered_rules_prod: Optional[List[str]] = None
    matching_exceptions: Optional[List[Dict[str, Any]]] = None
    repository_url: Optional[str] = None
    commit_sha: Optional[str] = None
    snapshot_name: Optional[str] = None
    konflux_url: str
    first_detected_at: datetime


class EvidenceRef(BaseModel):
    type: str
    url: str = ""
    description: str

class SourceTransparencyInfo(BaseModel):
    sources_consulted: List[str] = []
    sources_unavailable: List[str] = []
    limitations: List[str] = []

class AnalysisDetails(BaseModel):
    type: Literal["build", "conforma"]
    component: str
    model_used: str
    root_cause: str
    failure_category: str
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    recommended_fix: str
    recommended_files: List[str]
    fix_action_type: Optional[str] = None
    can_auto_fix: bool
    requires_human_review: bool
    analyzed_at: datetime
    langfuse_trace_url: Optional[str] = None
    tokens_used: int
    cost_usd: float
    evidence_references: List[EvidenceRef] = []
    source_transparency: Optional[SourceTransparencyInfo] = None


class NightlyWarning(BaseModel):
    component_name: str
    severity: str
    message: str

class FreezeCountdown(BaseModel):
    """Days remaining to each release milestone. Negative = past due."""
    code_freeze_days: Optional[int] = Field(None, description="Days to code freeze (negative = past)")
    initial_rc_days: Optional[int] = Field(None, description="Days to initial RC cut")
    release_date_days: Optional[int] = Field(None, description="Days to GA release date")
    phase: str = Field('unknown', description="Current phase: pre-freeze, frozen, rc, released, unknown")
    urgency: str = Field('normal', description="Urgency level: critical (<3 days to freeze), high (<7), normal, low (>14)")
    message: str = Field('', description="Human-readable summary for AI context")


class AlertsSummary(BaseModel):
    application: str
    build_failures: List[FailureSummary]
    conforma_violations: List[FailureSummary]
    nightly_warnings: List[NightlyWarning] = []
    total_count: int
    last_sync: datetime
    release_schedule: Optional[Dict[str, Any]] = None
    freeze_countdown: Optional[FreezeCountdown] = Field(None, description="Days to release milestones + urgency")
    blocker_signals: List[str] = Field(default_factory=list, description="Critical Jira blocker signals (unassigned, stale, verified-not-closed)")


class BlockerAlert(BaseModel):
    """A single Jira blocker with age and health signals."""
    key: str = Field(..., description="Jira issue key (e.g. RHOAIENG-12345)")
    summary: str
    status: str
    assignee: Optional[str] = None
    age_hours: float = Field(..., description="Hours since creation")
    hours_since_update: float = Field(..., description="Hours since last update")
    resolution: Optional[str] = None
    labels: List[str] = []
    category: str = Field('product', description="Blocker category: product (bug), tfa (test failure), infra (infrastructure), signoff (Product Sign-Off)")
    signals: List[str] = Field(default_factory=list, description="Alert signals: unassigned_24h, stale_48h, verified_not_closed, hardware_blocked")


class BlockersSummary(BaseModel):
    """Jira blocker analysis for an application."""
    application: str
    project: str
    total_blockers: int
    open_blockers: int
    product_blockers: int = Field(0, description="Product bug blockers (not TFA or infra)")
    tfa_blockers: int = Field(0, description="TFA (Test Failure Analysis) test-failure blockers")
    infra_blockers: int = Field(0, description="Infrastructure/CI blockers")
    signoff_blockers: int = Field(0, description="Product Sign-Off blocker tickets (not bugs)")
    blockers: List[BlockerAlert]
    critical_signals: List[str] = Field(default_factory=list, description="Top-level alerts for AI context")


class BouncingIssue(BaseModel):
    """A Jira issue that has been reassigned multiple times without resolution."""
    key: str
    summary: str
    status: str
    bounce_count: int = Field(..., description="Number of component/assignee changes")
    age_days: float = Field(..., description="Days since creation")
    reassignment_history: List[str] = Field(default_factory=list, description="Timeline of reassignments")


class BouncingIssuesSummary(BaseModel):
    """Issues bouncing between teams/components without resolution."""
    application: str
    project: str
    bouncing_issues: List[BouncingIssue]
    total_bouncing: int


class FBCFragmentEntry(BaseModel):
    """A single FBC fragment build with its image SHA."""
    pipelinerun: str
    status: str
    image_digest: Optional[str] = None
    output_image: Optional[str] = None
    built_at: datetime
    is_current: bool = Field(False, description="Whether this is the latest successful build")


class FBCFragmentHistory(BaseModel):
    """FBC fragment SHA history for an application."""
    application: str
    fbc_component: str
    current_sha: Optional[str] = None
    builds: List[FBCFragmentEntry]
    total_builds: int
    builds_today: int = Field(0, description="Number of builds produced today")


class ExceptionStatus(BaseModel):
    """Status of a single EC policy exception."""
    rule: str = Field(..., description="Rule name (e.g., hermetic_task.hermetic)")
    policy: str = Field(..., description="Policy name the exception belongs to")
    permanent: bool = Field(False, description="True if in config.exclude (permanent)")
    effective_until: Optional[str] = Field(None, description="Expiration date (ISO 8601)")
    days_left: Optional[int] = Field(None, description="Days until expiration, negative=expired")
    status: str = Field(..., description="active, expiring_soon (<7 days), expired")
    reference: Optional[str] = None
    gitlab_link: Optional[str] = None


class ExceptionLifecycleSummary(BaseModel):
    """Exception lifecycle tracking across all EC policies."""
    total_exceptions: int
    permanent: int
    active_temporary: int
    expiring_soon: int = Field(0, description="Temporary exceptions expiring within 7 days")
    expired: int = Field(0, description="Expired exceptions still in policy config")
    exceptions: List[ExceptionStatus]


class UnpropagatedFix(BaseModel):
    """A fix that landed on one branch but hasn't reached another."""
    component: str
    resolution_commit: str = Field(..., description="Commit SHA of the fix")
    source_branch: str = Field(..., description="Branch where fix was applied")
    missing_branches: List[str] = Field(default_factory=list, description="Branches missing the fix")
    repository_url: Optional[str] = None
    resolved_at: Optional[datetime] = None


class FixPropagationSummary(BaseModel):
    """Multi-branch fix propagation status for resolved build failures."""
    application: str
    checked_count: int = Field(0, description="Number of resolved fixes checked")
    unpropagated: List[UnpropagatedFix] = Field(default_factory=list)
    total_missing: int = Field(0, description="Total fixes not on main")


class JiraTokenHealth(BaseModel):
    """Jira API token health status."""
    status: str = Field(..., description="Token status: valid, expired, forbidden, unreachable, missing")
    user: Optional[str] = Field(None, description="Authenticated user display name if valid")
    message: str = Field('', description="Human-readable health description")


class ReleaseCondition(BaseModel):
    type: str
    status: str
    reason: str = ''
    message: str = ''


class ArtifactStatus(BaseModel):
    exists: Optional[bool] = None
    method: str = ''
    details: str = ''


class ComponentArtifactHealth(BaseModel):
    digest: str = ''
    healthy: bool = False
    missing: List[str] = []
    sig: Optional[ArtifactStatus] = None
    src: Optional[ArtifactStatus] = None
    att: Optional[ArtifactStatus] = None
    sbom: Optional[ArtifactStatus] = None


class ReleaseDetails(BaseModel):
    name: str
    snapshot: str = ''
    release_plan: str = ''
    created_at: str = ''
    target: str = ''
    release_type: str = ''
    type_label: str = ''
    component_count: int = 0
    conditions: List[ReleaseCondition] = []
    pipeline_ref: Optional[str] = None
    pipeline_ui_url: Optional[str] = None
    failed_task: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    duration_seconds: Optional[int] = None
    advisory_type: Optional[str] = None
    fixed_issues: List[str] = []
    cves: List[Dict[str, str]] = []
    is_failed: bool = False
    is_progressing: bool = False
    error_details: List[str] = []
    snapshot_components: List[Dict[str, str]] = []
    artifact_health: Dict[str, ComponentArtifactHealth] = {}
    ai_analysis: Optional[Dict[str, Any]] = None
    stale_snapshot: Optional[Dict[str, Any]] = None
    ec_policy: Optional[str] = None
    ec_policy_url: Optional[str] = None


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


class SkillInfo(BaseModel):
    qualified_name: str = Field(..., description="source/name")
    name: str
    source: str
    description: str
    status: str
    tags: List[str]
    category: Optional[str] = None
    allowed_tools: Optional[str] = None
    user_invocable: bool = False


class SkillSourceInfo(BaseModel):
    name: str
    url: str
    commit: Optional[str] = None
    branch: Optional[str] = None
    skill_count: int = 0


class SkillValidationFinding(BaseModel):
    severity: str = Field(..., description="'critical' or 'warning'")
    check: str
    message: str
    file: str
    line: int = Field(..., description="1-based line number, 0 if N/A")


class SkillValidationResult(BaseModel):
    skill_name: str
    passed: bool
    critical_count: int
    warning_count: int
    findings: List[SkillValidationFinding]


class SkillPrerequisiteResult(BaseModel):
    skill_name: str
    status: str = Field(..., description="'ok', 'warn', or 'fail'")
    tools: Dict[str, bool]
    env: Dict[str, bool]
