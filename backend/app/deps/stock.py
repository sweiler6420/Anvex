"""The ``/v1/stocks`` service factory — wiring, and nothing else.

One seam per resource (``CLAUDE.md`` §3): :func:`get_stock_service` resolves a session and
a :class:`~app.settings.Settings` out of the dependency graph, constructs the service and
returns it. Nothing is decided here, which is exactly what makes it the single dependency a
route contract test overrides to swap the whole service for one sitting on an in-memory
repo.

There is deliberately **no** ``get_stock_by_id`` / ``get_stock_by_ticker`` resolver
dependency. Normalising a ticker and turning a missing row into a
:class:`~app.domain.errors.NotFoundError` are rules, and rules live in
:mod:`app.services.stock` where a Celery task can reach them too. A dependency that did the
lookup would also mean two seams per resource, and a route test would have to know which
one to override.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps.session import get_session
from app.deps.settings import Settings, get_settings_dep
from app.services.stock import StockService


def get_stock_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> StockService:
    """Build a :class:`~app.services.stock.StockService` for this request.

    The repo is left to its keyword default — repos are stateless singletons, so the only
    thing that genuinely varies per request is the session.
    """
    return StockService(session, settings)


#: The annotation a ``/v1/stocks`` handler uses, so a route signature stays one parameter.
StockServiceDep = Annotated[StockService, Depends(get_stock_service)]

__all__ = ["StockServiceDep", "get_stock_service"]
