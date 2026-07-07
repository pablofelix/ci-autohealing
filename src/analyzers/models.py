"""Pydantic models for AI analysis validation.

Validates LLM outputs to catch hallucinations and enforce schemas.
Separate models for build failures vs Conforma violations.
"""

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class EvidenceReference(BaseModel):
    """A reference to documentation, config file, or log evidence."""
    type: Literal['doc', 'config', 'log', 'policy']
    url: str = ''
    description: str = Field(..., min_length=3)


class SourceTransparency(BaseModel):
    """Academic-style source attribution and limitation disclosure."""
    sources_consulted: List[str] = Field(
        default_factory=list,
        description="What data sources were actually used in this analysis"
    )
    sources_unavailable: List[str] = Field(
        default_factory=list,
        description="Sources that were attempted but failed or were not available"
    )
    limitations: List[str] = Field(
        default_factory=list,
        description="Factors that could change the diagnosis if more data were available"
    )


class AnalysisResult(BaseModel):
    """Validated analysis output from LLM for build failures."""

    root_cause: str = Field(
        ...,
        min_length=10,
        description="Detailed root cause explanation"
    )

    failure_category: Literal[
        'dependency_issue',
        'build_error',
        'test_failure',
        'resource_limit',
        'config_error',
        'git_sync_issue',
        'infrastructure'
    ] = Field(
        ...,
        description="Classification of the failure type"
    )

    confidence_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence in analysis (0.0 to 1.0)"
    )

    recommended_fix: str = Field(
        ...,
        min_length=10,
        description="Specific fix recommendation with file paths"
    )

    recommended_files: List[str] = Field(
        default_factory=list,
        description="List of files that need modification"
    )

    can_auto_fix: bool = Field(
        default=False,
        description="Whether this failure can be automatically fixed"
    )

    requires_human_review: bool = Field(
        default=True,
        description="Whether human review is required before applying fix"
    )

    evidence_references: List[EvidenceReference] = Field(
        default_factory=list,
        description="Links to docs, config files, or log evidence supporting the diagnosis"
    )

    source_transparency: Optional[SourceTransparency] = Field(
        default=None,
        description="What sources were used, what was unavailable, and analysis limitations"
    )

    @field_validator('confidence_score')
    @classmethod
    def round_confidence(cls, v):
        """Round confidence to 2 decimals."""
        return round(v, 2)

    @field_validator('recommended_files')
    @classmethod
    def validate_files(cls, v):
        """Ensure files are non-empty strings."""
        return [f.strip() for f in v if f and f.strip()]

    @field_validator('root_cause', 'recommended_fix')
    @classmethod
    def validate_not_placeholder(cls, v):
        """Ensure LLM didn't return placeholder text."""
        placeholders = ['n/a', 'none', 'unknown', 'todo', 'tbd']
        if v.lower().strip() in placeholders:
            raise ValueError(f"Invalid placeholder value: {v}")
        return v.strip()


class ConformaAnalysisResult(BaseModel):
    """Validated analysis output from LLM for Conforma policy violations."""

    root_cause: str = Field(
        ...,
        min_length=10,
        description="Detailed explanation of policy violation root cause"
    )

    failure_category: Literal[
        'policy_hermetic_build',
        'policy_unpinned_task',
        'policy_untrusted_image',
        'policy_signing_key',
        'policy_package_source',
        'policy_rpm_repository',
        'policy_version_label',
        'policy_fips_check',
        'policy_deprecated_task',
        'policy_deprecated_image',
        'policy_slsa_provenance',
        'policy_snyk_error',
        'policy_labels',
        'policy_sbom_vendor_label',
        'policy_cpe_label',
        'policy_source_image',
        'config_error',
        'infrastructure'
    ] = Field(
        ...,
        description="Classification of the Conforma policy violation"
    )

    confidence_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence in analysis (0.0 to 1.0)"
    )

    recommended_fix: str = Field(
        ...,
        min_length=10,
        description="Fix options (vendor, approved alternative, or policy exception)"
    )

    recommended_files: List[str] = Field(
        default_factory=list,
        description="List of files/configs that need modification"
    )

    can_auto_fix: bool = Field(
        default=False,
        description="Whether this violation can be automatically fixed"
    )

    requires_human_review: bool = Field(
        default=True,
        description="Whether human review is required (usually True for policy violations)"
    )

    evidence_references: List[EvidenceReference] = Field(
        default_factory=list,
        description="Links to docs, config files, or log evidence supporting the diagnosis"
    )

    source_transparency: Optional[SourceTransparency] = Field(
        default=None,
        description="What sources were used, what was unavailable, and analysis limitations"
    )

    @field_validator('confidence_score')
    @classmethod
    def round_confidence(cls, v):
        """Round confidence to 2 decimals."""
        return round(v, 2)

    @field_validator('recommended_files')
    @classmethod
    def validate_files(cls, v):
        """Ensure files are non-empty strings."""
        return [f.strip() for f in v if f and f.strip()]

    @field_validator('root_cause', 'recommended_fix')
    @classmethod
    def validate_not_placeholder(cls, v):
        """Ensure LLM didn't return placeholder text."""
        placeholders = ['n/a', 'none', 'unknown', 'todo', 'tbd']
        if v.lower().strip() in placeholders:
            raise ValueError(f"Invalid placeholder value: {v}")
        return v.strip()


class ReleaseAnalysisResult(BaseModel):
    """Validated analysis output from LLM for release pipeline failures."""

    root_cause: str = Field(
        ...,
        min_length=10,
        description="Detailed explanation of why the release failed"
    )

    failure_category: Literal[
        'unmapped_image',
        'rpa_mapping_typo',
        'cross_product_dependency',
        'missing_ec_exception',
        'build_artifact_missing',
        'validation_error',
        'publish_failure',
        'access_denied',
        'infrastructure'
    ] = Field(
        ...,
        description="Classification of the release failure type"
    )

    confidence_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence in analysis (0.0 to 1.0)"
    )

    recommended_fix: str = Field(
        ...,
        min_length=10,
        description="Specific fix recommendation with file paths and team owners"
    )

    recommended_files: List[str] = Field(
        default_factory=list,
        description="Files that need modification (in Build-Config or konflux-release-data)"
    )

    fix_action_type: Literal[
        'rebuild',
        'file_change',
        'config_change',
        'multi_step',
        'investigation_needed',
        'other'
    ] = Field(
        default='investigation_needed',
        description="Type of fix action: rebuild, file_change, config_change, multi_step, investigation_needed, other"
    )

    can_auto_fix: bool = Field(
        default=False,
        description="Whether this failure can be automatically fixed"
    )

    requires_human_review: bool = Field(
        default=True,
        description="Whether human review is required (usually True for release failures)"
    )

    affected_images: List[str] = Field(
        default_factory=list,
        description="List of image refs that caused the failure"
    )

    owner_team: str = Field(
        default='',
        description="Which team should fix this (e.g., RHOAI, RHAII, RelEng)"
    )

    evidence_references: List[EvidenceReference] = Field(
        default_factory=list,
        description="Links to docs, config files, or log evidence supporting the diagnosis"
    )

    source_transparency: Optional[SourceTransparency] = Field(
        default=None,
        description="What sources were used, what was unavailable, and analysis limitations"
    )

    @field_validator('confidence_score')
    @classmethod
    def round_confidence(cls, v):
        return round(v, 2)

    @field_validator('recommended_files', 'affected_images')
    @classmethod
    def validate_string_list(cls, v):
        return [f.strip() for f in v if f and f.strip()]

    @field_validator('root_cause', 'recommended_fix')
    @classmethod
    def validate_not_placeholder(cls, v):
        placeholders = ['n/a', 'none', 'unknown', 'todo', 'tbd']
        if v.lower().strip() in placeholders:
            raise ValueError(f"Invalid placeholder value: {v}")
        return v.strip()


class OnboardingAnalysisResult(BaseModel):
    """Validated analysis output from LLM for onboarding blockers."""

    root_cause: str = Field(
        ...,
        min_length=10,
        description="What is blocking this onboarding and why"
    )

    failure_category: Literal[
        'automation_stuck',
        'pr_review_needed',
        'missing_prerequisite',
        'configuration_error',
        'infrastructure_issue',
        'branch_conflict',
        'first_build_failing',
        'manual_intervention',
        'upstream_dependency',
    ] = Field(
        ...,
        description="Classification of the onboarding blocker"
    )

    confidence_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence in analysis (0.0 to 1.0)"
    )

    recommended_fix: str = Field(
        ...,
        min_length=10,
        description="Specific actions to unblock onboarding"
    )

    blocked_step: str = Field(
        default='',
        description="Which automation or Konflux step is blocked"
    )

    can_auto_fix: bool = Field(
        default=False,
        description="Whether this can be automatically resolved"
    )

    requires_human_review: bool = Field(
        default=True,
        description="Whether human review is needed"
    )

    evidence_references: List[EvidenceReference] = Field(
        default_factory=list,
        description="Links to Jira tickets, PRs, or docs"
    )

    source_transparency: Optional[SourceTransparency] = Field(
        default=None,
        description="What data sources were used in this analysis"
    )

    @field_validator('confidence_score')
    @classmethod
    def round_confidence(cls, v):
        return round(v, 2)

    @field_validator('root_cause', 'recommended_fix')
    @classmethod
    def validate_not_placeholder(cls, v):
        placeholders = ['n/a', 'none', 'unknown', 'todo', 'tbd']
        if v.lower().strip() in placeholders:
            raise ValueError(f"Invalid placeholder value: {v}")
        return v.strip()


class ScenariosAnalysisResult(BaseModel):
    """Validated analysis output from LLM for ITS scenario configuration."""

    findings: str = Field(
        ...,
        min_length=10,
        description="Detailed findings about ITS configuration issues"
    )

    severity: Literal[
        'critical',
        'warning',
        'info',
    ] = Field(
        ...,
        description="Overall severity of findings"
    )

    confidence_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence in analysis (0.0 to 1.0)"
    )

    recommendations: str = Field(
        ...,
        min_length=10,
        description="Prioritized recommendations"
    )

    issues_count: int = Field(
        default=0,
        ge=0,
        description="Number of issues found"
    )

    @field_validator('confidence_score')
    @classmethod
    def round_confidence(cls, v):
        return round(v, 2)

    @field_validator('findings', 'recommendations')
    @classmethod
    def validate_not_placeholder(cls, v):
        placeholders = ['n/a', 'none', 'unknown', 'todo', 'tbd']
        if v.lower().strip() in placeholders:
            raise ValueError(f"Invalid placeholder value: {v}")
        return v.strip()


class CategoryMetrics(BaseModel):
    """Accuracy metrics for a single failure category."""
    correct: int = 0
    partial: int = 0
    incorrect: int = 0
    total: int = 0
    avg_confidence: float = 0.0


class RegressionMetrics(BaseModel):
    """Aggregate regression test results from resolved conforma violations."""

    total_resolved: int = Field(
        ..., ge=0, description="Total resolved violations in the dataset"
    )
    with_ai_analysis: int = Field(
        ..., ge=0, description="Resolved violations that had AI analysis"
    )
    ai_coverage_pct: float = Field(
        ..., ge=0.0, le=100.0, description="Percentage of resolved violations with AI analysis"
    )
    accuracy: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Overall category accuracy (0-1)"
    )
    calibration_score: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="How well confidence scores predict correctness (0-1)"
    )
    by_category: Dict[str, CategoryMetrics] = Field(
        default_factory=dict,
        description="Accuracy breakdown per failure_category"
    )
    auto_fix_accuracy: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="How well can_auto_fix matches actual resolution speed"
    )
    coverage_gaps: List[str] = Field(
        default_factory=list,
        description="Violation rule groups with 0% AI coverage"
    )
    improvements: List[str] = Field(
        default_factory=list,
        description="Specific suggestions to improve the analyzer"
    )
    verifiable_count: int = Field(
        default=0, ge=0,
        description="Evaluations with ground truth (included in accuracy)"
    )
    unverifiable_count: int = Field(
        default=0, ge=0,
        description="Evaluations without ground truth (excluded from accuracy)"
    )


class ConfigFinding(BaseModel):
    """A single finding from the Konflux configuration audit."""

    title: str = Field(..., min_length=5, description="Short finding title")
    severity: Literal['critical', 'warning', 'info']
    category: Literal[
        'expired_exceptions', 'policy_gap', 'scenario_coverage',
        'pipeline_config', 'auto_rebuild_candidate', 'rule_catalog_gap'
    ]
    description: str = Field(..., min_length=10, description="Detailed description")
    recommendation: str = Field(..., min_length=10, description="What to do about it")
    affected_components: List[str] = Field(
        default_factory=list,
        description="Components affected by this finding"
    )
    can_auto_fix: bool = Field(
        default=False,
        description="Whether this can be fixed automatically (e.g., rebuild)"
    )
    fix_action: Literal[
        'rebuild', 'config_change', 'exception',
        'pipeline_update', 'investigation'
    ] = Field(
        default='investigation',
        description="Type of fix action needed"
    )


class ConfigAnalysisResult(BaseModel):
    """Validated output from the Konflux configuration analyzer."""

    findings: List[ConfigFinding] = Field(
        default_factory=list,
        description="List of configuration issues found"
    )
    overall_severity: Literal['critical', 'warning', 'info'] = Field(
        ..., description="Worst severity among findings"
    )
    confidence_score: float = Field(
        ..., ge=0.0, le=1.0, description="Confidence in analysis (0-1)"
    )
    summary: str = Field(
        ..., min_length=10, description="Overall configuration health summary"
    )
    auto_rebuild_candidates: List[str] = Field(
        default_factory=list,
        description="Components that would likely be fixed by a rebuild"
    )

    @field_validator('confidence_score')
    @classmethod
    def round_confidence(cls, v):
        return round(v, 2)

    @field_validator('summary')
    @classmethod
    def validate_not_placeholder(cls, v):
        placeholders = ['n/a', 'none', 'unknown', 'todo', 'tbd']
        if v.lower().strip() in placeholders:
            raise ValueError(f"Invalid placeholder value: {v}")
        return v.strip()
