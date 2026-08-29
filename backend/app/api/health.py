"""Liveness and readiness probes.

Unversioned on purpose: ``/health`` is consumed by Docker, compose and (later) an ALB
target group, none of which should have to follow an API version bump.

The split is the standard one and the distinction matters operationally:

``/health``   liveness — "is this process alive?" No I/O at all, so a database outage
              never causes the orchestrator to kill and restart otherwise-healthy
              containers in a loop.
``/health/ready``  readiness — "should traffic be routed here?" Does a real ``SELECT 1``,
              and answers 503 when it cannot, so a container with a broken pool is pulled
              out of the load balancer instead of failing every request it receives.
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.health import ping
from app.deps import get_session
from app.schemas.errors import ErrorResponse
from app.schemas.health import HealthOut, ReadinessOut

logger = structlog.get_logger("anvex.health")

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthOut, summary="Liveness probe")
async def health() -> HealthOut:
    """Return 200 as long as the process can serve a request. Touches nothing."""
    return HealthOut()


@router.get(
    "/health/ready",
    response_model=ReadinessOut,
    summary="Readiness probe",
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ErrorResponse,
            "description": "A dependency is unreachable; do not route traffic here.",
        }
    },
)
async def readiness(session: Annotated[AsyncSession, Depends(get_session)]) -> ReadinessOut:
    """Verify the database answers, or fail with 503.

    Constructing the session opens no socket, so the round trip in :func:`ping` is what is
    actually being tested. ``pool_pre_ping`` already discards dead pooled connections, so
    an exception here is a genuine outage rather than a stale handle.
    """
    try:
        await ping(session)
    except Exception as exc:
        logger.warning("health.database_unavailable", error_type=type(exc).__name__)
        # An HTTPException is the one exception an API handler may raise (``CLAUDE.md``
        # §3); the middleware renders it into the standard error body.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The database is unavailable.",
        ) from exc
    return ReadinessOut()


__all__ = ["router"]
