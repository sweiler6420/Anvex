"""``WatchlistService`` against real Postgres, over real ``watchlist_data`` rows.

``tests/unit/test_services_watchlist.py`` covers the branches against fakes; this module
covers the four claims only a database can settle:

* **The ordering is the database's, not the fake's.** ``Watchlist.entries`` declares
  ``order_by="WatchlistData.position"`` on the relationship, so nothing in the service or
  the schema sorts — and the fixture below inserts a watchlist's entries **out of order**,
  so a service relying on insertion order fails here.
* **A reorder round-trips.** The new ordinals go through ``set_positions`` into real
  ``UPDATE`` statements and come back out of a fresh ``SELECT``, expired from the identity
  map first so the assertion reads Postgres rather than the objects still in the session.
* **The mid-swap state is legal in SQL.** ANV-7 left ``position`` non-unique on purpose so a
  whole new ordering can be flushed in one statement; a fake cannot demonstrate the absence
  of a constraint, and a non-deferrable unique index would reject every reorder here.
* **``pk_watchlist_data`` is really what the driver reports.** A hand-built
  ``IntegrityError`` only tests itself (``CLAUDE.md`` §4), so the duplicate-add race is
  reproduced by blinding the pre-check and letting the real primary key fire.

Ownership is re-asserted here too, briefly. It is thoroughly covered at unit and API level,
but the check compares a column that is loaded by a real query, and "the repo returns
somebody else's row and the service refuses it" is worth seeing happen against real SQL
once.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import ConflictError, NotFoundError
from app.models import Stock, User, Watchlist, WatchlistData
from app.repos.stock import StockRepo
from app.repos.watchlist import WatchlistRepo
from app.schemas.watchlist import (
    WatchlistCreate,
    WatchlistEntryCreate,
    WatchlistEntryUpdate,
)
from app.services.watchlist import DUPLICATE_STOCK_MESSAGE, WatchlistService
from app.settings import Settings
from tests.factories import StockFactory, UserFactory, WatchlistDataFactory, WatchlistFactory

#: The tickers seeded onto ``watchlist`` below, in the order their positions put them.
ORDER = ("AAPL", "NVDA", "TSLA", "MSFT")


def build_service(session: AsyncSession) -> WatchlistService:
    """The real service over the real repos — only the session comes from the harness."""
    return WatchlistService(
        session,
        Settings(jwt_secret_key="integration-test-jwt-secret"),
        watchlists=WatchlistRepo(),
        stocks=StockRepo(),
    )


class BlindWatchlistRepo(WatchlistRepo):
    """The real repo with only the duplicate pre-check forced to answer "free".

    Which is exactly the state the loser of a race is in: it asked "is this stock already on
    this watchlist", was told no, and then flushed after somebody else's insert had landed.
    Everything else — the ``INSERT``, the primary key, the constraint name asyncpg reports —
    is real, which is the whole point (``CLAUDE.md`` §4).
    """

    async def entry_exists(self, session, watchlist_id, stock_id) -> bool:  # type: ignore[no-untyped-def]
        return False


@pytest.fixture
async def owner(db_session: AsyncSession) -> User:
    return await UserFactory().create(db_session)


@pytest.fixture
async def intruder(db_session: AsyncSession) -> User:
    return await UserFactory().create(db_session)


@pytest.fixture
async def stocks(db_session: AsyncSession) -> dict[str, Stock]:
    created = {}
    for ticker in (*ORDER, "QUIET"):
        created[ticker] = await StockFactory().create(db_session, ticker_symbol=ticker)
    return created


@pytest.fixture
async def watchlist(
    db_session: AsyncSession, owner: User, stocks: dict[str, Stock]
) -> Watchlist:
    """Four stocks, **inserted in an order that is nothing like their positions**.

    So a service or a schema that happened to return the rows in insertion order — the
    order a naive ``SELECT`` with no ``ORDER BY`` tends to give back — fails rather than
    passes by luck.
    """
    created = await WatchlistFactory().create(db_session, user=owner, title="Semis")
    insertion = (("MSFT", 3), ("AAPL", 0), ("TSLA", 2), ("NVDA", 1))
    for ticker, position in insertion:
        await WatchlistDataFactory().create(
            db_session, watchlist=created, stock=stocks[ticker], position=position
        )
    return created


@pytest.fixture
async def empty(db_session: AsyncSession, owner: User) -> Watchlist:
    return await WatchlistFactory().create(db_session, user=owner, title="Later")


async def stored_positions(
    session: AsyncSession, watchlist_id: uuid.UUID
) -> list[tuple[str, int]]:
    """``(ticker, position)`` straight out of Postgres, in ``position`` order.

    A Core ``SELECT`` of two columns, deliberately: the rows come back as plain values off
    the connection rather than as ORM instances resolved through the identity map, so this
    reads what was actually flushed rather than what the service happens to be holding.
    """
    rows = await session.execute(
        select(Stock.ticker_symbol, WatchlistData.position)
        .join(Stock, Stock.stock_id == WatchlistData.stock_id)
        .where(WatchlistData.watchlist_id == watchlist_id)
        .order_by(WatchlistData.position.asc())
    )
    return [(ticker, position) for ticker, position in rows.all()]


# ---------------------------------------------------------------------------------------
# the ordered join
# ---------------------------------------------------------------------------------------


class TestTheOrderedRead:
    async def test_the_entries_come_back_in_position_order_not_insertion_order(
        self, db_session: AsyncSession, owner: User, watchlist: Watchlist
    ) -> None:
        detail = await build_service(db_session).get_watchlist(
            watchlist_id=watchlist.watchlist_id, owner=owner
        )

        assert [entry.stock.ticker_symbol for entry in detail.entries] == list(ORDER)
        assert [entry.position for entry in detail.entries] == [0, 1, 2, 3]

    async def test_each_entry_carries_its_security(
        self, db_session: AsyncSession, owner: User, watchlist: Watchlist
    ) -> None:
        """The two-level ``selectinload`` chain: without it, reading ``entry.stock`` under
        asyncio raises ``MissingGreenlet`` rather than lazily loading."""
        detail = await build_service(db_session).get_watchlist(
            watchlist_id=watchlist.watchlist_id, owner=owner
        )

        for entry in detail.entries:
            assert entry.stock.ticker_symbol
            assert entry.stock.stock_id == entry.stock_id

    async def test_an_empty_watchlist_reads_as_an_empty_list(
        self, db_session: AsyncSession, owner: User, empty: Watchlist
    ) -> None:
        detail = await build_service(db_session).get_watchlist(
            watchlist_id=empty.watchlist_id, owner=owner
        )

        assert detail.entries == []


# ---------------------------------------------------------------------------------------
# the reorder round trip
# ---------------------------------------------------------------------------------------


class TestReorderRoundTrip:
    async def test_a_move_to_the_front_is_persisted(
        self, db_session: AsyncSession, owner: User, watchlist: Watchlist, stocks: dict[str, Stock]
    ) -> None:
        await build_service(db_session).reorder_stock(
            WatchlistEntryUpdate(position=0),
            watchlist_id=watchlist.watchlist_id,
            stock_id=stocks["MSFT"].stock_id,
            owner=owner,
        )

        assert await stored_positions(db_session, watchlist.watchlist_id) == [
            ("MSFT", 0),
            ("AAPL", 1),
            ("NVDA", 2),
            ("TSLA", 3),
        ]

    async def test_a_move_to_the_back_is_persisted(
        self, db_session: AsyncSession, owner: User, watchlist: Watchlist, stocks: dict[str, Stock]
    ) -> None:
        await build_service(db_session).reorder_stock(
            WatchlistEntryUpdate(position=3),
            watchlist_id=watchlist.watchlist_id,
            stock_id=stocks["AAPL"].stock_id,
            owner=owner,
        )

        assert await stored_positions(db_session, watchlist.watchlist_id) == [
            ("NVDA", 0),
            ("TSLA", 1),
            ("MSFT", 2),
            ("AAPL", 3),
        ]

    async def test_a_whole_new_ordering_flushes_in_one_statement(
        self, db_session: AsyncSession, owner: User, watchlist: Watchlist, stocks: dict[str, Stock]
    ) -> None:
        """The mid-swap state a unique index would have rejected.

        Moving index 3 to index 1 assigns position 1 to a row while another row still holds
        it, until the flush completes. ``position`` carries no unique constraint (ANV-7)
        precisely so that is legal — and a fake cannot demonstrate the absence of a
        constraint, so this has to run against real Postgres.
        """
        await build_service(db_session).reorder_stock(
            WatchlistEntryUpdate(position=1),
            watchlist_id=watchlist.watchlist_id,
            stock_id=stocks["MSFT"].stock_id,
            owner=owner,
        )

        assert await stored_positions(db_session, watchlist.watchlist_id) == [
            ("AAPL", 0),
            ("MSFT", 1),
            ("NVDA", 2),
            ("TSLA", 3),
        ]

    async def test_a_no_op_move_leaves_the_rows_alone(
        self, db_session: AsyncSession, owner: User, watchlist: Watchlist, stocks: dict[str, Stock]
    ) -> None:
        service = build_service(db_session)
        before = await stored_positions(db_session, watchlist.watchlist_id)

        await service.reorder_stock(
            WatchlistEntryUpdate(position=1),
            watchlist_id=watchlist.watchlist_id,
            stock_id=stocks["NVDA"].stock_id,
            owner=owner,
        )

        assert await stored_positions(db_session, watchlist.watchlist_id) == before

    async def test_several_moves_compose(
        self, db_session: AsyncSession, owner: User, watchlist: Watchlist, stocks: dict[str, Stock]
    ) -> None:
        service = build_service(db_session)

        for ticker, position in (("TSLA", 0), ("AAPL", 3), ("NVDA", 1)):
            await service.reorder_stock(
                WatchlistEntryUpdate(position=position),
                watchlist_id=watchlist.watchlist_id,
                stock_id=stocks[ticker].stock_id,
                owner=owner,
            )

        stored = await stored_positions(db_session, watchlist.watchlist_id)
        assert [ticker for ticker, _ in stored] == ["TSLA", "NVDA", "MSFT", "AAPL"]
        # The invariant survives real SQL: dense, zero-based, no ties.
        assert [position for _, position in stored] == [0, 1, 2, 3]

    async def test_repairing_positions_that_are_not_contiguous(
        self, db_session: AsyncSession, owner: User, empty: Watchlist, stocks: dict[str, Stock]
    ) -> None:
        """Untidy ordinals are a state the database can genuinely hold, since nothing
        constrains ``position``. A reorder must come back dense regardless."""
        for ticker, position in (("AAPL", 5), ("NVDA", 9), ("TSLA", 40)):
            await WatchlistDataFactory().create(
                db_session, watchlist=empty, stock=stocks[ticker], position=position
            )

        await build_service(db_session).reorder_stock(
            WatchlistEntryUpdate(position=0),
            watchlist_id=empty.watchlist_id,
            stock_id=stocks["TSLA"].stock_id,
            owner=owner,
        )

        assert await stored_positions(db_session, empty.watchlist_id) == [
            ("TSLA", 0),
            ("AAPL", 1),
            ("NVDA", 2),
        ]


# ---------------------------------------------------------------------------------------
# writes
# ---------------------------------------------------------------------------------------


class TestAddAndRemove:
    async def test_appending_lands_after_the_last_stock(
        self, db_session: AsyncSession, owner: User, watchlist: Watchlist, stocks: dict[str, Stock]
    ) -> None:
        entry = await build_service(db_session).add_stock(
            WatchlistEntryCreate(stock_id=stocks["QUIET"].stock_id),
            watchlist_id=watchlist.watchlist_id,
            owner=owner,
        )

        assert entry.position == 4
        assert (await stored_positions(db_session, watchlist.watchlist_id))[-1] == (
            "QUIET",
            4,
        )

    async def test_appending_to_a_single_stock_watchlist_does_not_collide(
        self, db_session: AsyncSession, owner: User, empty: Watchlist, stocks: dict[str, Stock]
    ) -> None:
        """The ``max_position == 0`` case, against the real ``max()`` query — which returns
        ``0``, not ``None``, and would append on top of the first stock under the
        ``(max_position or -1) + 1`` phrasing of the append rule."""
        service = build_service(db_session)
        for ticker in ("AAPL", "NVDA"):
            await service.add_stock(
                WatchlistEntryCreate(stock_id=stocks[ticker].stock_id),
                watchlist_id=empty.watchlist_id,
                owner=owner,
            )

        assert await stored_positions(db_session, empty.watchlist_id) == [
            ("AAPL", 0),
            ("NVDA", 1),
        ]

    async def test_inserting_at_an_index_shifts_the_rest_down(
        self, db_session: AsyncSession, owner: User, watchlist: Watchlist, stocks: dict[str, Stock]
    ) -> None:
        await build_service(db_session).add_stock(
            WatchlistEntryCreate(stock_id=stocks["QUIET"].stock_id, position=1),
            watchlist_id=watchlist.watchlist_id,
            owner=owner,
        )

        assert await stored_positions(db_session, watchlist.watchlist_id) == [
            ("AAPL", 0),
            ("QUIET", 1),
            ("NVDA", 2),
            ("TSLA", 3),
            ("MSFT", 4),
        ]

    async def test_removing_a_stock_closes_the_gap_in_the_database(
        self, db_session: AsyncSession, owner: User, watchlist: Watchlist, stocks: dict[str, Stock]
    ) -> None:
        await build_service(db_session).remove_stock(
            watchlist_id=watchlist.watchlist_id,
            stock_id=stocks["NVDA"].stock_id,
            owner=owner,
        )

        assert await stored_positions(db_session, watchlist.watchlist_id) == [
            ("AAPL", 0),
            ("TSLA", 1),
            ("MSFT", 2),
        ]

    async def test_a_duplicate_is_a_conflict(
        self, db_session: AsyncSession, owner: User, watchlist: Watchlist, stocks: dict[str, Stock]
    ) -> None:
        with pytest.raises(ConflictError):
            await build_service(db_session).add_stock(
                WatchlistEntryCreate(stock_id=stocks["AAPL"].stock_id),
                watchlist_id=watchlist.watchlist_id,
                owner=owner,
            )

    async def test_the_primary_key_catches_a_duplicate_the_pre_check_missed(
        self, db_session: AsyncSession, owner: User, watchlist: Watchlist, stocks: dict[str, Stock]
    ) -> None:
        """The race the pre-check cannot close, and the reason the constant is not a guess.

        With ``entry_exists`` blinded, the ``INSERT`` reaches Postgres and
        ``pk_watchlist_data`` rejects it. If the real constraint name ever stopped matching
        ``ENTRY_PRIMARY_KEY``, the service would re-raise the ``IntegrityError`` and this
        test would fail with a 500-shaped error instead of the conflict — which is exactly
        what a hand-built exception could never tell us.
        """
        service = WatchlistService(
            db_session,
            Settings(jwt_secret_key="integration-test-jwt-secret"),
            watchlists=BlindWatchlistRepo(),
            stocks=StockRepo(),
        )

        with pytest.raises(ConflictError) as caught:
            await service.add_stock(
                WatchlistEntryCreate(stock_id=stocks["AAPL"].stock_id),
                watchlist_id=watchlist.watchlist_id,
                owner=owner,
            )

        assert caught.value.message == DUPLICATE_STOCK_MESSAGE

    async def test_the_session_is_usable_again_after_the_conflict(
        self, db_session: AsyncSession, owner: User, watchlist: Watchlist, stocks: dict[str, Stock]
    ) -> None:
        """The rollback is not cosmetic. Postgres puts a transaction that hit a constraint
        violation into an aborted state and refuses every later statement in it, so without
        the ``session.rollback()`` in ``add_stock`` the 409 would be followed by an
        ``InFailedSQLTransaction`` on whatever ran next — which is a 500 for the *next*
        request, not this one. Asserted by doing more real work afterwards, exactly as
        ``tests/integration/test_services_user.py`` does.

        The recovery work is built from **fresh** rows rather than the fixtures above,
        because the harness joins this session to the outer transaction with
        ``create_savepoint``: the service's ``rollback()`` releases back to the savepoint the
        session opened, so everything the fixtures inserted is gone by the time we get here.
        That is the harness being faithful, not a flaw — in production the same rollback
        discards exactly the work the aborted request had done.
        """
        service = WatchlistService(
            db_session,
            Settings(jwt_secret_key="integration-test-jwt-secret"),
            watchlists=BlindWatchlistRepo(),
            stocks=StockRepo(),
        )

        with pytest.raises(ConflictError):
            await service.add_stock(
                WatchlistEntryCreate(stock_id=stocks["AAPL"].stock_id),
                watchlist_id=watchlist.watchlist_id,
                owner=owner,
            )

        survivor = await UserFactory().create(db_session)
        recovered = await service.create(WatchlistCreate(title="After"), owner=survivor)
        assert recovered.title == "After"
        assert recovered.user_id == survivor.user_id


class TestDelete:
    async def test_deleting_a_watchlist_cascades_its_entries(
        self, db_session: AsyncSession, owner: User, watchlist: Watchlist
    ) -> None:
        """``watchlist_data.watchlist_id`` is ``ON DELETE CASCADE`` with
        ``passive_deletes=True``, so the rows go in the database rather than being loaded
        and rewritten by the ORM first."""
        await build_service(db_session).delete_watchlist(
            watchlist_id=watchlist.watchlist_id, owner=owner
        )

        remaining = await db_session.execute(
            select(WatchlistData).where(
                WatchlistData.watchlist_id == watchlist.watchlist_id
            )
        )
        assert remaining.scalars().all() == []
        assert (
            await db_session.get(Watchlist, watchlist.watchlist_id)
        ) is None

    async def test_the_securities_themselves_survive(
        self, db_session: AsyncSession, owner: User, watchlist: Watchlist, stocks: dict[str, Stock]
    ) -> None:
        """A stock is shared reference data, not part of the watchlist that pointed at it."""
        await build_service(db_session).delete_watchlist(
            watchlist_id=watchlist.watchlist_id, owner=owner
        )

        assert await db_session.get(Stock, stocks["AAPL"].stock_id) is not None


# ---------------------------------------------------------------------------------------
# ownership, against real SQL
# ---------------------------------------------------------------------------------------


class TestOwnershipAgainstRealSql:
    async def test_the_repo_hands_back_another_users_row_and_the_service_refuses_it(
        self, db_session: AsyncSession, intruder: User, watchlist: Watchlist
    ) -> None:
        """Both halves in one test, because together they are the design.

        ``WatchlistRepo.get_by_id`` genuinely returns the row — the repo layer provides no
        "owned by user" query on purpose (``CLAUDE.md`` §3) — and the service is the only
        thing standing between that row and the response.
        """
        row = await WatchlistRepo().get_by_id(db_session, watchlist.watchlist_id)
        assert row is not None and row.user_id != intruder.user_id

        with pytest.raises(NotFoundError):
            await build_service(db_session).get_watchlist(
                watchlist_id=watchlist.watchlist_id, owner=intruder
            )

    async def test_a_refused_reorder_changes_nothing_in_the_database(
        self,
        db_session: AsyncSession,
        intruder: User,
        watchlist: Watchlist,
        stocks: dict[str, Stock],
    ) -> None:
        before = await stored_positions(db_session, watchlist.watchlist_id)

        with pytest.raises(NotFoundError):
            await build_service(db_session).reorder_stock(
                WatchlistEntryUpdate(position=0),
                watchlist_id=watchlist.watchlist_id,
                stock_id=stocks["MSFT"].stock_id,
                owner=intruder,
            )

        assert await stored_positions(db_session, watchlist.watchlist_id) == before

    async def test_a_refused_delete_leaves_the_watchlist_standing(
        self, db_session: AsyncSession, intruder: User, watchlist: Watchlist
    ) -> None:
        with pytest.raises(NotFoundError):
            await build_service(db_session).delete_watchlist(
                watchlist_id=watchlist.watchlist_id, owner=intruder
            )

        assert await db_session.get(Watchlist, watchlist.watchlist_id) is not None

    async def test_listing_is_scoped_to_the_caller(
        self, db_session: AsyncSession, owner: User, intruder: User, watchlist: Watchlist
    ) -> None:
        await WatchlistFactory().create(db_session, user=intruder, title="Theirs")
        service = build_service(db_session)

        mine = await service.list_mine(owner=owner)
        theirs = await service.list_mine(owner=intruder)

        assert watchlist.watchlist_id in {row.watchlist_id for row in mine.items}
        assert watchlist.watchlist_id not in {row.watchlist_id for row in theirs.items}


async def test_a_created_watchlist_belongs_to_its_creator(
    db_session: AsyncSession, owner: User
) -> None:
    created = await build_service(db_session).create(
        WatchlistCreate(title="Fresh"), owner=owner
    )

    stored = await db_session.get(Watchlist, created.watchlist_id)
    assert stored is not None and stored.user_id == owner.user_id
