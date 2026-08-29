"""``WatchlistRepo`` against a real Postgres.

The ordered join is the assertion that matters: ``WatchlistDetailOut`` renders a list a
user has dragged into an order, and if the query hands the entries back in insertion order
the reordering feature silently does nothing. Entries are inserted out of order in every
test here for that reason.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, Watchlist
from app.repos import StockRepo, WatchlistRepo
from tests.factories import StockFactory, UserFactory, WatchlistDataFactory, WatchlistFactory

repo = WatchlistRepo()


async def _watchlist_with_entries(
    session: AsyncSession, *, positions: tuple[int, ...] = (2, 0, 1)
) -> tuple[User, Watchlist, list]:
    """A watchlist whose stocks are inserted in an order that is *not* ``position`` order."""
    user = await UserFactory().create(session)
    watchlist = await WatchlistFactory().create(session, user=user)
    stocks = await StockFactory().create_many(session, len(positions))
    for position, stock in zip(positions, stocks, strict=True):
        await WatchlistDataFactory().create(
            session, watchlist=watchlist, stock=stock, position=position
        )
    return user, watchlist, stocks


class TestWatchlists:
    async def test_get_by_id(self, db_session: AsyncSession) -> None:
        user = await UserFactory().create(db_session)
        watchlist = await WatchlistFactory().create(db_session, user=user)

        found = await repo.get_by_id(db_session, watchlist.watchlist_id)

        assert found is not None
        assert found.watchlist_id == watchlist.watchlist_id

    async def test_get_by_id_is_none_for_an_unknown_id(self, db_session: AsyncSession) -> None:
        assert await repo.get_by_id(db_session, uuid.uuid4()) is None

    async def test_list_for_user_returns_only_that_users_watchlists(
        self, db_session: AsyncSession
    ) -> None:
        mine = await UserFactory().create(db_session)
        theirs = await UserFactory().create(db_session)
        await WatchlistFactory().create(db_session, user=mine, title="Semis")
        await WatchlistFactory().create(db_session, user=mine, title="Banks")
        await WatchlistFactory().create(db_session, user=theirs, title="Theirs")

        rows, total = await repo.list_for_user(db_session, mine.user_id, limit=10)

        assert [w.title for w in rows] == ["Banks", "Semis"]
        assert total == 2

    async def test_list_for_user_is_empty_for_a_user_with_none(
        self, db_session: AsyncSession
    ) -> None:
        user = await UserFactory().create(db_session)

        assert await repo.list_for_user(db_session, user.user_id, limit=10) == ([], 0)

    async def test_list_for_user_pagination_boundaries(self, db_session: AsyncSession) -> None:
        user = await UserFactory().create(db_session)
        for title in ("A", "B", "C"):
            await WatchlistFactory().create(db_session, user=user, title=title)

        first, total = await repo.list_for_user(db_session, user.user_id, limit=2)
        past_end, past_end_total = await repo.list_for_user(
            db_session, user.user_id, limit=2, offset=9
        )

        assert [w.title for w in first] == ["A", "B"]
        assert total == 3
        assert past_end == []
        assert past_end_total == 3, "an offset past the end still reports the full total"

    async def test_create_uses_the_server_default_title(self, db_session: AsyncSession) -> None:
        user = await UserFactory().create(db_session)

        watchlist = await repo.create(db_session, user_id=user.user_id)

        assert isinstance(watchlist.watchlist_id, uuid.UUID)
        assert watchlist.title == "My Watchlist"

    async def test_create_accepts_a_title(self, db_session: AsyncSession) -> None:
        user = await UserFactory().create(db_session)

        watchlist = await repo.create(db_session, user_id=user.user_id, title="Semis")

        assert watchlist.title == "Semis"

    async def test_create_does_not_commit(self, db_session: AsyncSession) -> None:
        user = await UserFactory().create(db_session)
        watchlist = await repo.create(db_session, user_id=user.user_id, title="Temp")
        watchlist_id = watchlist.watchlist_id

        await db_session.rollback()

        assert await repo.get_by_id(db_session, watchlist_id) is None

    async def test_update_renames(self, db_session: AsyncSession) -> None:
        user = await UserFactory().create(db_session)
        watchlist = await WatchlistFactory().create(db_session, user=user, title="Old")

        await repo.update(db_session, watchlist, {"title": "New"})

        assert watchlist.title == "New"

    async def test_delete_takes_its_entries_with_it(self, db_session: AsyncSession) -> None:
        """`watchlist_data.watchlist_id` is ON DELETE CASCADE — the memberships go too."""
        _, watchlist, _ = await _watchlist_with_entries(db_session)

        await repo.delete(db_session, watchlist)

        assert await repo.get_by_id(db_session, watchlist.watchlist_id) is None
        assert await repo.count_entries(db_session, watchlist.watchlist_id) == 0


class TestTheOrderedJoin:
    async def test_get_with_entries_returns_position_order(
        self, db_session: AsyncSession
    ) -> None:
        """Inserted 2, 0, 1 — read back 0, 1, 2, without the caller sorting anything."""
        _, watchlist, _ = await _watchlist_with_entries(db_session)
        db_session.expunge_all()

        found = await repo.get_with_entries(db_session, watchlist.watchlist_id)

        assert found is not None
        assert [entry.position for entry in found.entries] == [0, 1, 2]

    async def test_get_with_entries_eager_loads_each_stock(
        self, db_session: AsyncSession
    ) -> None:
        """`WatchlistDetailOut` reads `entry.stock`; a lazy load would raise under asyncio."""
        user = await UserFactory().create(db_session)
        watchlist = await WatchlistFactory().create(db_session, user=user)
        first = await StockFactory().create(db_session, ticker_symbol="AAA")
        second = await StockFactory().create(db_session, ticker_symbol="BBB")
        await WatchlistDataFactory().create(
            db_session, watchlist=watchlist, stock=second, position=1
        )
        await WatchlistDataFactory().create(
            db_session, watchlist=watchlist, stock=first, position=0
        )
        db_session.expunge_all()

        found = await repo.get_with_entries(db_session, watchlist.watchlist_id)

        assert found is not None
        assert [entry.stock.ticker_symbol for entry in found.entries] == ["AAA", "BBB"]

    async def test_get_with_entries_is_none_for_an_unknown_watchlist(
        self, db_session: AsyncSession
    ) -> None:
        assert await repo.get_with_entries(db_session, uuid.uuid4()) is None

    async def test_an_empty_watchlist_loads_an_empty_entry_list(
        self, db_session: AsyncSession
    ) -> None:
        user = await UserFactory().create(db_session)
        watchlist = await WatchlistFactory().create(db_session, user=user)
        db_session.expunge_all()

        found = await repo.get_with_entries(db_session, watchlist.watchlist_id)

        assert found is not None
        assert found.entries == []

    async def test_list_entries_is_also_position_ordered(
        self, db_session: AsyncSession
    ) -> None:
        _, watchlist, _ = await _watchlist_with_entries(db_session)
        db_session.expunge_all()

        entries = await repo.list_entries(db_session, watchlist.watchlist_id)

        assert [entry.position for entry in entries] == [0, 1, 2]


class TestEntries:
    async def test_get_entry_finds_the_pair(self, db_session: AsyncSession) -> None:
        _, watchlist, stocks = await _watchlist_with_entries(db_session)

        entry = await repo.get_entry(db_session, watchlist.watchlist_id, stocks[0].stock_id)

        assert entry is not None
        assert entry.position == 2

    async def test_get_entry_is_none_for_a_stock_not_on_the_list(
        self, db_session: AsyncSession
    ) -> None:
        _, watchlist, _ = await _watchlist_with_entries(db_session)
        other = await StockFactory().create(db_session)

        assert await repo.get_entry(db_session, watchlist.watchlist_id, other.stock_id) is None

    async def test_entry_exists_is_the_duplicate_check(self, db_session: AsyncSession) -> None:
        _, watchlist, stocks = await _watchlist_with_entries(db_session)
        other = await StockFactory().create(db_session)

        assert await repo.entry_exists(db_session, watchlist.watchlist_id, stocks[0].stock_id)
        assert not await repo.entry_exists(db_session, watchlist.watchlist_id, other.stock_id)

    async def test_the_same_stock_is_not_scoped_across_watchlists(
        self, db_session: AsyncSession
    ) -> None:
        """A stock on one list must not read as already on another."""
        user = await UserFactory().create(db_session)
        stock = await StockFactory().create(db_session)
        first = await WatchlistFactory().create(db_session, user=user)
        second = await WatchlistFactory().create(db_session, user=user)
        await WatchlistDataFactory().create(
            db_session, watchlist=first, stock=stock, position=0
        )

        assert await repo.entry_exists(db_session, first.watchlist_id, stock.stock_id)
        assert not await repo.entry_exists(db_session, second.watchlist_id, stock.stock_id)

    async def test_add_entry_puts_a_stock_on_the_list(self, db_session: AsyncSession) -> None:
        user = await UserFactory().create(db_session)
        watchlist = await WatchlistFactory().create(db_session, user=user)
        stock = await StockFactory().create(db_session)

        entry = await repo.add_entry(
            db_session, watchlist_id=watchlist.watchlist_id, stock_id=stock.stock_id, position=0
        )

        assert entry.position == 0
        assert await repo.count_entries(db_session, watchlist.watchlist_id) == 1

    async def test_adding_the_same_stock_twice_raises_at_the_flush(
        self, db_session: AsyncSession
    ) -> None:
        """ANV-7's real composite key; the old ORM-only key allowed the duplicate."""
        _, watchlist, stocks = await _watchlist_with_entries(db_session)

        with pytest.raises(IntegrityError, match="pk_watchlist_data"):
            await repo.add_entry(
                db_session,
                watchlist_id=watchlist.watchlist_id,
                stock_id=stocks[0].stock_id,
                position=9,
            )

    async def test_remove_entry_reports_what_it_removed(self, db_session: AsyncSession) -> None:
        _, watchlist, stocks = await _watchlist_with_entries(db_session)

        removed = await repo.remove_entry(
            db_session, watchlist.watchlist_id, stocks[0].stock_id
        )

        assert removed is True
        assert await repo.count_entries(db_session, watchlist.watchlist_id) == 2

    async def test_removing_an_absent_entry_is_false_not_an_error(
        self, db_session: AsyncSession
    ) -> None:
        """A repo reports the fact; whether that is a 404 is ANV-15's judgement."""
        _, watchlist, _ = await _watchlist_with_entries(db_session)
        other = await StockFactory().create(db_session)

        assert await repo.remove_entry(db_session, watchlist.watchlist_id, other.stock_id) is False

    async def test_remove_entry_leaves_the_stock_itself_alone(
        self, db_session: AsyncSession
    ) -> None:
        _, watchlist, stocks = await _watchlist_with_entries(db_session)

        await repo.remove_entry(db_session, watchlist.watchlist_id, stocks[0].stock_id)

        assert await StockRepo().get_by_id(db_session, stocks[0].stock_id) is not None

    async def test_count_entries_and_max_position(self, db_session: AsyncSession) -> None:
        _, watchlist, _ = await _watchlist_with_entries(db_session)

        assert await repo.count_entries(db_session, watchlist.watchlist_id) == 3
        assert await repo.max_position(db_session, watchlist.watchlist_id) == 2

    async def test_max_position_is_none_on_an_empty_watchlist(
        self, db_session: AsyncSession
    ) -> None:
        """`None`, not `-1`: the 0-based append rule belongs in `app/domain/`."""
        user = await UserFactory().create(db_session)
        watchlist = await WatchlistFactory().create(db_session, user=user)

        assert await repo.max_position(db_session, watchlist.watchlist_id) is None


class TestRepositioning:
    async def test_set_position_moves_one_entry(self, db_session: AsyncSession) -> None:
        _, watchlist, stocks = await _watchlist_with_entries(db_session)

        moved = await repo.set_position(
            db_session, watchlist.watchlist_id, stocks[0].stock_id, 5
        )

        assert moved is not None
        assert moved.position == 5
        entries = await repo.list_entries(db_session, watchlist.watchlist_id)
        assert [entry.position for entry in entries] == [0, 1, 5]

    async def test_set_position_is_none_for_an_entry_that_is_not_there(
        self, db_session: AsyncSession
    ) -> None:
        _, watchlist, _ = await _watchlist_with_entries(db_session)
        other = await StockFactory().create(db_session)

        moved = await repo.set_position(db_session, watchlist.watchlist_id, other.stock_id, 0)

        assert moved is None

    async def test_set_positions_applies_a_whole_reorder(
        self, db_session: AsyncSession
    ) -> None:
        """The write half of ANV-15: the pure rule computes the map, this applies it."""
        _, watchlist, stocks = await _watchlist_with_entries(db_session, positions=(0, 1, 2))

        changed = await repo.set_positions(
            db_session,
            watchlist.watchlist_id,
            {stocks[0].stock_id: 2, stocks[1].stock_id: 0, stocks[2].stock_id: 1},
        )
        db_session.expunge_all()

        assert changed == 3
        entries = await repo.list_entries(db_session, watchlist.watchlist_id)
        assert [entry.stock_id for entry in entries] == [
            stocks[1].stock_id,
            stocks[2].stock_id,
            stocks[0].stock_id,
        ]

    async def test_a_swap_is_legal_because_position_is_not_unique(
        self, db_session: AsyncSession
    ) -> None:
        """ANV-7 left `position` non-unique precisely so an intermediate state is allowed."""
        _, watchlist, stocks = await _watchlist_with_entries(db_session, positions=(0, 1))

        changed = await repo.set_positions(
            db_session,
            watchlist.watchlist_id,
            {stocks[0].stock_id: 1, stocks[1].stock_id: 0},
        )

        assert changed == 2

    async def test_set_positions_counts_only_what_actually_moved(
        self, db_session: AsyncSession
    ) -> None:
        _, watchlist, stocks = await _watchlist_with_entries(db_session, positions=(0, 1, 2))

        changed = await repo.set_positions(
            db_session,
            watchlist.watchlist_id,
            {stocks[0].stock_id: 0, stocks[1].stock_id: 2, stocks[2].stock_id: 1},
        )

        assert changed == 2

    async def test_set_positions_ignores_stocks_not_on_the_watchlist(
        self, db_session: AsyncSession
    ) -> None:
        _, watchlist, stocks = await _watchlist_with_entries(db_session, positions=(0, 1))
        stranger = await StockFactory().create(db_session)

        changed = await repo.set_positions(
            db_session,
            watchlist.watchlist_id,
            {stocks[0].stock_id: 1, stranger.stock_id: 0},
        )

        assert changed == 1

    async def test_an_empty_map_is_a_no_op(self, db_session: AsyncSession) -> None:
        _, watchlist, _ = await _watchlist_with_entries(db_session)

        assert await repo.set_positions(db_session, watchlist.watchlist_id, {}) == 0
