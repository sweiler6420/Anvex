"""The FastAPI application factory.

``create_app()`` exists rather than a module-level ``app = FastAPI()`` so a test can build
an isolated instance with its own :class:`~app.settings.Settings` (different CORS origins,
a different log level) instead of monkeypatching a global. ``app = create_app()`` at the
bottom is what ``uvicorn app.main:app`` and the compose healthcheck import.

Assembly order is deliberate:

1. configure logging **first**, so anything the rest of startup logs is already structured;
2. build the app with its lifespan;
3. install middleware, then exception handlers;
4. mount routers.

There is no ``Base.metadata.create_all`` anywhere. Schema comes from Alembic only
(``CLAUDE.md`` §4) — an app that creates its own tables silently diverges from the
migration history and the divergence is only discovered in production.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app.api import health_router, v1_router
from app.db.engine import dispose_engine
from app.middleware import (
    configure_logging,
    install_exception_handlers,
    install_middleware,
)
from app.schemas.errors import ErrorResponse
from app.settings import Settings, get_settings

logger = structlog.get_logger("anvex.app")

API_TITLE = "Anvex API"
API_VERSION = "0.1.0"
API_DESCRIPTION = (
    "Investment research API. Every non-2xx response uses the shared error envelope: "
    '`{"error": {"code", "message", "details", "request_id"}}`.'
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown hook.

    Startup does nothing on purpose: the engine is lazy and opens no socket at import, so
    there is nothing to warm and no reason for the process to fail to boot just because
    Postgres is a few seconds behind it in the compose graph.

    Shutdown disposes the engine so pooled connections are closed politely rather than
    left for Postgres to time out.
    """
    logger.info("app.startup", env=get_settings().anvex_env, version=API_VERSION)
    try:
        yield
    finally:
        await dispose_engine()
        logger.info("app.shutdown")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a fully wired application instance."""
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        description=API_DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        # Documents the shared envelope on every operation, so the generated client has
        # one error type rather than FastAPI's default `{"detail": ...}`.
        responses={
            400: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
        },
    )

    install_middleware(app, settings)
    install_exception_handlers(app)

    app.include_router(health_router)
    app.include_router(v1_router)

    return app


app = create_app()

__all__ = ["API_TITLE", "API_VERSION", "app", "create_app", "lifespan"]
