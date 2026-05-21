"""Mount all API route modules."""

from fastapi import FastAPI

from api.routes import (
    analyses,
    applications,
    exports,
    failures,
    fixes,
    health,
    patterns,
    violations,
)


def mount_routes(app: FastAPI) -> None:
    app.include_router(health.router)
    app.include_router(applications.router, prefix="/api/v1")
    app.include_router(failures.router, prefix="/api/v1")
    app.include_router(violations.router, prefix="/api/v1")
    app.include_router(analyses.router, prefix="/api/v1")
    app.include_router(patterns.router, prefix="/api/v1")
    app.include_router(fixes.router, prefix="/api/v1")
    app.include_router(exports.router, prefix="/api/v1")
