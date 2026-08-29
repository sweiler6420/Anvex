"""structlog configuration and the access log.

``CLAUDE.md`` §4: logging is structured and request-id tagged, and there is no bare
``print`` anywhere. :func:`configure_logging` is the one place that decides how a log line
is rendered, and it owns the **stdlib root logger** too, so uvicorn, SQLAlchemy and any
library that logs through ``logging`` come out in the same format instead of two
competing ones interleaved on stdout.

Rendering depends on the environment: a human-readable console renderer for
``ANVEX_ENV=local``, JSON everywhere else, because that is what a log shipper ingests.
The threshold comes from ``settings.log_level``.
"""

from __future__ import annotations

import logging
import sys
import time
from typing import Any

import structlog
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.middleware.request_id import get_request_id
from app.settings import Settings, get_settings

logger = structlog.get_logger("anvex.access")

#: Paths whose access log is suppressed. Container/ALB health probes fire every few
#: seconds and would otherwise be ~99% of the log volume in a quiet environment.
DEFAULT_EXCLUDED_PATHS: frozenset[str] = frozenset({"/health", "/health/ready"})


def _log_level(value: str) -> int:
    """Translate ``LOG_LEVEL`` to a stdlib level, defaulting to INFO if it is nonsense."""
    return logging.getLevelNamesMapping().get(value.strip().upper(), logging.INFO)


def configure_logging(settings: Settings | None = None) -> None:
    """Point structlog and the stdlib root logger at one formatter on stdout.

    Idempotent — ``create_app()`` calls it, and so may a Celery worker or a script.
    """
    settings = settings or get_settings()
    level = _log_level(settings.log_level)

    shared_processors: list[Any] = [
        # Pulls in whatever `RequestIDMiddleware` bound, so `request_id` appears on every
        # line without a single call site threading it through.
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]
    renderer: Any = (
        structlog.dev.ConsoleRenderer(colors=False)
        if settings.anvex_env == "local"
        else structlog.processors.JSONRenderer()
    )

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        # Deliberately off: a cached logger keeps the configuration it was first bound
        # with, which makes reconfiguration (tests, a worker re-init) silently ineffective.
        cache_logger_on_first_use=False,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            # Applied to records from plain `logging` callers so they gain the same keys.
            foreign_pre_chain=shared_processors,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.format_exc_info,
                renderer,
            ],
        )
    )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    # uvicorn installs its own handlers at import; leaving them attached double-prints
    # every line. Strip them and let the records propagate to the root handler above.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        library_logger = logging.getLogger(name)
        library_logger.handlers = []
        library_logger.propagate = True


class AccessLogMiddleware:
    """Emit one structured line per request, with its duration.

    Sits *inside* :class:`~app.middleware.request_id.RequestIDMiddleware` so the id is
    already bound, and *outside* the router so the timing covers dependency resolution.
    An exception passing through is logged with its traceback and re-raised — turning it
    into a response is the exception handler's job, not the logger's.
    """

    def __init__(self, app: ASGIApp, excluded_paths: frozenset[str] | None = None) -> None:
        self.app = app
        self.excluded_paths = (
            DEFAULT_EXCLUDED_PATHS if excluded_paths is None else frozenset(excluded_paths)
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in self.excluded_paths:
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        context = self._context(scope)
        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            # `exc_info=True` gets the traceback into the server log even though the
            # client will only ever see the opaque 500 body.
            logger.exception(
                "request.failed",
                duration_ms=self._elapsed_ms(started),
                status_code=500,
                **context,
            )
            raise

        logger.info(
            "request.completed",
            duration_ms=self._elapsed_ms(started),
            status_code=status_code,
            **context,
        )

    @staticmethod
    def _elapsed_ms(started: float) -> float:
        return round((time.perf_counter() - started) * 1000, 2)

    @staticmethod
    def _context(scope: Scope) -> dict[str, Any]:
        client = scope.get("client")
        query = scope.get("query_string", b"").decode("latin-1")
        return {
            "method": scope.get("method"),
            "path": scope.get("path"),
            # Kept separate from `path` so log search groups routes rather than fanning
            # out over every distinct query string.
            "query": query or None,
            "client_ip": client[0] if client else None,
            "user_agent": Headers(scope=scope).get("user-agent"),
            "request_id": get_request_id(scope),
        }


__all__ = ["DEFAULT_EXCLUDED_PATHS", "AccessLogMiddleware", "configure_logging", "logger"]
