"""The request-scoped database session dependency."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session as db_session


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield an :class:`AsyncSession` for the duration of one request.

    A thin wrapper over ``app.db.session.get_session`` and nothing more — the rollback and
    close semantics live there, and there is deliberately **no second sessionmaker**, so a
    Celery task and an API handler share one engine and one pool.

    Note that it does not commit: the transaction boundary belongs to the service
    (``CLAUDE.md`` §3).
    """
    async with db_session() as session:
        yield session


__all__ = ["get_session"]
