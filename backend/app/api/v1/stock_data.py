"""``/v1/stocks/{...}/data`` — a security's intraday candle series.

Written to the handler shape ``app/api/v1/auth.py`` established (``CLAUDE.md`` §3): accept a
validated request, call **one** service method, return a schema. No ``try``, no ``if``, no
session, no ``HTTPException``. Why the routes behave as they do is documented in
``app/services/stock_data.py``.

**The URL is nested under the stock, not a sibling of it.** The backlog sketched
``GET /v1/stock-data?ticker=NVDA``, which is the old router's shape. It is not the shape this
API has: ANV-13 made ``/v1/stocks`` the securities resource, and a candle series is not a
top-level collection anybody browses — it is *this stock's* prices, meaningless without the
stock, cascade-deleted with it (``stock_data.stock_id`` is ``ON DELETE CASCADE``), and never
queried across securities. So it is a sub-collection::

    GET /v1/stocks/{stock_id}/data
    GET /v1/stocks/by-ticker/{ticker}/data

Three things that buys over a query parameter. The parent is in the path, so "which stock"
cannot be omitted — the old endpoint's ``search=""`` default silently meant *every* stock's
candles interleaved, which is not a series anybody can plot. A missing parent is a 404 from
the URL itself rather than an empty list. And the two ways of naming a security are the two
ANV-13 already established, spelled the same way, instead of a third convention.

The layering is unchanged by the URL choice: service, dependency and router are separate
modules (``CLAUDE.md`` §3), and this router owns its own ``prefix`` and ``tags`` even though
the prefix matches ``app/api/v1/stocks.py``'s. Included after it in ``app/api/v1/__init__.py``
so the securities routes are declared first — see :class:`TestRouteOrdering` in
``tests/api/test_stock_data.py`` for what that does and does not decide.

**Read-only, and authenticated.** There is no ``POST`` here: candles arrive from a vendor,
and ANV-22's ingest will write them through the service rather than over HTTP. Both routes
take ``user: CurrentUser``.

The ticker path parameter is a **plain string**: normalisation is the service's, not the
edge's, for the reason ``app/services/stock.py`` sets out.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from app.deps.auth import CurrentUser
from app.deps.stock_data import StockDataServiceDep
from app.schemas.errors import ErrorResponse
from app.schemas.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, Page
from app.schemas.stock_data import StockDataPoint

router = APIRouter(prefix="/stocks", tags=["stock data"])

#: Documented on both routes: they are guarded, so a 401 here is ordinary traffic and the
#: client needs to know which ``code`` to branch on — ``token_expired`` means refresh, the
#: rest mean sign in again.
UNAUTHORIZED_RESPONSE = {
    status.HTTP_401_UNAUTHORIZED: {
        "model": ErrorResponse,
        "description": (
            "`unauthorized` (no credentials, or a deleted account), `invalid_token`, "
            "`token_expired`, or `wrong_token_type`."
        ),
    }
}

#: A 404 here is always about the **security**, never about the candles: a stock that exists
#: and simply has nothing in the requested range answers 200 with an empty page.
NOT_FOUND_RESPONSE = {
    status.HTTP_404_NOT_FOUND: {
        "model": ErrorResponse,
        "description": "`not_found` — no such security. An empty range is a 200, not a 404.",
    }
}

#: `validation_error` from the service (an inverted date range), distinct from FastAPI's own
#: 422 for an unparseable date or an out-of-bounds `limit`. Both use the same envelope.
#: Spelled as the plain integer `app/main.py` uses: Starlette has deprecated
#: `HTTP_422_UNPROCESSABLE_ENTITY` in favour of a name older versions do not have, and a
#: literal cannot go stale in either direction.
INVALID_RANGE_RESPONSE = {
    422: {
        "model": ErrorResponse,
        "description": "`validation_error` — `start` falls after `end`, or a bound is unparseable.",
    }
}

CANDLE_RESPONSES = {
    **UNAUTHORIZED_RESPONSE,
    **NOT_FOUND_RESPONSE,
    **INVALID_RANGE_RESPONSE,
}

StartQuery = Annotated[
    dt.date | None,
    Query(
        description=(
            "Earliest trading date to include, **inclusive**. Omit for the whole series "
            "back to its beginning."
        ),
        examples=["2026-01-05"],
    ),
]

EndQuery = Annotated[
    dt.date | None,
    Query(
        description=(
            "Latest trading date to include, **inclusive**. Omit for everything up to the "
            "most recent candle. Equal to `start` means that single trading day."
        ),
        examples=["2026-01-09"],
    ),
]

LimitQuery = Annotated[
    int,
    Query(
        ge=1,
        le=MAX_PAGE_LIMIT,
        description="Window size. Above the ceiling is a 422, never a silent clamp.",
    ),
]

OffsetQuery = Annotated[int, Query(ge=0, description="Candles to skip.")]


# Declared before `/{stock_id}/data`, per `CLAUDE.md` §4. The two routes differ in segment
# count so neither can shadow the other; the convention is kept so nobody has to re-derive
# that, and `tests/api/test_stock_data.py` proves it rather than asserting a comment.
@router.get(
    "/by-ticker/{ticker}/data",
    response_model=Page[StockDataPoint],
    summary="A security's candles, by ticker",
    responses=CANDLE_RESPONSES,
)
async def list_stock_data_by_ticker(
    ticker: Annotated[
        str,
        Path(description="Ticker symbol, in any casing — it is normalised server-side."),
    ],
    user: CurrentUser,
    service: StockDataServiceDep,
    start: StartQuery = None,
    end: EndQuery = None,
    limit: LimitQuery = DEFAULT_PAGE_LIMIT,
    offset: OffsetQuery = 0,
) -> Page[StockDataPoint]:
    """One chronological window of a security's candles, resolved by ticker.

    Each point carries a single `datetime` recombined from the stored `date` and `time`
    columns. It is **naive** — the exchange's local trading clock, which carries no zone —
    and it is the one timestamp in this API without an offset.
    """
    return await service.list_for_ticker(
        ticker=ticker, start=start, end=end, limit=limit, offset=offset
    )


@router.get(
    "/{stock_id}/data",
    response_model=Page[StockDataPoint],
    summary="A security's candles",
    responses=CANDLE_RESPONSES,
)
async def list_stock_data(
    stock_id: Annotated[uuid.UUID, Path(description="The security whose series to read.")],
    user: CurrentUser,
    service: StockDataServiceDep,
    start: StartQuery = None,
    end: EndQuery = None,
    limit: LimitQuery = DEFAULT_PAGE_LIMIT,
    offset: OffsetQuery = 0,
) -> Page[StockDataPoint]:
    """One chronological window of a security's candles, oldest first.

    `total` counts every candle in the date range regardless of the window, so an `offset`
    past the end is an empty page with a truthful total. Prices are serialised as **quoted
    JSON strings** — they are `Decimal`, and a JSON number would lose the fourth decimal
    place.
    """
    return await service.list_for_stock(
        stock_id=stock_id, start=start, end=end, limit=limit, offset=offset
    )


__all__ = ["router"]
