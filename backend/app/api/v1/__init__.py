"""Version 1 of the HTTP API.

The single aggregation point for every ``/v1`` router. The ``prefix`` lives here and
nowhere else (``CLAUDE.md`` §4) — a path decorator must never spell out ``/v1``.

Adding a resource from ANV-11 onward is two lines::

    from app.api.v1 import auth
    router.include_router(auth.router)

with the resource module owning its own ``prefix="/auth"`` and ``tags``.
"""

from fastapi import APIRouter

from app.api.v1 import auth, politicians, stock_data, stocks, users, watchlists

router = APIRouter(prefix="/v1")

# Resource routers are included here as they land, in the order they should read in the
# generated docs. Each module owns its own `prefix` and `tags`.
router.include_router(auth.router)
router.include_router(users.router)
router.include_router(stocks.router)
# Included after `stocks` so the securities routes are declared first: they are the parent
# resource, and `/stocks/by-ticker/{ticker}` therefore wins the one URL both routers could
# claim (`/v1/stocks/by-ticker/data`, a security whose ticker is literally "data").
router.include_router(stock_data.router)
# Its own prefix (`/watchlists`), so it competes with nothing above it.
router.include_router(watchlists.router)
# Likewise `/politicians` — reference data, read-only, filled by the seed script.
router.include_router(politicians.router)

__all__ = ["router"]
