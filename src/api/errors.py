"""Structured error handling for the IC API."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class ICError(Exception):
    def __init__(self, status_code, error, detail, suggestion=None):
        self.status_code = status_code
        self.error = error
        self.detail = detail
        self.suggestion = suggestion


def not_found(resource, name, suggestion=None):
    raise ICError(404, 'not_found',
                  '{} not found: {}'.format(resource, name),
                  suggestion)


def validation_error(detail, suggestion=None):
    raise ICError(422, 'validation_error', detail, suggestion)


def config_error(detail, suggestion=None):
    raise ICError(400, 'config_error', detail, suggestion)


def register_error_handlers(app: FastAPI):
    @app.exception_handler(ICError)
    async def ic_error_handler(request: Request, exc: ICError):
        body = {
            'error': exc.error,
            'detail': exc.detail,
        }
        if exc.suggestion:
            body['suggestion'] = exc.suggestion
        return JSONResponse(status_code=exc.status_code, content=body)

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError):
        if request.url.path.startswith('/api/'):
            return JSONResponse(status_code=422, content={
                'error': 'validation_error',
                'detail': str(exc),
            })
        raise exc
