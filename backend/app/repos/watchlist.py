"""Queries over ``watchlists`` and its association table ``watchlist_data``.

One repo, not two: a ``WatchlistData`` row has no independent existence — no surrogate
key, identity is the ``(watchlist_id, stock_id)`` pair — so the watchlist *is* the
aggregate and its entries are reached through it.

**What is not here.** The reorder algorithm. The old ``PUT /v1/watchlist/stock`` computed
new ordinals inline in the handler, using a list index as if it were a position and
mutating the wrong rows whenever the two diverged. In Anvex that arithmetic is pure and
lives in ``app/domain/watchlist.py`` (ANV-15); this module supplies the ordinals it reads
(:meth:`WatchlistRepo.list_entries`, :meth:`WatchlistRepo.max_position`) and applies the
ordinals it returns (:meth:`WatchlistRepo.set_positions`). That split is the whole reason
the reorder rule is unit-testable without a database.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Watchlist, WatchlistData
from app.repos.base import BaseRepo


class WatchlistRepo(BaseRepo[Watchlist]):
    """Data access for :class:`app.models.Watchlist` and its entries."""

    model = Watchlist

    # -----------------------------------------------------------------------------------
    # Watchlists
    # -----------------------------------------------------------------------------------

    async def get_by_id(
        self, session: AsyncSession, watchlist_id: uuid.UUID
    ) -> Watchlist | None:
        """The watchlist with this id, or ``None``. Entries are **not** loaded."""
        return await self._one_or_none(
            session, select(Watchlist).where(Watchlist.watchlist_id == watchlist_id)
        )

    async def get_with_entries(
        self, session: AsyncSession, watchlist_id: uuid.UUID
    ) -> Watchlist | None:
        """The watchlist with its entries and each entry's stock eagerly loaded.

        The query behind ``WatchlistDetailOut`` (ANV-8), which cannot be served from a
        lazily-loaded object: touching ``.entries`` on a plain :meth:`get_by_id` result
        raises ``MissingGreenlet`` under asyncio.

        ``entries`` arrives **in ``position`` order** because ``Watchlist.entries``
        declares ``order_by="WatchlistData.position"`` on the relationship itself. That is
        deliberate and it is why no caller sorts: the ordering is a property of the
        collection, so it cannot be forgotten at one call site and remembered at another.
        """
        return await self._one_or_none(
            session,
            select(Watchlist)
            .where(Watchlist.watchlist_id == watchlist_id)
            .options(selectinload(Watchlist.entries).selectinload(WatchlistData.stock)),
        )

    async def list_for_user(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        *,
        limit: int,
        offset: int = 0,
    ) -> tuple[list[Watchlist], int]:
        """One window of a user's watchlists, plus how many they have.

        Ordered by title then id. ``watchlists`` has no ``created_at``, and a title alone
        is not unique, so the id is what makes the order total — without it two watchlists
        called "Semis" could swap places between two requests for the same page.
        """
        stmt = (
            select(Watchlist)
            .where(Watchlist.user_id == user_id)
            .order_by(Watchlist.title.asc(), Watchlist.watchlist_id.asc())
        )
        return await self._page(session, stmt, limit=limit, offset=offset)

    async def create(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        title: str | None = None,
    ) -> Watchlist:
        """Insert a watchlist and flush, so ``watchlist_id`` is readable.

        Omitting ``title`` leaves the column to its server default (``'My Watchlist'``)
        rather than repeating that string in Python, so the default has exactly one
        definition — the one Postgres enforces.
        """
        values: dict[str, object] = {"user_id": user_id}
        if title is not None:
            values["title"] = title
        watchlist = await self.add(session, Watchlist(**values))
        # `title` may have come from a server default, which the flush wrote but did not
        # read back. Refreshing here means the returned object is never a partial truth.
        await session.refresh(watchlist)
        return watchlist

    # -----------------------------------------------------------------------------------
    # Entries
    # -----------------------------------------------------------------------------------

    async def get_entry(
        self, session: AsyncSession, watchlist_id: uuid.UUID, stock_id: uuid.UUID
    ) -> WatchlistData | None:
        """The one entry for this ``(watchlist, stock)`` pair, or ``None``."""
        return await self._one_or_none(
            session,
            select(WatchlistData).where(
                WatchlistData.watchlist_id == watchlist_id,
                WatchlistData.stock_id == stock_id,
            ),
        )

    async def entry_exists(
        self, session: AsyncSession, watchlist_id: uuid.UUID, stock_id: uuid.UUID
    ) -> bool:
        """Whether this stock is already on this watchlist.

        The duplicate check the old ``POST /v1/watchlist/stock`` did by fetching the row.
        The composite primary key ANV-7 added means a race that slips past this check
        still fails at the flush with an ``IntegrityError`` rather than silently creating a
        second membership — the check is for a clean 409, not for correctness.
        """
        return await self._exists(
            session,
            select(WatchlistData.stock_id).where(
                WatchlistData.watchlist_id == watchlist_id,
                WatchlistData.stock_id == stock_id,
            ),
        )

    async def list_entries(
        self, session: AsyncSession, watchlist_id: uuid.UUID
    ) -> list[WatchlistData]:
        """Every entry on this watchlist in ``position`` order, stocks **not** loaded.

        The input to ANV-15's reorder: it needs the ordinals, not the securities. Use
        :meth:`get_with_entries` when the stocks themselves are wanted.

        Unpaginated on purpose — a watchlist is a hand-curated list, and the reorder rule
        is only correct if it sees all of it.
        """
        return await self._all(
            session,
            select(WatchlistData)
            .where(WatchlistData.watchlist_id == watchlist_id)
            .order_by(WatchlistData.position.asc(), WatchlistData.stock_id.asc()),
        )

    async def count_entries(self, session: AsyncSession, watchlist_id: uuid.UUID) -> int:
        """How many stocks are on this watchlist."""
        return await self._count(
            session,
            select(WatchlistData.stock_id).where(WatchlistData.watchlist_id == watchlist_id),
        )

    async def max_position(
        self, session: AsyncSession, watchlist_id: uuid.UUID
    ) -> int | None:
        """The highest ordinal on this watchlist, or ``None`` when it is empty.

        ``None`` rather than ``-1``: "there is no last position" is a different statement
        from "the last position is minus one", and inventing the latter would bake the
        0-based convention into the repo. Appending is ``(max_position or -1) + 1`` — a
        rule, and rules live in ``app/domain/``.
        """
        return await session.scalar(
            select(func.max(WatchlistData.position)).where(
                WatchlistData.watchlist_id == watchlist_id
            )
        )

    async def add_entry(
        self,
        session: AsyncSession,
        *,
        watchlist_id: uuid.UUID,
        stock_id: uuid.UUID,
        position: int,
    ) -> WatchlistData:
        """Put a stock on a watchlist at ``position`` and flush.

        ``position`` is required, not defaulted: where a new stock lands (top, bottom,
        somewhere the user dropped it) is a product decision, and the repo refuses to make
        it silently. ``position`` is intentionally not unique per watchlist (ANV-7), so a
        collision is legal — the reorder rule is what keeps the ordinals sane.
        """
        return await self.add(
            session,
            WatchlistData(watchlist_id=watchlist_id, stock_id=stock_id, position=position),
        )

    async def remove_entry(
        self, session: AsyncSession, watchlist_id: uuid.UUID, stock_id: uuid.UUID
    ) -> bool:
        """Take a stock off a watchlist. ``True`` when a row was actually removed.

        The boolean is a fact — how many rows matched — not a verdict. A service turns
        ``False`` into ``NotFoundError`` if it cares; a delete that removes nothing is not
        in itself an error.

        Issued as a single ``DELETE ... WHERE``: the row has no surrogate key, so there is
        nothing to load first, and loading it only to delete it would be two round trips
        for one statement.
        """
        result = await session.execute(
            delete(WatchlistData).where(
                WatchlistData.watchlist_id == watchlist_id,
                WatchlistData.stock_id == stock_id,
            )
        )
        return bool(result.rowcount)

    async def set_position(
        self,
        session: AsyncSession,
        watchlist_id: uuid.UUID,
        stock_id: uuid.UUID,
        position: int,
    ) -> WatchlistData | None:
        """Move one entry to ``position``. ``None`` when the entry does not exist."""
        entry = await self.get_entry(session, watchlist_id, stock_id)
        if entry is None:
            return None
        entry.position = position
        await session.flush()
        return entry

    async def set_positions(
        self,
        session: AsyncSession,
        watchlist_id: uuid.UUID,
        positions: Mapping[uuid.UUID, int],
    ) -> int:
        """Apply a whole ``{stock_id: position}`` map at once; returns rows changed.

        The write half of a reorder. ANV-15's pure function takes the current ordinals
        (:meth:`list_entries`) and a move, and returns the new ordinals; this applies them
        in one flush. Because ``position`` carries **no** unique constraint (ANV-7 chose
        that deliberately), the intermediate state part-way through a swap is legal and no
        ordering of the updates can trip a constraint.

        Stock ids not on the watchlist are ignored — the caller derived the map from this
        watchlist's own entries, and silently skipping a stale one beats a half-applied
        reorder.
        """
        if not positions:
            return 0
        entries = await self._all(
            session,
            select(WatchlistData).where(
                WatchlistData.watchlist_id == watchlist_id,
                WatchlistData.stock_id.in_(list(positions)),
            ),
        )
        changed = 0
        for entry in entries:
            new_position = positions[entry.stock_id]
            if entry.position != new_position:
                entry.position = new_position
                changed += 1
        await session.flush()
        return changed


#: A stateless, shareable instance. Repos hold no session, so one is enough.
watchlist_repo = WatchlistRepo()

__all__ = ["WatchlistRepo", "watchlist_repo"]
