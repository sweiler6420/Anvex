"""Domain error -> HTTP response mapping.

This module is the *only* translation point between the pure exceptions in
``app/domain/errors.py`` and HTTP. Keeping the status codes here rather than on the
exception classes is what lets ``app/domain/`` stay free of web concerns (``CLAUDE.md``
§3) while still giving the API a single, predictable contract.

Four handlers are registered, and **all four emit the same body** —
:class:`app.schemas.errors.ErrorResponse`::

    {"error": {"code": ..., "message": ..., "details": {...}, "request_id": ...}}

so a client has exactly one error parser regardless of whether the failure came from a
service, a pydantic body, a 404 on an unknown route, or a crash.

The unhandled-exception handler is the security-relevant one: the client gets a fixed
``internal_error`` message with an empty ``details``, and the traceback goes to the log
tagged with the same request id the client was handed.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request

from app.domain.errors import (
    AnvexError,
    ConflictError,
    ExternalServiceError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)
from app.middleware.request_id import REQUEST_ID_HEADER, get_request_id
from app.schemas.errors import ErrorBody, ErrorResponse

logger = structlog.get_logger("anvex.errors")

#: The contract. Lookup walks the MRO, so a future subclass of ``NotFoundError`` inherits
#: 404 for free and an error missing from this table degrades to the base 500 rather than
#: crashing the handler.
ERROR_STATUS_CODES: dict[type[AnvexError], int] = {
    AnvexError: 500,
    ValidationError: 422,
    UnauthorizedError: 401,
    ForbiddenError: 403,
    NotFoundError: 404,
    ConflictError: 409,
    # 502, not 503: *we* are up, the thing behind us is not.
    ExternalServiceError: 502,
}

#: Slugs for responses Starlette raises on our behalf (unknown route, bad method, and the
#: ``HTTPException``s handlers are allowed to raise). Anything unlisted becomes
#: ``http_error`` so the field is never empty.
_HTTP_STATUS_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    503: "service_unavailable",
}

#: What the client sees when something we did not anticipate escapes. Fixed text: an
#: exception message can contain a DSN, a query, or a token.
INTERNAL_ERROR_MESSAGE = "An unexpected error occurred."


def status_for(error: AnvexError | type[AnvexError]) -> int:
    """Return the HTTP status for a domain error class or instance."""
    error_type = error if isinstance(error, type) else type(error)
    for candidate in error_type.__mro__:
        if candidate in ERROR_STATUS_CODES:
            return ERROR_STATUS_CODES[candidate]
    return ERROR_STATUS_CODES[AnvexError]


def error_response(
    request: Request | None,
    *,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Build the one and only error body shape."""
    request_id = get_request_id(request.scope) if request is not None else None
    body = ErrorResponse(
        error=ErrorBody(
            code=code,
            message=message,
            details=details or {},
            request_id=request_id,
        )
    )
    response_headers = dict(headers) if headers else {}
    if request_id is not None:
        # Set here rather than relying on `RequestIDMiddleware` alone: an unhandled
        # exception is caught by Starlette's `ServerErrorMiddleware`, which sits *outside*
        # our middleware and sends its response without passing through our send wrapper.
        # Without this the one response a client most needs to correlate is the one
        # response missing the header. The middleware de-duplicates, so this is safe.
        response_headers[REQUEST_ID_HEADER] = request_id
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json"),
        headers=response_headers,
    )


async def anvex_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Map a deliberately-raised domain error to its status code."""
    if not isinstance(exc, AnvexError):  # pragma: no cover - registered for this type only
        raise exc
    status_code = status_for(exc)
    # 5xx means *we* failed and someone should look; 4xx is the caller's problem and is
    # noise at error level.
    log = logger.error if status_code >= 500 else logger.warning
    log(
        "request.domain_error",
        error_code=exc.code,
        error_type=type(exc).__name__,
        status_code=status_code,
        detail=exc.message,
        **({"error_details": exc.details} if exc.details else {}),
    )
    return error_response(
        request,
        status_code=status_code,
        code=exc.code,
        message=exc.message,
        details=exc.details,
    )


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Re-shape Starlette/FastAPI ``HTTPException`` into the Anvex body.

    Without this, a 404 on an unknown route answers ``{"detail": "Not Found"}`` and the
    client needs a second parser for exactly the responses it is least likely to test.
    ``exc.headers`` is preserved — dropping it would strip ``WWW-Authenticate`` off a 401.
    """
    if not isinstance(exc, StarletteHTTPException):  # pragma: no cover
        raise exc
    code = _HTTP_STATUS_CODES.get(exc.status_code, "http_error")
    message = exc.detail if isinstance(exc.detail, str) and exc.detail else INTERNAL_ERROR_MESSAGE
    return error_response(
        request,
        status_code=exc.status_code,
        code=code,
        message=message,
        headers=exc.headers,
    )


async def validation_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Wrap FastAPI request-validation failures in the same envelope.

    Pydantic's per-field list is genuinely useful to a client, so it is preserved under
    ``details.errors`` rather than flattened away. ``ctx`` is dropped: it can hold the
    original exception object and, for a constrained field, the value that failed.
    """
    if not isinstance(exc, RequestValidationError):  # pragma: no cover
        raise exc
    errors = [
        {key: value for key, value in error.items() if key != "ctx"} for error in exc.errors()
    ]
    logger.info("request.validation_error", error_count=len(errors))
    return error_response(
        request,
        status_code=422,
        code="validation_error",
        message="Request validation failed.",
        details={"errors": jsonable_encoder(errors)},
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last resort: log the traceback, return an opaque 500.

    The response deliberately carries no exception type, message or stack — only the
    request id, which is the handle support needs to find this exact log line.
    """
    logger.exception(
        "request.unhandled_error",
        error_type=type(exc).__name__,
        status_code=500,
        path=request.url.path,
        method=request.method,
    )
    return error_response(
        request,
        status_code=500,
        code="internal_error",
        message=INTERNAL_ERROR_MESSAGE,
    )


def install_exception_handlers(app: FastAPI) -> None:
    """Register every handler on ``app``. Called by ``create_app()``."""
    app.add_exception_handler(AnvexError, anvex_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    # Handled by Starlette's ServerErrorMiddleware: it sends this response and then
    # re-raises so the ASGI server still records the crash.
    app.add_exception_handler(Exception, unhandled_exception_handler)


__all__ = [
    "ERROR_STATUS_CODES",
    "INTERNAL_ERROR_MESSAGE",
    "anvex_error_handler",
    "error_response",
    "http_exception_handler",
    "install_exception_handlers",
    "status_for",
    "unhandled_exception_handler",
    "validation_exception_handler",
]
