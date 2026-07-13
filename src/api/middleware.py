"""API middleware: authentication, CORS, and request timeout."""

import asyncio
import os
import secrets
from typing import Optional

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

_API_KEY: Optional[str] = os.environ.get("IC_API_KEY")

REQUEST_TIMEOUT_SECONDS = 30.0


class RequestTimeoutMiddleware(BaseHTTPMiddleware):
    """Abort requests that exceed a time limit.

    Prevents slow endpoints (e.g. /alerts with N+1 queries) from blocking
    all uvicorn workers and starving the entire API server.
    """

    def __init__(self, app, timeout: float = REQUEST_TIMEOUT_SECONDS):
        super().__init__(app)
        self.timeout = timeout

    async def dispatch(self, request: Request, call_next):
        try:
            response = await asyncio.wait_for(
                call_next(request), timeout=self.timeout
            )
            return response
        except TimeoutError:
            return JSONResponse(
                status_code=504,
                content={
                    "error": "Request timeout",
                    "detail": "Request exceeded {}s limit".format(int(self.timeout)),
                    "suggestion": "Try a more specific query or use pagination",
                },
            )


def setup_middleware(app: FastAPI) -> None:
    # Timeout middleware first — wraps everything including CORS.
    app.add_middleware(RequestTimeoutMiddleware, timeout=REQUEST_TIMEOUT_SECONDS)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next) -> Response:
        if request.url.path in ("/health", "/docs", "/openapi.json", "/redoc"):
            return await call_next(request)

        if not _API_KEY:
            return await call_next(request)

        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing Authorization header (Bearer token)"},
            )

        token = auth[len("Bearer "):]
        if not secrets.compare_digest(token, _API_KEY):
            return JSONResponse(
                status_code=403,
                content={"detail": "Invalid API key"},
            )

        return await call_next(request)
