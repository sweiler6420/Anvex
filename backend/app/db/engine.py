"""The application's async SQLAlchemy engine.

One engine per process, created lazily so that importing ``app.db`` never opens a socket
and tests can override settings before the first use. The FastAPI lifespan (ANV-4) calls
:func:`dispose_engine` on shutdown.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.settings import Settings, get_settings

#: Persistent connections kept per process. The API runs a handful of uvicorn workers and
#: Postgres defaults to 100 connections, so this leaves plenty of headroom for the worker,
#: beat and interactive psql sessions.
POOL_SIZE = 10
#: Extra short-lived connections allowed above ``POOL_SIZE`` during a burst.
MAX_OVERFLOW = 5
#: Seconds a request waits for a free connection before erroring rather than hanging.
POOL_TIMEOUT_SECONDS = 30
#: Recycle below the typical 1h idle timeout of managed Postgres/proxies (RDS, pgbouncer).
POOL_RECYCLE_SECONDS = 1800

_engine: AsyncEngine | None = None


def create_engine(settings: Settings | None = None) -> AsyncEngine:
    """Build a new async engine from ``settings`` (defaults to the cached settings).

    Creating an engine performs no I/O; the first connection is opened on demand.
    """
    settings = settings or get_settings()
    return create_async_engine(
        settings.postgres_dsn,
        # Cheap liveness check on checkout — without it, a connection killed by a restart
        # or an idle timeout surfaces as a random 500 on the next request.
        pool_pre_ping=True,
        pool_size=POOL_SIZE,
        max_overflow=MAX_OVERFLOW,
        pool_timeout=POOL_TIMEOUT_SECONDS,
        pool_recycle=POOL_RECYCLE_SECONDS,
        echo=False,
    )


def get_engine() -> AsyncEngine:
    """Return the process-wide engine, creating it on first use."""
    global _engine
    if _engine is None:
        _engine = create_engine()
    return _engine


async def dispose_engine() -> None:
    """Close every pooled connection and forget the engine.

    Safe to call when no engine was ever created. The next :func:`get_engine` builds a
    fresh one, which is what makes this usable both from an app shutdown hook and from
    tests that repoint the settings.
    """
    global _engine
    if _engine is not None:
        engine, _engine = _engine, None
        await engine.dispose()
