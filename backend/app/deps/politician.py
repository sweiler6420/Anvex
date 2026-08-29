"""The ``/v1/politicians`` service factory — wiring, and nothing else.

One seam per resource (``CLAUDE.md`` §3): :func:`get_politician_service` resolves a session
and a :class:`~app.settings.Settings` out of the dependency graph, constructs the service and
returns it. Nothing is decided here, which is exactly what makes it the single dependency a
route contract test overrides to swap the whole service for one sitting on an in-memory repo.

There is deliberately **no** ``get_politician`` resolver dependency and no seed dependency.
Turning a missing row into a :class:`~app.domain.errors.NotFoundError` is a rule, and rules
live in :mod:`app.services.politician` where the seed script reaches them too; the seed is
not an HTTP endpoint at all, so it has nothing to be a dependency of.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps.session import get_session
from app.deps.settings import Settings, get_settings_dep
from app.services.politician import PoliticianService


def get_politician_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> PoliticianService:
    """Build a :class:`~app.services.politician.PoliticianService` for this request.

    The repo is left to its keyword default — repos are stateless singletons, so the only
    thing that genuinely varies per request is the session.
    """
    return PoliticianService(session, settings)


#: The annotation a ``/v1/politicians`` handler uses, so a route signature stays one
#: parameter.
PoliticianServiceDep = Annotated[PoliticianService, Depends(get_politician_service)]

__all__ = ["PoliticianServiceDep", "get_politician_service"]
