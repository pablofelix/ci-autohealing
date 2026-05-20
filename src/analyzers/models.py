"""Pydantic models for AI analysis validation.

Validates LLM outputs to catch hallucinations and enforce schemas.
Separate models for build failures vs Conforma violations.
"""

from typing import List, Literal
from pydantic import BaseModel, Field, field_validator


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

    @field_validator('confidence_score')
    @classmethod
    def round_confidence(cls, v):
        # type: (float) -> float
        """Round confidence to 2 decimals."""
        return round(v, 2)

    @field_validator('recommended_files')
    @classmethod
    def validate_files(cls, v):
        # type: (List[str]) -> List[str]
        """Ensure files are non-empty strings."""
        return [f.strip() for f in v if f and f.strip()]

    @field_validator('root_cause', 'recommended_fix')
    @classmethod
    def validate_not_placeholder(cls, v):
        # type: (str) -> str
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

    @field_validator('confidence_score')
    @classmethod
    def round_confidence(cls, v):
        # type: (float) -> float
        """Round confidence to 2 decimals."""
        return round(v, 2)

    @field_validator('recommended_files')
    @classmethod
    def validate_files(cls, v):
        # type: (List[str]) -> List[str]
        """Ensure files are non-empty strings."""
        return [f.strip() for f in v if f and f.strip()]

    @field_validator('root_cause', 'recommended_fix')
    @classmethod
    def validate_not_placeholder(cls, v):
        # type: (str) -> str
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

    @field_validator('confidence_score')
    @classmethod
    def round_confidence(cls, v):
        # type: (float) -> float
        return round(v, 2)

    @field_validator('recommended_files', 'affected_images')
    @classmethod
    def validate_string_list(cls, v):
        # type: (List[str]) -> List[str]
        return [f.strip() for f in v if f and f.strip()]

    @field_validator('root_cause', 'recommended_fix')
    @classmethod
    def validate_not_placeholder(cls, v):
        # type: (str) -> str
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
        # type: (float) -> float
        return round(v, 2)

    @field_validator('findings', 'recommendations')
    @classmethod
    def validate_not_placeholder(cls, v):
        # type: (str) -> str
        placeholders = ['n/a', 'none', 'unknown', 'todo', 'tbd']
        if v.lower().strip() in placeholders:
            raise ValueError(f"Invalid placeholder value: {v}")
        return v.strip()
