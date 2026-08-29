"""Contracts for a watchlist and the stocks on it.

Two levels of output, because the two reads are different sizes:

:class:`WatchlistOut`
    The watchlist itself. What ``GET /v1/watchlists`` returns for each row — a list of
    watchlists should not drag every membership row and every stock behind it.
:class:`WatchlistDetailOut`
    The watchlist *with* its ordered entries and each entry's stock, which is the one
    screen the frontend actually renders. It maps onto the eager-load chain ANV-7 proved
    in ``tests/integration/test_models.py``: ``User.watchlists -> Watchlist.entries ->
    WatchlistData.stock``. Serving it from a lazily-loaded object is impossible under
    asyncio, so the schema and the repo's ``selectinload`` are two halves of one decision.

**No ``user_id`` is ever accepted from a client.** Ownership comes from the access token,
never from a request body — a ``user_id`` field on :class:`WatchlistCreate` would be an
invitation to create a watchlist on somebody else's account. It is returned on
:class:`WatchlistOut` because a client already knows whose watchlists it asked for.

``WatchlistData`` has no surrogate key: an entry is identified by its
``(watchlist_id, stock_id)`` pair, so :class:`WatchlistEntryOut` carries both and no ``id``.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.models.watchlist import DEFAULT_TITLE, TITLE_MAX_LENGTH
from app.schemas.stock import StockOut

Title = Annotated[
    str, Field(min_length=1, max_length=TITLE_MAX_LENGTH, examples=["Semiconductors"])
]

#: Ordinals are zero-based and dense within a watchlist. They are *not* unique in the
#: database, because a reorder swaps two of them and a non-deferrable constraint would
#: reject the intermediate state (ANV-7).
Position = Annotated[int, Field(ge=0, examples=[0])]


class WatchlistCreate(BaseModel):
    """``POST /v1/watchlists`` — start a new list.

    ``title`` defaults to the same string the column's server default uses, so a client
    that sends ``{}`` and one that omits the body entirely get the same list back.
    """

    title: Title = Field(default=DEFAULT_TITLE)


class WatchlistUpdate(BaseModel):
    """``PATCH /v1/watchlists/{watchlist_id}`` — rename a list.

    Only ``title`` is here because only ``title`` is a user's to change: ``watchlist_id``
    is the identity and ``user_id`` is the owner, and neither is editable. ``None`` means
    "unchanged"; the column is ``NOT NULL``, so it can never mean "clear it".
    """

    title: Title | None = None


class WatchlistEntryCreate(BaseModel):
    """``POST /v1/watchlists/{watchlist_id}/stocks`` — put a stock on a list.

    ``position`` is optional and defaults to "append": the service asks the repo for the
    current length rather than trusting a client to know it. Sending one explicitly is how
    an insert-at-index works.
    """

    stock_id: uuid.UUID
    position: Position | None = Field(
        default=None,
        description="Where to insert. Omit to append to the end of the list.",
    )


class WatchlistEntryUpdate(BaseModel):
    """``PATCH /v1/watchlists/{watchlist_id}/stocks/{stock_id}`` — move an entry.

    Position is the only mutable thing about a membership row; changing which stock it
    points at is a delete and an add, not an edit. The rule that renumbers the *other*
    entries afterwards is pure and belongs to ANV-15's ``app/domain/watchlist.py``.
    """

    position: Position


class WatchlistEntryOut(BaseModel):
    """One stock's membership of one watchlist, at one position.

    Both halves of the composite key are present because together they *are* the entry's
    identity — there is no surrogate id to return instead.
    """

    model_config = ConfigDict(from_attributes=True)

    watchlist_id: uuid.UUID
    stock_id: uuid.UUID
    position: int


class WatchlistEntryDetailOut(WatchlistEntryOut):
    """An entry with the stock it points at, for the screen that renders both."""

    stock: StockOut


class WatchlistOut(BaseModel):
    """A watchlist without its contents."""

    model_config = ConfigDict(from_attributes=True)

    watchlist_id: uuid.UUID
    user_id: uuid.UUID
    title: str


class WatchlistDetailOut(WatchlistOut):
    """A watchlist with its entries, already in ``position`` order.

    The ordering is the relationship's (``order_by="WatchlistData.position"``), so neither
    this schema nor its caller sorts anything.
    """

    entries: list[WatchlistEntryDetailOut]


__all__ = [
    "Position",
    "Title",
    "WatchlistCreate",
    "WatchlistDetailOut",
    "WatchlistEntryCreate",
    "WatchlistEntryDetailOut",
    "WatchlistEntryOut",
    "WatchlistEntryUpdate",
    "WatchlistOut",
    "WatchlistUpdate",
]
