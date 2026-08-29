"""Watchlist use cases: create, list mine, read one, add a stock, drop one, reorder, delete.

Written to the shape ``app/services/auth.py`` established (``CLAUDE.md`` §3) — collaborators
in the constructor defaulting to the repo singletons, one ``async`` method per use case,
keyword-only arguments, a schema out, ``app.domain.errors`` on the way out, and the
``commit()`` here because repos only flush.

**Ownership is the point of this module.** Every method takes ``owner`` and every method
enforces it, and the enforcement is *here* rather than in a query on purpose. ``CLAUDE.md``
§3: repos deliberately provide no "owned by user" lookup, because authorization is not data
access — a repo that silently filtered by ``user_id`` would make the check invisible, and an
invisible check is one nobody notices the absence of. The endpoint this replaces is the
worked example: ``get_watchlist`` filtered on ``user_id`` and ``reposition_stock`` did not,
in the same file, and the difference was one clause on one line. ``current_user`` was
injected into the reorder handler and then never read, so any authenticated caller could
reorder — and, via ``add_watchlist_stock``, add to — anybody's watchlist. Since registration
is self-service, "any authenticated caller" is "anybody at all". Here the check goes through
one private method, :meth:`WatchlistService._resolve_owned`, that every use case must call
to obtain the watchlist it is about to work on: you cannot reach the entries without having
passed it.

**The refusal is a 404, byte-identical to "no such watchlist".** ``CLAUDE.md`` §4 and
ANV-12's ``GET /v1/users/{user_id}``: a 403 would confirm that the id names a real list,
which is the half of the information worth protecting — an attacker enumerating ids learns
nothing from a uniform 404 and learns the entire id space from a 403. Both refusals raise
:class:`~app.domain.errors.NotFoundError` with the same ``resource`` and the same
``identifier``, and both are raised **before any query against the entries**, so the
response time does not answer the question either. That is why
:meth:`_resolve_owned` reads the watchlist row alone (``get_by_id``) rather than resolving
it with its entries eagerly loaded: one extra primary-key lookup on the read path, in
exchange for a refusal that does strictly no work on the child table.

**An empty watchlist is a 200 with an empty list.** The old ``GET /v1/watchlist/{id}``
raised ``HTTP_204_NO_CONTENT`` *with* a ``detail`` body when a list had no stocks in it —
which is not a valid HTTP response (204 forbids a body, so the payload it built was
discarded or the response was malformed depending on the stack), and which made "you have
not added anything yet" indistinguishable from an error at every client. A watchlist with
no stocks is a successful read of an empty collection.

**The ordinals are the domain's, not this module's.** ``app/domain/watchlist.py`` owns
append, insert, move and drop; this service's job is to fetch the current ordinals, hand
them over, and apply whatever comes back through
:meth:`~app.repos.watchlist.WatchlistRepo.set_positions`. Nothing here does arithmetic on a
position, which is what keeps the rule unit-testable without a database and identical for a
future Celery caller.
"""

from __future__ import annotations

import uuid
from typing import Final

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import ConflictError, NotFoundError
from app.domain.stock_data import resolve_window
from app.domain.watchlist import (
    ENTRY_RESOURCE,
    RESOURCE,
    insert,
    next_position,
    remove,
    reposition,
)
from app.models import User, Watchlist
from app.repos.stock import StockRepo, stock_repo
from app.repos.watchlist import WatchlistRepo, watchlist_repo
from app.schemas.pagination import Page
from app.schemas.watchlist import (
    WatchlistCreate,
    WatchlistDetailOut,
    WatchlistEntryCreate,
    WatchlistEntryOut,
    WatchlistEntryUpdate,
    WatchlistOut,
)
from app.settings import Settings

logger = structlog.get_logger("anvex.watchlists")

#: The noun a 404 about a security reports. Deliberately the same string as
#: ``app.services.stock.RESOURCE`` and ``app.services.stock_data.RESOURCE`` — a unit test
#: asserts they stay equal — so a client branching on ``details["resource"]`` sees one
#: spelling no matter which endpoint refused it. It is re-declared rather than imported
#: because one service importing another's module is the coupling ``CLAUDE.md`` §3 warns
#: about; the test is what keeps the two honest.
STOCK_RESOURCE: Final[str] = "stock"

#: The composite primary key ANV-7 put on ``watchlist_data``. It is what actually prevents
#: the same stock appearing twice on one watchlist; :meth:`WatchlistService.add_stock`'s
#: ``entry_exists`` pre-check exists only so the ordinary case gets a clean 409 instead of
#: an integrity error (``CLAUDE.md`` §4). The name comes from ``Base.metadata``'s naming
#: convention, so it is reproducible rather than whatever Postgres would have invented.
ENTRY_PRIMARY_KEY: Final[str] = "pk_watchlist_data"

DUPLICATE_STOCK_MESSAGE: Final[str] = "That stock is already on this watchlist."


class WatchlistService:
    """Everything a signed-in user can do to their own watchlists."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        watchlists: WatchlistRepo = watchlist_repo,
        stocks: StockRepo = stock_repo,
    ) -> None:
        self.session = session
        self.settings = settings
        #: Both keyword-defaulted to the module-level singletons, which is the seam a unit
        #: test replaces with in-memory fakes to run the real service without Postgres.
        self.watchlists = watchlists
        #: Only ever asked "does this security exist" — adding a stock that does not is a
        #: 404 rather than a foreign-key violation surfacing as a 500.
        self.stocks = stocks

    # -----------------------------------------------------------------------------------
    # Watchlists
    # -----------------------------------------------------------------------------------

    async def create(self, data: WatchlistCreate, *, owner: User) -> WatchlistOut:
        """Start a new, empty watchlist owned by ``owner``.

        The owner comes from the access token and can come from nowhere else:
        :class:`~app.schemas.watchlist.WatchlistCreate` has no ``user_id`` field, so there
        is no request body that could name somebody else's account.
        """
        watchlist = await self.watchlists.create(
            self.session, user_id=owner.user_id, title=data.title
        )
        await self.session.commit()
        logger.info(
            "watchlists.created",
            user_id=str(owner.user_id),
            watchlist_id=str(watchlist.watchlist_id),
        )
        return WatchlistOut.model_validate(watchlist)

    async def list_mine(
        self, *, owner: User, limit: int | None = None, offset: int | None = None
    ) -> Page[WatchlistOut]:
        """One page of ``owner``'s own watchlists — there is no way to ask for anybody's.

        The scoping here is not a refusal, it is the whole query: the collection *is* "mine",
        so there is no id for a caller to substitute and no cross-account case to leak. A
        user with no watchlists gets an empty page with ``total`` 0, not a 404.
        """
        window = resolve_window(limit=limit, offset=offset)
        rows, total = await self.watchlists.list_for_user(
            self.session, owner.user_id, limit=window.limit, offset=window.offset
        )
        return Page[WatchlistOut](
            items=[WatchlistOut.model_validate(row) for row in rows],
            total=total,
            limit=window.limit,
            offset=window.offset,
        )

    async def get_watchlist(self, *, watchlist_id: uuid.UUID, owner: User) -> WatchlistDetailOut:
        """One of ``owner``'s watchlists, with its stocks already in ``position`` order.

        The ordering is the relationship's (``order_by="WatchlistData.position"``), so
        nothing here sorts — see :meth:`~app.repos.watchlist.WatchlistRepo.get_with_entries`.
        An empty watchlist is a 200 with ``entries: []``; see the module docstring for the
        invalid 204 this replaces.

        :raises NotFoundError: no such watchlist, **or** it is not ``owner``'s. The two are
            deliberately indistinguishable.
        """
        await self._resolve_owned(watchlist_id, owner)
        watchlist = await self.watchlists.get_with_entries(self.session, watchlist_id)
        if watchlist is None:  # pragma: no cover - lost a race with a concurrent delete
            raise NotFoundError(RESOURCE, watchlist_id)
        return WatchlistDetailOut.model_validate(watchlist)

    async def delete_watchlist(self, *, watchlist_id: uuid.UUID, owner: User) -> None:
        """Delete one of ``owner``'s watchlists and everything on it.

        The entries go with it in the database (``watchlist_data.watchlist_id`` is
        ``ON DELETE CASCADE``, mirrored by ``passive_deletes=True``), so the ORM does not
        load them only to rewrite them first.

        :raises NotFoundError: no such watchlist, or it is not ``owner``'s.
        """
        watchlist = await self._resolve_owned(watchlist_id, owner)
        await self.watchlists.delete(self.session, watchlist)
        await self.session.commit()
        logger.info(
            "watchlists.deleted",
            user_id=str(owner.user_id),
            watchlist_id=str(watchlist_id),
        )

    # -----------------------------------------------------------------------------------
    # Entries
    # -----------------------------------------------------------------------------------

    async def add_stock(
        self, data: WatchlistEntryCreate, *, watchlist_id: uuid.UUID, owner: User
    ) -> WatchlistEntryOut:
        """Put a stock on one of ``owner``'s watchlists.

        An omitted ``position`` **appends**, which costs one scalar query
        (:meth:`~app.repos.watchlist.WatchlistRepo.max_position`) rather than a read of
        every entry — the common "watch this too" case does not need to see the list. An
        explicit ``position`` is an insert-at-index, which does need the whole list, because
        every stock at or after that index shifts down by one.

        :raises NotFoundError: no such watchlist (or not ``owner``'s), or no such security.
        :raises ConflictError: that stock is already on this watchlist.
        :raises ValidationError: ``position`` is outside ``0..n``.
        """
        await self._resolve_owned(watchlist_id, owner)
        await self._require_stock(data.stock_id)
        await self._refuse_duplicate(watchlist_id, data.stock_id)

        if data.position is None:
            positions = {
                data.stock_id: next_position(
                    await self.watchlists.max_position(self.session, watchlist_id)
                )
            }
        else:
            positions = insert(
                await self._positions(watchlist_id),
                stock_id=data.stock_id,
                destination=data.position,
            )

        try:
            entry = await self.watchlists.add_entry(
                self.session,
                watchlist_id=watchlist_id,
                stock_id=data.stock_id,
                position=positions[data.stock_id],
            )
            # The new row is already at its final position, so it is a no-op in this map;
            # passing the whole thing keeps one source of truth for the new ordering.
            await self.watchlists.set_positions(self.session, watchlist_id, positions)
            await self.session.commit()
        except IntegrityError as exc:
            # Somebody added the same stock between the pre-check and this flush. The
            # composite primary key is what makes that safe; this makes it civil — and the
            # rollback comes first, because Postgres aborts the whole transaction on a
            # constraint violation and refuses every later statement in it.
            await self.session.rollback()
            if ENTRY_PRIMARY_KEY not in self._constraint_hint(exc):
                raise
            logger.warning("watchlists.add_stock_lost_the_race", watchlist_id=str(watchlist_id))
            raise self._duplicate(data.stock_id) from exc

        logger.info(
            "watchlists.stock_added",
            watchlist_id=str(watchlist_id),
            stock_id=str(data.stock_id),
            position=entry.position,
        )
        return WatchlistEntryOut.model_validate(entry)

    async def remove_stock(
        self, *, watchlist_id: uuid.UUID, stock_id: uuid.UUID, owner: User
    ) -> None:
        """Take a stock off one of ``owner``'s watchlists and close the gap.

        The remaining stocks are renumbered so the ordinals stay ``0..n-1``: leaving a hole
        would make every later insert-at-index reason about a list whose positions no longer
        match its indices.

        :raises NotFoundError: no such watchlist (or not ``owner``'s), or that stock is not
            on it.
        """
        await self._resolve_owned(watchlist_id, owner)
        # `remove` raises the 404 for a stock that is not on the list, so the repo's
        # "did a row actually go" boolean is redundant here — the decision is already made.
        remaining = remove(await self._positions(watchlist_id), stock_id=stock_id)

        await self.watchlists.remove_entry(self.session, watchlist_id, stock_id)
        await self.watchlists.set_positions(self.session, watchlist_id, remaining)
        await self.session.commit()
        logger.info(
            "watchlists.stock_removed",
            watchlist_id=str(watchlist_id),
            stock_id=str(stock_id),
        )

    async def reorder_stock(
        self,
        data: WatchlistEntryUpdate,
        *,
        watchlist_id: uuid.UUID,
        stock_id: uuid.UUID,
        owner: User,
    ) -> WatchlistDetailOut:
        """Move one stock to ``data.position`` and renumber the list around it.

        The whole watchlist comes back, in its new order, because a drag-and-drop client has
        just guessed what the result will be and wants the server's answer to render against
        — and because the response is then the same shape as
        :meth:`get_watchlist`, so one client-side reducer handles both.

        Which stock moves is the ``stock_id`` in the path; where it lands is the position in
        the body. There is no "current index" parameter, and
        ``app/domain/watchlist.py`` explains at length why there must not be.

        :raises NotFoundError: no such watchlist (or not ``owner``'s), or that stock is not
            on it.
        :raises ValidationError: ``position`` is outside ``0..n-1``.
        """
        await self._resolve_owned(watchlist_id, owner)
        moved = reposition(
            await self._positions(watchlist_id),
            stock_id=stock_id,
            destination=data.position,
        )

        changed = await self.watchlists.set_positions(self.session, watchlist_id, moved)
        await self.session.commit()
        logger.info(
            "watchlists.reordered",
            watchlist_id=str(watchlist_id),
            stock_id=str(stock_id),
            position=data.position,
            rows_changed=changed,
        )
        return await self.get_watchlist(watchlist_id=watchlist_id, owner=owner)

    # -----------------------------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------------------------

    async def _resolve_owned(self, watchlist_id: uuid.UUID, owner: User) -> Watchlist:
        """The watchlist ``watchlist_id`` names, provided ``owner`` owns it.

        **The single gate.** Every use case above starts here, so "did this endpoint check
        ownership" is a question about one call rather than about seven separate ``WHERE``
        clauses — which is the shape the old router got wrong.

        Both refusals are the same :class:`~app.domain.errors.NotFoundError` with the same
        ``details``, and neither has touched ``watchlist_data``: the row is fetched **without
        its entries** precisely so that refusing does no work proportional to the size of a
        list the caller may not see.

        :raises NotFoundError: no such watchlist, or it belongs to somebody else.
        """
        watchlist = await self.watchlists.get_by_id(self.session, watchlist_id)
        if watchlist is None or watchlist.user_id != owner.user_id:
            if watchlist is not None:
                logger.info(
                    "watchlists.cross_account_access_refused",
                    user_id=str(owner.user_id),
                    watchlist_id=str(watchlist_id),
                )
            raise NotFoundError(RESOURCE, watchlist_id)
        return watchlist

    async def _positions(self, watchlist_id: uuid.UUID) -> dict[uuid.UUID, int]:
        """The watchlist's current ``{stock_id: position}`` map — the domain's input.

        Unpaginated on purpose: the reorder rule is only correct if it sees the whole list
        (:meth:`~app.repos.watchlist.WatchlistRepo.list_entries`), and a watchlist is a
        hand-curated collection rather than an unbounded feed.
        """
        entries = await self.watchlists.list_entries(self.session, watchlist_id)
        return {entry.stock_id: entry.position for entry in entries}

    async def _require_stock(self, stock_id: uuid.UUID) -> None:
        """Refuse a stock that does not exist, before the foreign key has to.

        ``watchlist_data.stock_id`` references ``stocks`` (ANV-7), so an unknown id would
        fail at the flush as an ``IntegrityError`` — a 500 for a request the API can simply
        answer with a 404. Reference data has no owner, so this 404 is the plain kind and
        confirming that a ticker exists gives nothing away.
        """
        if await self.stocks.get_by_id(self.session, stock_id) is None:
            raise NotFoundError(STOCK_RESOURCE, stock_id)

    async def _refuse_duplicate(self, watchlist_id: uuid.UUID, stock_id: uuid.UUID) -> None:
        """Turn an already-watched stock into a clean 409 rather than a primary-key error."""
        if await self.watchlists.entry_exists(self.session, watchlist_id, stock_id):
            raise self._duplicate(stock_id)

    @staticmethod
    def _duplicate(stock_id: uuid.UUID) -> ConflictError:
        """The one conflict this service raises, spelled once so the pre-check and the
        lost-race path are indistinguishable to a client."""
        return ConflictError(
            ENTRY_RESOURCE,
            stock_id,
            message=DUPLICATE_STOCK_MESSAGE,
            details={"field": "stock_id"},
        )

    @staticmethod
    def _constraint_hint(exc: IntegrityError) -> str:
        """Whatever ``exc`` can tell us about which constraint it violated.

        The same two-place lookup ``app/services/user.py`` uses: asyncpg exposes
        ``constraint_name`` as an attribute, and the name also appears in the message text.
        Which of the two survives SQLAlchemy's DBAPI adapter is not part of anybody's public
        API, so both are checked.
        """
        original = exc.orig
        for candidate in (original, getattr(original, "__cause__", None)):
            name = getattr(candidate, "constraint_name", None)
            if isinstance(name, str) and name:
                return name
        return str(original) if original is not None else str(exc)


__all__ = [
    "DUPLICATE_STOCK_MESSAGE",
    "ENTRY_PRIMARY_KEY",
    "RESOURCE",
    "STOCK_RESOURCE",
    "WatchlistService",
]
