"""Cross-cutting request concerns (``CLAUDE.md`` §3).

Nothing here is resource-specific: correlation ids, access logging, the error contract and
CORS apply to every request. ``create_app()`` wires the whole package through the two
``install_*`` functions below, so a new cross-cutting concern is added in one place.

**Middleware order matters.** Starlette treats the *last* registered middleware as the
outermost, so ``install_middleware`` produces::

    RequestID -> AccessLog -> CORS -> exception handlers -> router

The request id is outermost so it exists before anything else logs or responds — including
a CORS preflight, which short-circuits without ever reaching the router.
"""

from app.middleware.errors import (
    ERROR_STATUS_CODES,
    error_response,
    install_exception_handlers,
    status_for,
)
from app.middleware.logging import AccessLogMiddleware, configure_logging
from app.middleware.request_id import REQUEST_ID_HEADER, RequestIDMiddleware, get_request_id
from app.middleware.setup import install_middleware

__all__ = [
    "ERROR_STATUS_CODES",
    "REQUEST_ID_HEADER",
    "AccessLogMiddleware",
    "RequestIDMiddleware",
    "configure_logging",
    "error_response",
    "get_request_id",
    "install_exception_handlers",
    "install_middleware",
    "status_for",
]
