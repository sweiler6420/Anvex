"""Async session factory and lifecycle.

The transaction boundary belongs to the *service* layer (``CLAUDE.md`` §3): this module
hands out sessions and guarantees they are closed. It never commits.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db.engine import get_engine

_sessionmaker: async_sessionmaker[AsyncSession] | None = None
_bound_engine: AsyncEngine | None = None


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the process-wide session factory, rebuilding it if the engine changed.

    ``expire_on_commit=False`` keeps ORM objects usable after a commit, which matters
    because a service commits and then hands models to a response schema.
    """
    global _sessionmaker, _bound_engine
    engine = get_engine()
    if _sessionmaker is None or _bound_engine is not engine:
        _bound_engine = engine
        _sessionmaker = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _sessionmaker


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield an :class:`AsyncSession`, rolling back on error and always closing.

    Used directly by Celery tasks and scripts; ANV-4's ``app/deps`` wraps it for FastAPI.
    """
    session = get_sessionmaker()()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
