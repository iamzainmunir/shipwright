"""The Canon §13.5 error envelope and the exception handlers that render it.

Every non-2xx response body is exactly:

    { "error": { "code", "message", "details", "requestId", "retryable" } }

`ApiError` is the typed exception the API raises; `install_error_handlers` wires up
handlers for `ApiError`, Starlette `HTTPException` (so 404s use the envelope too),
`RequestValidationError` (422 with structured issues), and any unhandled `Exception`
(500, message not leaked). Each response also carries the `X-Request-Id` header.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = structlog.get_logger("foundry.orchestrator")


class ApiError(Exception):
    """A domain error that renders as the Canon §13.5 envelope."""

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details: dict[str, Any] = details or {}
        self.retryable = retryable


def error_envelope(
    *,
    code: str,
    message: str,
    request_id: str,
    details: dict[str, Any] | None = None,
    retryable: bool = False,
) -> dict[str, Any]:
    """Build the locked error envelope body."""
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "requestId": request_id,
            "retryable": retryable,
        }
    }


# ---- helpers for the common statuses (Canon §13.5 code table) ----

def not_found(
    message: str = "Resource not found.", details: dict[str, Any] | None = None
) -> ApiError:
    return ApiError(status_code=404, code="not_found", message=message, details=details)


def forbidden(
    message: str = "Insufficient scope for this action.",
    *,
    missing_scope: str | None = None,
    details: dict[str, Any] | None = None,
) -> ApiError:
    merged = dict(details or {})
    if missing_scope:
        merged.setdefault("missingScope", missing_scope)
    return ApiError(status_code=403, code="insufficient_scope", message=message, details=merged)


def unprocessable(
    message: str = "The request was understood but could not be processed.",
    details: dict[str, Any] | None = None,
) -> ApiError:
    return ApiError(status_code=422, code="unprocessable", message=message, details=details)


def request_id_of(request: Request) -> str:
    """The current request id (set by the request-context middleware) or a fresh one."""
    rid = getattr(request.state, "request_id", None)
    if not rid:
        rid = request.headers.get("x-request-id") or uuid4().hex
    return rid


def _json(status_code: int, body: dict[str, Any], request_id: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=body, headers={"X-Request-Id": request_id})


def install_error_handlers(app: FastAPI) -> None:
    """Register the envelope-rendering exception handlers on the app."""

    @app.exception_handler(ApiError)
    async def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        rid = request_id_of(request)
        return _json(
            exc.status_code,
            error_envelope(
                code=exc.code,
                message=exc.message,
                request_id=rid,
                details=exc.details,
                retryable=exc.retryable,
            ),
            rid,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exc(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        rid = request_id_of(request)
        code = "not_found" if exc.status_code == 404 else "http_error"
        message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return _json(
            exc.status_code,
            error_envelope(code=code, message=message, request_id=rid),
            rid,
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        rid = request_id_of(request)
        issues = [
            {
                "field": ".".join(str(p) for p in err.get("loc", ())),
                "issue": err.get("msg", "invalid"),
                "type": err.get("type", "value_error"),
            }
            for err in exc.errors()
        ]
        return _json(
            422,
            error_envelope(
                code="validation_failed",
                message="One or more fields failed validation.",
                request_id=rid,
                details={"issues": issues},
            ),
            rid,
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        rid = request_id_of(request)
        log.error("unhandled_exception", error=str(exc), path=request.url.path, exc_info=exc)
        return _json(
            500,
            error_envelope(
                code="internal_error",
                message="An unexpected error occurred.",
                request_id=rid,
                retryable=True,
            ),
            rid,
        )
