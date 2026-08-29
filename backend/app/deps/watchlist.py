"""The watchlist service factory — wiring, and nothing else.

One seam per resource (``CLAUDE.md`` §3), exactly as ``app/deps/stock.py`` is for
``/v1/stocks``: :func:`get_watchlist_service` resolves a session and a
:class:`~app.settings.Settings` out of the dependency graph, constructs the service and
returns it. Nothing is decided here, which is what makes it the single dependency an API
contract test overrides to swap the whole service for one sitting on in-memory repos.

**In particular, the ownership check is not here.** It would fit — a dependency could
resolve ``{watchlist_id}`` against ``CurrentUser`` and hand the handler a watchlist it is
already allowed to see. It lives in :meth:`~app.services.watchlist.WatchlistService.
_resolve_owned` instead, for the reason ``CLAUDE.md`` §3 gives about ``get_current_user``:
logic that looks like a dependency belongs in the service, so that a Celery task, a script
or a future WebSocket handler goes through the same gate. A rule enforced only by the HTTP
edge is a rule with one caller and no guarantee.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps.session import get_session
from app.deps.settings import Settings, get_settings_dep
from app.services.watchlist import WatchlistService


def get_watchlist_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> WatchlistService:
    """Build a :class:`~app.services.watchlist.WatchlistService` for this request.

    Both repos are left to their keyword defaults — repos are stateless singletons, so the
    only thing that genuinely varies per request is the session.
    """
    return WatchlistService(session, settings)


#: The annotation a watchlist handler uses, so a route signature stays one parameter.
WatchlistServiceDep = Annotated[WatchlistService, Depends(get_watchlist_service)]

__all__ = ["WatchlistServiceDep", "get_watchlist_service"]
