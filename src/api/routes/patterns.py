"""Error pattern endpoints."""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter

from repositories.error_pattern_repository import ErrorPatternRepository
from repositories.repository_factory import get_repository

router = APIRouter(tags=["patterns"])


def _pattern_repo():
    return get_repository(ErrorPatternRepository)


@router.get("/patterns")
def list_patterns(failure_type: Optional[str] = None) -> List[Dict[str, Any]]:
    """List known error patterns from the pattern library."""
    return _pattern_repo().get_all(failure_type)


@router.get("/patterns/{name}")
def get_pattern(name: str) -> Optional[Dict[str, Any]]:
    """Detailed pattern info: description, typical fix, docs."""
    return _pattern_repo().get_by_name_or_category(name)
