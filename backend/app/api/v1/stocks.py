"""``/v1/stocks`` — the securities reference table: list, search, resolve.

Written to the handler shape ``app/api/v1/auth.py`` established (``CLAUDE.md`` §3): accept
a validated request, call **one** service method, return a schema. No ``try``, no ``if``, no
session, no ``HTTPException``. Why the routes behave as they do is documented in
``app/services/stock.py``.

**Read-only, and authenticated.** There is no ``POST``/``PATCH``/``DELETE`` here: nothing in
the product creates a security by hand, and ANV-22's ingest will do it through the service
rather than over HTTP. Every route takes ``user: CurrentUser`` — the router this replaces
required a token on its one route, and reference data is no reason to drop the requirement.

**Route ordering.** ``/by-ticker/{ticker}`` is declared before ``/{stock_id}``, per
``CLAUDE.md`` §4. Unlike ``/users/me``, it is not *strictly* required here — the literal
route has two path segments and Starlette's default converter never matches a ``/``, so
``/{stock_id}`` cannot swallow ``/by-ticker/AAPL`` whichever way round they are declared
(``tests/api/test_stocks.py`` proves both halves of that). It is still declared first
because the convention is what keeps the next person from having to re-derive that
reasoning, and because the one-segment ``/v1/stocks/by-ticker`` **is** matched by
``/{stock_id}`` and answers 422.

The ticker path parameter is a **plain string**: normalisation is the service's, not the
edge's (see ``app/services/stock.py``). ANV-8's annotated ``Ticker`` type would in fact
apply its ``BeforeValidator`` to a path parameter — that was checked rather than assumed —
but using it here would put the rule in the one layer a Celery task does not go through,
and split it across two.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from app.deps.auth import CurrentUser
from app.deps.stock import StockServiceDep
from app.schemas.errors import ErrorResponse
from app.schemas.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, Page
from app.schemas.stock import StockOut

router = APIRouter(prefix="/stocks", tags=["stocks"])

#: Documented on every route: all three are guarded, so a 401 here is ordinary traffic and
#: the client needs to know which ``code`` to branch on — ``token_expired`` means refresh,
#: the rest mean sign in again.
UNAUTHORIZED_RESPONSE = {
    status.HTTP_401_UNAUTHORIZED: {
        "model": ErrorResponse,
        "description": (
            "`unauthorized` (no credentials, or a deleted account), `invalid_token`, "
            "`token_expired`, or `wrong_token_type`."
        ),
    }
}

NOT_FOUND_RESPONSE = {
    status.HTTP_404_NOT_FOUND: {
        "model": ErrorResponse,
        "description": "`not_found` — no such security.",
    }
}


@router.get(
    "",
    response_model=Page[StockOut],
    summary="List securities",
    responses=UNAUTHORIZED_RESPONSE,
)
async def list_stocks(
    user: CurrentUser,
    service: StockServiceDep,
    search: Annotated[
        str | None,
        Query(
            description=(
                "Case-insensitive substring match against the ticker **or** the company "
                "name. Blank means no filter."
            ),
            examples=["nvid"],
        ),
    ] = None,
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=MAX_PAGE_LIMIT,
            description="Window size. Above the ceiling is a 422, never a silent clamp.",
        ),
    ] = DEFAULT_PAGE_LIMIT,
    offset: Annotated[int, Query(ge=0, description="Rows to skip.")] = 0,
) -> Page[StockOut]:
    """One window of the securities list, ordered by ticker.

    ``total`` counts every match regardless of the window, so an ``offset`` past the end is
    an empty page with a truthful total rather than an implied end of the collection.
    """
    return await service.list_stocks(search=search, limit=limit, offset=offset)


# Declared before `/{stock_id}` — see the module docstring for what that does and does not
# buy here.
@router.get(
    "/by-ticker/{ticker}",
    response_model=StockOut,
    summary="A security by ticker",
    responses={**UNAUTHORIZED_RESPONSE, **NOT_FOUND_RESPONSE},
)
async def read_stock_by_ticker(
    ticker: Annotated[
        str,
        Path(description="Ticker symbol, in any casing — it is normalised server-side."),
    ],
    user: CurrentUser,
    service: StockServiceDep,
) -> StockOut:
    """Resolve a ticker to a security. ``aapl`` and ``AAPL`` name the same row."""
    return await service.get_stock_by_ticker(ticker=ticker)


@router.get(
    "/{stock_id}",
    response_model=StockOut,
    summary="A security by id",
    responses={**UNAUTHORIZED_RESPONSE, **NOT_FOUND_RESPONSE},
)
async def read_stock(
    stock_id: Annotated[uuid.UUID, Path(description="The security to read.")],
    user: CurrentUser,
    service: StockServiceDep,
) -> StockOut:
    """Resolve a security id. Unlike an account, a security belongs to nobody."""
    return await service.get_stock(stock_id=stock_id)


__all__ = ["router"]
