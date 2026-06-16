"""Skill registry endpoints with execution support."""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from mcp_server.models import (
    SkillInfo,
    SkillPrerequisiteResult,
    SkillSourceInfo,
    SkillValidationFinding,
    SkillValidationResult,
)


class SkillRunRequest(BaseModel):
    dry_run: bool = True
    params: Dict[str, str] = {}
    timeout: int = 300


class SkillRunResponse(BaseModel):
    skill_name: str
    status: str
    exit_code: int = 0
    stdout: str = ''
    stderr: str = ''
    duration_seconds: float = 0.0
    risk_level: str = 'medium'
    risk_reasons: List[str] = []
    security_warnings: List[str] = []
    steps_executed: int = 0
    steps_total: int = 0
    dry_run_steps: List[str] = []

router = APIRouter(tags=["skills"])


def _registry():
    from skills.db_registry import get_registry
    return get_registry()


def _entry_to_info(entry) -> SkillInfo:
    return SkillInfo(
        qualified_name=entry.qualified_name,
        name=entry.name,
        source=entry.source,
        description=entry.metadata.description,
        status=entry.status,
        tags=entry.tags,
        category=entry.metadata.category or None,
        allowed_tools=entry.metadata.allowed_tools,
        user_invocable=entry.metadata.user_invocable,
    )


@router.get("/skills")
def list_skills(
    tag: Optional[str] = None,
    source: Optional[str] = None,
) -> List[SkillInfo]:
    """List registered skills, optionally filtered by tag or source."""
    registry = _registry()
    entries = registry.list_skills(tag=tag, source=source)
    return [_entry_to_info(e) for e in entries]


@router.get("/skills/sources")
def list_skill_sources() -> List[SkillSourceInfo]:
    """List registered skill sources (Git repos)."""
    registry = _registry()
    sources = registry.list_sources()
    skills = registry.list_skills()

    source_counts = {}
    for s in skills:
        source_counts[s.source] = source_counts.get(s.source, 0) + 1

    return [
        SkillSourceInfo(
            name=src.name,
            url=src.url,
            commit=src.commit or None,
            skill_count=source_counts.get(src.name, 0),
        )
        for src in sources
    ]


@router.get("/skills/{name}")
def get_skill(name: str) -> SkillInfo:
    """Get details for a specific skill by name or qualified name."""
    registry = _registry()
    try:
        entry = registry.get_skill(name)
    except KeyError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not entry:
        raise HTTPException(status_code=404, detail='Skill not found: {}'.format(name))
    return _entry_to_info(entry)


@router.get("/skills/{name}/validate")
def validate_skill(name: str) -> SkillValidationResult:
    """Run static security analysis on a registered skill."""
    registry = _registry()
    try:
        entry = registry.get_skill(name)
    except KeyError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not entry:
        raise HTTPException(status_code=404, detail='Skill not found: {}'.format(name))

    from skills.validator import SkillValidator
    validator = SkillValidator()
    result = validator.validate(entry.path, entry.metadata)

    return SkillValidationResult(
        skill_name=result.skill_name,
        passed=result.passed,
        critical_count=result.critical_count,
        warning_count=result.warning_count,
        findings=[
            SkillValidationFinding(
                severity=f.severity, check=f.check,
                message=f.message, file=f.file, line=f.line,
            )
            for f in result.findings
        ],
    )


@router.get("/skills/{name}/prerequisites")
def check_skill_prerequisites(name: str) -> SkillPrerequisiteResult:
    """Check if required tools and env vars are available for a skill."""
    registry = _registry()
    try:
        entry = registry.get_skill(name)
    except KeyError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not entry:
        raise HTTPException(status_code=404, detail='Skill not found: {}'.format(name))

    from skills.validator import check_prerequisites
    prereqs = check_prerequisites(entry.metadata)

    return SkillPrerequisiteResult(
        skill_name=entry.qualified_name,
        status=prereqs['status'],
        tools=prereqs['tools'],
        env=prereqs['env'],
    )


@router.post("/skills/{name}/run")
def run_skill(name: str, request: SkillRunRequest) -> SkillRunResponse:
    """Execute a skill (dry_run=true by default for safety)."""
    registry = _registry()
    try:
        entry = registry.get_skill(name)
    except KeyError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not entry:
        raise HTTPException(status_code=404, detail='Skill not found: {}'.format(name))

    from skills.executor import SkillExecutor
    executor = SkillExecutor(
        entry, params=request.params,
        dry_run=request.dry_run, timeout=request.timeout,
        triggered_by='api',
    )

    assessment = executor.assess()
    result = executor.execute()

    try:
        registry.record_run(result)
    except Exception:
        pass

    return SkillRunResponse(
        skill_name=result.skill_name,
        status=result.status,
        exit_code=result.exit_code,
        stdout=result.stdout[:50000],
        stderr=result.stderr[:10000],
        duration_seconds=result.duration_seconds,
        risk_level=assessment.level,
        risk_reasons=assessment.reasons,
        security_warnings=assessment.security_warnings,
        steps_executed=result.steps_executed,
        steps_total=result.steps_total,
        dry_run_steps=result.dry_run_steps,
    )


@router.get("/skills/runs")
def list_skill_runs(
    skill: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """List skill execution history."""
    registry = _registry()
    try:
        return registry.get_run_history(skill_name=skill, limit=limit)
    except Exception:
        return []
