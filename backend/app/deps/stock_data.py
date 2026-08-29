"""The candle-series service factory — wiring, and nothing else.

One seam per resource (``CLAUDE.md`` §3), exactly as ``app/deps/stock.py`` is for
``/v1/stocks``: :func:`get_stock_data_service` resolves a session and a
:class:`~app.settings.Settings` out of the dependency graph, constructs the service and
returns it. Nothing is decided here, which is what makes it the single dependency a route
contract test overrides to swap the whole service for one sitting on in-memory repos.

The routes live under the ``/v1/stocks`` prefix, but the service keeps its **own** factory
rather than sharing ``get_stock_service``. Two reasons: this service is constructed from two
repos rather than one, and an API test that wanted to stub the candle series would otherwise
have to replace the stock endpoints too. One seam per service is the rule; sharing a URL
prefix does not make two services one.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps.session import get_session
from app.deps.settings import Settings, get_settings_dep
from app.services.stock_data import StockDataService


def get_stock_data_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> StockDataService:
    """Build a :class:`~app.services.stock_data.StockDataService` for this request.

    Both repos are left to their keyword defaults — repos are stateless singletons, so the
    only thing that genuinely varies per request is the session.
    """
    return StockDataService(session, settings)


#: The annotation a candle-series handler uses, so a route signature stays one parameter.
StockDataServiceDep = Annotated[StockDataService, Depends(get_stock_data_service)]

__all__ = ["StockDataServiceDep", "get_stock_data_service"]
