"""Skill registry endpoints (read-only)."""

from typing import List, Optional

from fastapi import APIRouter, HTTPException

from mcp_server.models import SkillInfo, SkillSourceInfo

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
