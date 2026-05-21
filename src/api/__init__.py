"""Konflux CI Monitoring REST API.

Usage:
    python -m api               # uvicorn on port 8000
    python -m serve --api       # via unified entry point
"""

from fastapi import FastAPI

from config import CollectorConfig
from repositories.repository_factory import init_pool


def create_app() -> FastAPI:
    cfg = CollectorConfig.from_env()
    init_pool(cfg.db, pool_size=10)

    app = FastAPI(
        title="Konflux CI Monitoring API",
        description="Read-only REST API for RHOAI CI/CD failure tracking and analysis.",
        version="1.0.0",
    )

    from api.middleware import setup_middleware
    setup_middleware(app)

    from api.routes import mount_routes
    mount_routes(app)

    return app
