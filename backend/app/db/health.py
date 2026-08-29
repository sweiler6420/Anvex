"""Liveness check for the database connection.

Lives in ``app/db/`` rather than ``app/repos/`` on purpose: it asks nothing about Anvex's
data, only whether the pool can hand out a working connection. A repo is about an
aggregate; this is plumbing checking itself.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: Cheapest statement that proves a round trip actually happened.
PING_STATEMENT = text("SELECT 1")


async def ping(session: AsyncSession) -> None:
    """Execute ``SELECT 1``, raising whatever the driver raises if it cannot.

    Deliberately not swallowing the error: the caller (``/health/ready``) decides what a
    failure means, and the original exception is what the log needs.
    """
    await session.execute(PING_STATEMENT)


__all__ = ["PING_STATEMENT", "ping"]
