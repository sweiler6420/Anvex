"""``/v1/watchlists`` — a user's ordered lists of stocks, and the stocks on them.

Written to the handler shape ``app/api/v1/auth.py`` established (``CLAUDE.md`` §3): accept a
validated request, call **one** service method, return a schema. No ``try``, no ``if``, no
session, no ``HTTPException`` — a service raises a domain error and
``app/middleware/errors.py`` renders the one envelope every non-2xx uses. Why the routes
behave as they do is documented in ``app/services/watchlist.py``; a handler is not the place
for it.

**Every route is authenticated and every route is scoped to the caller.** ``user:
CurrentUser`` is passed down to the service as ``owner``, and the service is where ownership
is decided — not here, and not in a repo query. A watchlist that belongs to somebody else
answers ``404``, byte-identical to one that does not exist (``CLAUDE.md`` §4).

**The URL shape**, and what it fixes relative to ``AverageInvestorApi/api/routers/
watchlist.py``::

    POST   /v1/watchlists                                 create
    GET    /v1/watchlists                                 list mine (paginated)
    GET    /v1/watchlists/{watchlist_id}                  read one, stocks in order
    DELETE /v1/watchlists/{watchlist_id}                  delete
    POST   /v1/watchlists/{watchlist_id}/stocks           add a stock
    DELETE /v1/watchlists/{watchlist_id}/stocks/{stock_id} remove a stock
    PATCH  /v1/watchlists/{watchlist_id}/stocks/{stock_id} move a stock

* The old router had **no "list my watchlists"** at all, so a client that lost a
  ``watchlist_id`` had no way to find it again.
* Its membership routes were ``POST``/``PUT`` on the single path ``/v1/watchlist/stock``
  with every identifier — ``watchlist_id``, ``stock_id``, ``current_index``,
  ``destination_index`` — in the **query string**. Here the two ids that identify the row
  are in the path, where they identify it, and the one mutable thing about a membership row
  travels in the body.
* Its reorder was a ``PUT`` returning ``201 Created`` for a move that created nothing. This
  is a ``PATCH`` returning ``200`` and the reordered list.
* Its ``GET`` answered ``204 No Content`` **with a body** for an empty watchlist, which is
  not a valid HTTP response. An empty watchlist is a ``200`` with ``entries: []``.
* There was **no delete** for a watchlist.

Route ordering is not load-bearing here — ``/{watchlist_id}`` and ``/{watchlist_id}/stocks``
differ in segment count, and ``CLAUDE.md`` §4's trap needs two routes competing for the
*same* segment — but the literal-first habit is kept anyway.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from app.deps.auth import CurrentUser
from app.deps.watchlist import WatchlistServiceDep
from app.schemas.errors import ErrorResponse
from app.schemas.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, Page
from app.schemas.watchlist import (
    WatchlistCreate,
    WatchlistDetailOut,
    WatchlistEntryCreate,
    WatchlistEntryOut,
    WatchlistEntryUpdate,
    WatchlistOut,
)

router = APIRouter(prefix="/watchlists", tags=["watchlists"])

#: Documented on every route: they are all guarded, so a 401 here is ordinary traffic and
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

#: The refusal that carries this ticket. A watchlist belonging to another account is
#: **indistinguishable** from one that was never created — same status, same `code`, same
#: `details`. A 403 would confirm which ids are real, which is the half of the information
#: worth protecting.
NOT_FOUND_RESPONSE = {
    status.HTTP_404_NOT_FOUND: {
        "model": ErrorResponse,
        "description": (
            "`not_found` — no such watchlist, **or** it is not yours. The two are "
            "deliberately indistinguishable."
        ),
    }
}

#: On the entry routes the 404 has a second cause, and `details.resource` says which:
#: `watchlist`, `watchlist entry` (that stock is not on this list) or `stock` (no such
#: security at all).
ENTRY_NOT_FOUND_RESPONSE = {
    status.HTTP_404_NOT_FOUND: {
        "model": ErrorResponse,
        "description": (
            "`not_found` — no such watchlist (or not yours), no such security, or that "
            "stock is not on this watchlist. `details.resource` distinguishes them."
        ),
    }
}

#: `validation_error` from the service (a position outside the list), distinct from
#: FastAPI's own 422 for a malformed body. Both use the same envelope. Spelled as the plain
#: integer `app/main.py` uses: Starlette has deprecated `HTTP_422_UNPROCESSABLE_ENTITY` in
#: favour of a name older versions do not have, and a literal cannot go stale either way.
INVALID_POSITION_RESPONSE = {
    422: {
        "model": ErrorResponse,
        "description": (
            "`validation_error` — `position` falls outside the watchlist, or the body is malformed."
        ),
    }
}

LimitQuery = Annotated[
    int,
    Query(
        ge=1,
        le=MAX_PAGE_LIMIT,
        description="Window size. Above the ceiling is a 422, never a silent clamp.",
    ),
]

OffsetQuery = Annotated[int, Query(ge=0, description="Watchlists to skip.")]

WatchlistPath = Annotated[uuid.UUID, Path(description="The watchlist to act on. Yours only.")]

StockPath = Annotated[uuid.UUID, Path(description="The security whose membership row this is.")]


@router.post(
    "",
    response_model=WatchlistOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a watchlist",
    responses=UNAUTHORIZED_RESPONSE,
)
async def create_watchlist(
    body: WatchlistCreate, user: CurrentUser, service: WatchlistServiceDep
) -> WatchlistOut:
    """Start a new, empty watchlist owned by the signed-in account.

    The body carries a `title` and nothing else: ownership comes from the access token, so
    there is no field in which a caller could name somebody else's account.
    """
    return await service.create(body, owner=user)


@router.get(
    "",
    response_model=Page[WatchlistOut],
    summary="Your watchlists",
    responses=UNAUTHORIZED_RESPONSE,
)
async def list_watchlists(
    user: CurrentUser,
    service: WatchlistServiceDep,
    limit: LimitQuery = DEFAULT_PAGE_LIMIT,
    offset: OffsetQuery = 0,
) -> Page[WatchlistOut]:
    """Your own watchlists, without their contents, ordered by title.

    There is no id to substitute here and no way to ask for anybody else's: the collection
    *is* "mine". An account with no watchlists gets an empty page, not a 404.
    """
    return await service.list_mine(owner=user, limit=limit, offset=offset)


@router.get(
    "/{watchlist_id}",
    response_model=WatchlistDetailOut,
    summary="A watchlist with its stocks",
    responses={**UNAUTHORIZED_RESPONSE, **NOT_FOUND_RESPONSE},
)
async def read_watchlist(
    watchlist_id: WatchlistPath, user: CurrentUser, service: WatchlistServiceDep
) -> WatchlistDetailOut:
    """One of your watchlists, with its stocks already in `position` order.

    A watchlist you have not put anything on yet is a **200 with `entries: []`** — not a
    204, and not an error.
    """
    return await service.get_watchlist(watchlist_id=watchlist_id, owner=user)


@router.delete(
    "/{watchlist_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a watchlist",
    responses={**UNAUTHORIZED_RESPONSE, **NOT_FOUND_RESPONSE},
)
async def delete_watchlist(
    watchlist_id: WatchlistPath, user: CurrentUser, service: WatchlistServiceDep
) -> None:
    """Delete one of your watchlists and every membership row on it.

    The securities themselves are untouched — they are shared reference data.
    """
    await service.delete_watchlist(watchlist_id=watchlist_id, owner=user)


@router.post(
    "/{watchlist_id}/stocks",
    response_model=WatchlistEntryOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a stock to a watchlist",
    responses={
        **UNAUTHORIZED_RESPONSE,
        **ENTRY_NOT_FOUND_RESPONSE,
        **INVALID_POSITION_RESPONSE,
        status.HTTP_409_CONFLICT: {
            "model": ErrorResponse,
            "description": "`conflict` — that stock is already on this watchlist.",
        },
    },
)
async def add_watchlist_stock(
    watchlist_id: WatchlistPath,
    body: WatchlistEntryCreate,
    user: CurrentUser,
    service: WatchlistServiceDep,
) -> WatchlistEntryOut:
    """Put a stock on one of your watchlists.

    Omit `position` to **append**. Send one to insert at that index, pushing everything from
    there down by one; the valid range is `0` to the current length, inclusive.
    """
    return await service.add_stock(body, watchlist_id=watchlist_id, owner=user)


@router.delete(
    "/{watchlist_id}/stocks/{stock_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a stock from a watchlist",
    responses={**UNAUTHORIZED_RESPONSE, **ENTRY_NOT_FOUND_RESPONSE},
)
async def remove_watchlist_stock(
    watchlist_id: WatchlistPath,
    stock_id: StockPath,
    user: CurrentUser,
    service: WatchlistServiceDep,
) -> None:
    """Take a stock off one of your watchlists.

    The stocks below it move up, so the positions stay dense from zero.
    """
    await service.remove_stock(watchlist_id=watchlist_id, stock_id=stock_id, owner=user)


@router.patch(
    "/{watchlist_id}/stocks/{stock_id}",
    response_model=WatchlistDetailOut,
    summary="Move a stock within a watchlist",
    responses={
        **UNAUTHORIZED_RESPONSE,
        **ENTRY_NOT_FOUND_RESPONSE,
        **INVALID_POSITION_RESPONSE,
    },
)
async def move_watchlist_stock(
    watchlist_id: WatchlistPath,
    stock_id: StockPath,
    body: WatchlistEntryUpdate,
    user: CurrentUser,
    service: WatchlistServiceDep,
) -> WatchlistDetailOut:
    """Move the stock named in the path to the `position` named in the body.

    **Which** stock moves is the `stock_id` in the URL, not an index the client believes it
    currently sits at — the server already knows where it is, and a stale client view of
    that used to reorder a different stock than the user dropped. The whole watchlist comes
    back in its new order, so a drag-and-drop client can render the server's answer rather
    than its own guess.
    """
    return await service.reorder_stock(
        body, watchlist_id=watchlist_id, stock_id=stock_id, owner=user
    )


__all__ = ["router"]
