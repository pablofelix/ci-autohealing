"""Simple in-memory rate limiter for the API.

Limits requests per API key or IP. No external dependencies.
"""

import time
from collections import defaultdict

from fastapi import Request, Response
from fastapi.responses import JSONResponse


class RateLimiter:
    """Token bucket rate limiter."""

    def __init__(self, read_limit=100, write_limit=10, window_seconds=60):
        self.read_limit = read_limit
        self.write_limit = write_limit
        self.window = window_seconds
        self._buckets = defaultdict(list)

    def _clean(self, key):
        now = time.time()
        self._buckets[key] = [t for t in self._buckets[key] if now - t < self.window]

    def _check(self, key, limit):
        self._clean(key)
        if len(self._buckets[key]) >= limit:
            return False
        self._buckets[key].append(time.time())
        return True

    def is_allowed(self, key, is_write=False):
        limit = self.write_limit if is_write else self.read_limit
        return self._check(key, limit)


_limiter = RateLimiter()
_WRITE_METHODS = {'POST', 'PUT', 'DELETE', 'PATCH'}


def setup_rate_limiter(app):
    @app.middleware("http")
    async def rate_limit_middleware(request: Request, call_next) -> Response:
        if request.url.path in ('/health', '/docs', '/openapi.json'):
            return await call_next(request)

        auth = request.headers.get('Authorization', '')
        key = auth[-8:] if auth else request.client.host
        is_write = request.method in _WRITE_METHODS

        if not _limiter.is_allowed(key, is_write=is_write):
            import logging
            logging.getLogger('api.rate_limit').warning(
                "Rate limit hit: key=%s method=%s path=%s",
                key, request.method, request.url.path)
            return JSONResponse(
                status_code=429,
                content={
                    'error': 'rate_limit_exceeded',
                    'detail': 'Too many requests. Limit: {}/min for {} operations.'.format(
                        _limiter.write_limit if is_write else _limiter.read_limit,
                        'write' if is_write else 'read'),
                }
            )

        return await call_next(request)
