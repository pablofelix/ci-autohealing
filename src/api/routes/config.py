"""Runtime configuration endpoints."""

from typing import Any, Dict, List

from fastapi import APIRouter
from pydantic import BaseModel

from repositories.config_repository import ConfigRepository
from repositories.repository_factory import get_repository

router = APIRouter(tags=["config"])


def _repo():
    return get_repository(ConfigRepository)


class WatchedAppsResponse(BaseModel):
    applications: List[str]


class AddAppRequest(BaseModel):
    application: str


@router.get("/config/applications")
def get_watched_applications() -> WatchedAppsResponse:
    """List applications being monitored."""
    apps = _repo().get_watched_applications()
    return WatchedAppsResponse(applications=apps)


@router.post("/config/applications")
def add_watched_application(request: AddAppRequest) -> WatchedAppsResponse:
    """Add an application to the watch list."""
    apps = _repo().add_watched_application(request.application, updated_by='api')
    return WatchedAppsResponse(applications=apps)


@router.delete("/config/applications/{application}")
def remove_watched_application(application: str) -> WatchedAppsResponse:
    """Remove an application from the watch list."""
    apps = _repo().remove_watched_application(application, updated_by='api')
    return WatchedAppsResponse(applications=apps)


@router.get("/config")
def get_all_config() -> List[Dict[str, Any]]:
    """List all runtime configuration."""
    return _repo().get_all()
