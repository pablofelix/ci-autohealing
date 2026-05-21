"""Fix attempt endpoints."""

from typing import Any, Dict

from fastapi import APIRouter

from repositories.repository_factory import get_repository
from repositories.resolution_attempt_repository import ResolutionAttemptRepository

router = APIRouter(tags=["fixes"])


def _resolution_repo():
    return get_repository(ResolutionAttemptRepository)


@router.get("/fixes")
def get_fix_history(days: int = 30) -> Dict[str, Any]:
    """Fix attempt history and success rates."""
    repo = _resolution_repo()
    attempts = repo.get_all(days)
    summary = repo.get_outcome_summary(days)
    return {
        'summary': summary,
        'attempts': attempts,
    }
