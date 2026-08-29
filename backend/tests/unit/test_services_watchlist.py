"""``WatchlistService`` against in-memory repos: ownership, ordering, conflicts, commits.

The unit tier (``CLAUDE.md`` §6): the real service, real domain rules, real schemas, and
:class:`tests.helpers.FakeWatchlistRepo` in place of SQL. No fixtures, no Postgres, no skip.

**Ownership is what this module is for.** ``tests/unit/test_domain_watchlist.py`` proves the
ordinals are right; this proves the right person is moving them. Two accounts exist in every
test, and the second one is used to attack every use case in turn. Three properties are
asserted about each refusal, and only the first is obvious:

1. it is a ``NotFoundError``, never a ``ForbiddenError`` — a 403 confirms the id is real
   (``CLAUDE.md`` §4);
2. its ``details`` are **identical** to the refusal for an id that was never created, so the
   two cannot be told apart by a client;
3. it reaches the repo's ownership lookup and **stops** — ``list_entries``,
   ``get_with_entries`` and every write stay unrecorded in ``FakeWatchlistRepo.calls``. That
   is the half a status-code assertion cannot see, and it is what makes the refusal free of
   a timing signal proportional to the size of a list the caller may not read.

The fake is deliberately unhelpful about ownership: its ``get_by_id`` filters on
``watchlist_id`` alone, exactly as the real query does, because ``CLAUDE.md`` §3 keeps
authorization out of the repo layer. A service that dropped the ``user_id`` comparison would
pass every other test in this file and fail only these.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from app.domain.errors import ConflictError, NotFoundError, ValidationError
from app.domain.watchlist import ENTRY_RESOURCE
from app.middleware.errors import status_for
from app.models.watchlist import DEFAULT_TITLE
from app.schemas.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT
from app.schemas.watchlist import (
    WatchlistCreate,
    WatchlistEntryCreate,
    WatchlistEntryUpdate,
)
from app.services.watchlist import (
    DUPLICATE_STOCK_MESSAGE,
    ENTRY_PRIMARY_KEY,
    RESOURCE,
    STOCK_RESOURCE,
    WatchlistService,
)
from app.settings import Settings
from tests.helpers import (
    FakeStockRepo,
    FakeWatchlistRepo,
    StubSession,
    make_entry,
    make_stock,
    make_user,
    make_watchlist,
)

#: An id no fixture ever creates — the "does not exist" half of the 404 comparison.
MISSING = uuid.UUID(int=404)


def settings() -> Settings:
    return Settings(jwt_secret_key="unit-test-jwt-secret")


class World:
    """Two accounts, four securities, and one watchlist per account.

    Owner's list holds three stocks in a known order; the intruder's holds one, so "the
    intruder has watchlists of their own" is never the reason an attack fails.
    """

    def __init__(self, *, stocked: bool = True) -> None:
        self.owner = make_user(username="stephen1", email="stephen@example.com")
        self.intruder = make_user(username="mallory1", email="mallory@example.com")

        self.apple = make_stock(ticker_symbol="AAPL", company="Apple Inc.")
        self.nvidia = make_stock(ticker_symbol="NVDA", company="NVIDIA Corp.")
        self.tesla = make_stock(ticker_symbol="TSLA", company="Tesla Inc.")
        self.quiet = make_stock(ticker_symbol="QUIET", company="Quiet Holdings Inc.")

        self.watchlist = make_watchlist(user_id=self.owner.user_id, title="Semis")
        self.other = make_watchlist(user_id=self.intruder.user_id, title="Theirs")

        entries = []
        if stocked:
            entries = [
                make_entry(watchlist_id=self.watchlist.watchlist_id, stock=stock, position=n)
                for n, stock in enumerate((self.apple, self.nvidia, self.tesla))
            ]
        entries.append(
            make_entry(watchlist_id=self.other.watchlist_id, stock=self.quiet, position=0)
        )

        self.session = StubSession()
        catalogue = (self.apple, self.nvidia, self.tesla, self.quiet)
        self.watchlists = FakeWatchlistRepo(
            self.watchlist, self.other, entries=entries, catalogue=catalogue
        )
        self.stocks = FakeStockRepo(*catalogue)
        self.service = WatchlistService(
            self.session,  # type: ignore[arg-type]
            settings(),
            watchlists=self.watchlists,  # type: ignore[arg-type]
            stocks=self.stocks,  # type: ignore[arg-type]
        )

    @property
    def watchlist_id(self) -> uuid.UUID:
        return self.watchlist.watchlist_id

    def tickers(self, detail: Any) -> list[str]:
        return [entry.stock.ticker_symbol for entry in detail.entries]

    def positions(self) -> dict[uuid.UUID, int]:
        return {
            entry.stock_id: entry.position
            for entry in self.watchlists._ordered(self.watchlist_id)
        }

    def methods_called(self) -> list[str]:
        return [name for name, _ in self.watchlists.calls]


@pytest.fixture
def world() -> World:
    return World()


@pytest.fixture
def empty_world() -> World:
    """The owner has a watchlist and has put nothing on it — the 200-with-`[]` case."""
    return World(stocked=False)


def duplicate_key_violation() -> IntegrityError:
    """What Postgres hands back when ``pk_watchlist_data`` rejects the insert.

    The constraint name arrives in the message text, which is the fallback
    ``WatchlistService._constraint_hint`` uses when the adapted DBAPI error carries no
    ``constraint_name`` attribute. That the *real* name matches is a claim only Postgres can
    settle, and ``tests/integration/test_services_watchlist.py`` settles it.
    """
    return IntegrityError(
        "INSERT INTO anvex.watchlist_data ...",
        {},
        Exception(f'duplicate key value violates unique constraint "{ENTRY_PRIMARY_KEY}"'),
    )


# ---------------------------------------------------------------------------------------
# create / list / read / delete
# ---------------------------------------------------------------------------------------


class TestCreate:
    async def test_the_owner_comes_from_the_token_not_the_body(self, world: World) -> None:
        """:class:`~app.schemas.watchlist.WatchlistCreate` has no ``user_id`` field at all,
        so there is nothing for a caller to put somebody else's account into."""
        created = await world.service.create(
            WatchlistCreate(title="Semis"), owner=world.owner
        )

        assert created.user_id == world.owner.user_id
        assert "user_id" not in WatchlistCreate.model_fields

    async def test_an_omitted_title_uses_the_default(self, world: World) -> None:
        created = await world.service.create(WatchlistCreate(), owner=world.owner)

        assert created.title == DEFAULT_TITLE

    async def test_it_commits(self, world: World) -> None:
        await world.service.create(WatchlistCreate(), owner=world.owner)

        assert (world.session.commits, world.session.rollbacks) == (1, 0)


class TestListMine:
    async def test_it_returns_only_the_callers_own(self, world: World) -> None:
        page = await world.service.list_mine(owner=world.owner)

        assert [row.watchlist_id for row in page.items] == [world.watchlist_id]
        assert page.total == 1

    async def test_the_other_account_sees_only_theirs(self, world: World) -> None:
        page = await world.service.list_mine(owner=world.intruder)

        assert [row.watchlist_id for row in page.items] == [world.other.watchlist_id]

    async def test_an_account_with_nothing_gets_an_empty_page_not_an_error(
        self, world: World
    ) -> None:
        stranger = make_user(username="nobody1", email="nobody@example.com")

        page = await world.service.list_mine(owner=stranger)

        assert (page.items, page.total, page.has_more) == ([], 0, False)

    async def test_the_window_is_resolved_before_it_reaches_the_repo(
        self, world: World
    ) -> None:
        """``resolve_window`` is ANV-14's, reused rather than re-derived: ``None`` becomes
        the default limit and a negative offset is floored for callers with no request to
        reject."""
        await world.service.list_mine(owner=world.owner, limit=None, offset=-5)

        assert world.watchlists.calls[-1] == (
            "list_for_user",
            {
                "user_id": world.owner.user_id,
                "limit": DEFAULT_PAGE_LIMIT,
                "offset": 0,
            },
        )

    async def test_an_over_large_limit_is_clamped_for_a_caller_with_no_request(
        self, world: World
    ) -> None:
        await world.service.list_mine(owner=world.owner, limit=10_000)

        assert world.watchlists.calls[-1][1]["limit"] == MAX_PAGE_LIMIT

    async def test_reading_a_list_does_not_commit(self, world: World) -> None:
        await world.service.list_mine(owner=world.owner)

        assert world.session.commits == 0


class TestGetWatchlist:
    async def test_the_stocks_come_back_in_position_order(self, world: World) -> None:
        detail = await world.service.get_watchlist(
            watchlist_id=world.watchlist_id, owner=world.owner
        )

        assert world.tickers(detail) == ["AAPL", "NVDA", "TSLA"]
        assert [entry.position for entry in detail.entries] == [0, 1, 2]

    async def test_an_empty_watchlist_is_a_successful_read_of_an_empty_list(
        self, empty_world: World
    ) -> None:
        """The bug this replaces: the old handler raised ``204 No Content`` **with** a
        ``detail`` body, which is not a valid HTTP response and made "you have not added
        anything yet" indistinguishable from an error at every client."""
        detail = await empty_world.service.get_watchlist(
            watchlist_id=empty_world.watchlist_id, owner=empty_world.owner
        )

        assert detail.entries == []
        assert detail.watchlist_id == empty_world.watchlist_id

    async def test_an_unknown_id_is_not_found(self, world: World) -> None:
        with pytest.raises(NotFoundError) as caught:
            await world.service.get_watchlist(watchlist_id=MISSING, owner=world.owner)

        assert caught.value.details == {"resource": RESOURCE, "identifier": str(MISSING)}

    async def test_it_reads_the_detail_through_the_eager_loading_query(
        self, world: World
    ) -> None:
        """``get_by_id`` does not load entries — under asyncio touching them would raise
        ``MissingGreenlet`` — so the detail must come from ``get_with_entries``."""
        await world.service.get_watchlist(
            watchlist_id=world.watchlist_id, owner=world.owner
        )

        assert "get_with_entries" in world.methods_called()


class TestDeleteWatchlist:
    async def test_it_removes_the_watchlist_and_its_entries(self, world: World) -> None:
        await world.service.delete_watchlist(
            watchlist_id=world.watchlist_id, owner=world.owner
        )

        assert world.watchlists.watchlists == [world.other]
        assert all(
            entry.watchlist_id != world.watchlist_id for entry in world.watchlists.entries
        )
        assert world.session.commits == 1

    async def test_an_unknown_id_is_not_found(self, world: World) -> None:
        with pytest.raises(NotFoundError):
            await world.service.delete_watchlist(watchlist_id=MISSING, owner=world.owner)


# ---------------------------------------------------------------------------------------
# add / remove / reorder
# ---------------------------------------------------------------------------------------


class TestAddStock:
    async def test_an_omitted_position_appends(self, world: World) -> None:
        entry = await world.service.add_stock(
            WatchlistEntryCreate(stock_id=world.quiet.stock_id),
            watchlist_id=world.watchlist_id,
            owner=world.owner,
        )

        assert entry.position == 3
        assert world.session.commits == 1

    async def test_appending_to_an_empty_watchlist_lands_at_zero(
        self, empty_world: World
    ) -> None:
        entry = await empty_world.service.add_stock(
            WatchlistEntryCreate(stock_id=empty_world.apple.stock_id),
            watchlist_id=empty_world.watchlist_id,
            owner=empty_world.owner,
        )

        assert entry.position == 0

    async def test_appending_to_a_single_stock_watchlist_does_not_collide(
        self, empty_world: World
    ) -> None:
        """The ``max_position == 0`` case. ``(max_position or -1) + 1`` would put the second
        stock at position 0 as well, and the ordering could never resolve the tie."""
        for stock in (empty_world.apple, empty_world.nvidia):
            await empty_world.service.add_stock(
                WatchlistEntryCreate(stock_id=stock.stock_id),
                watchlist_id=empty_world.watchlist_id,
                owner=empty_world.owner,
            )

        assert sorted(empty_world.positions().values()) == [0, 1]

    async def test_appending_asks_for_the_maximum_rather_than_the_whole_list(
        self, world: World
    ) -> None:
        """One scalar query, not a read of every entry: the common "watch this too" case
        does not need to see the list."""
        await world.service.add_stock(
            WatchlistEntryCreate(stock_id=world.quiet.stock_id),
            watchlist_id=world.watchlist_id,
            owner=world.owner,
        )

        assert "max_position" in world.methods_called()
        assert "list_entries" not in world.methods_called()

    async def test_an_explicit_position_inserts_and_shifts_the_rest_down(
        self, world: World
    ) -> None:
        await world.service.add_stock(
            WatchlistEntryCreate(stock_id=world.quiet.stock_id, position=1),
            watchlist_id=world.watchlist_id,
            owner=world.owner,
        )

        detail = await world.service.get_watchlist(
            watchlist_id=world.watchlist_id, owner=world.owner
        )
        assert world.tickers(detail) == ["AAPL", "QUIET", "NVDA", "TSLA"]
        assert [entry.position for entry in detail.entries] == [0, 1, 2, 3]

    async def test_inserting_at_the_length_appends(self, world: World) -> None:
        entry = await world.service.add_stock(
            WatchlistEntryCreate(stock_id=world.quiet.stock_id, position=3),
            watchlist_id=world.watchlist_id,
            owner=world.owner,
        )

        assert entry.position == 3

    async def test_a_position_past_the_length_is_refused(self, world: World) -> None:
        with pytest.raises(ValidationError) as caught:
            await world.service.add_stock(
                WatchlistEntryCreate(stock_id=world.quiet.stock_id, position=9),
                watchlist_id=world.watchlist_id,
                owner=world.owner,
            )

        assert status_for(caught.value) == 422
        assert world.session.commits == 0

    async def test_a_stock_already_on_the_list_is_a_conflict(self, world: World) -> None:
        with pytest.raises(ConflictError) as caught:
            await world.service.add_stock(
                WatchlistEntryCreate(stock_id=world.apple.stock_id),
                watchlist_id=world.watchlist_id,
                owner=world.owner,
            )

        assert status_for(caught.value) == 409
        assert caught.value.message == DUPLICATE_STOCK_MESSAGE
        assert caught.value.details["field"] == "stock_id"

    async def test_a_stock_that_does_not_exist_is_a_plain_404(self, world: World) -> None:
        """Reference data has no owner, so confirming a security exists gives nothing away —
        and refusing here keeps an unknown id from becoming a foreign-key 500."""
        with pytest.raises(NotFoundError) as caught:
            await world.service.add_stock(
                WatchlistEntryCreate(stock_id=MISSING),
                watchlist_id=world.watchlist_id,
                owner=world.owner,
            )

        assert caught.value.details["resource"] == STOCK_RESOURCE

    async def test_the_watchlist_is_resolved_before_the_stock_is(self, world: World) -> None:
        """Both are 404s, but only one of them is a secret. Asking about a security on a
        watchlist you may not see must not answer whether that security exists."""
        with pytest.raises(NotFoundError) as caught:
            await world.service.add_stock(
                WatchlistEntryCreate(stock_id=MISSING),
                watchlist_id=MISSING,
                owner=world.owner,
            )

        assert caught.value.details["resource"] == RESOURCE
        assert world.stocks.calls == []

    async def test_losing_the_race_is_the_same_conflict_the_pre_check_would_have_raised(
        self, world: World
    ) -> None:
        """Two requests can both pass ``entry_exists`` before either flushes. The composite
        primary key catches the loser; this makes it a 409 rather than a 500, and the two
        callers cannot tell which of them was second."""
        world.watchlists.add_entry_error = duplicate_key_violation()

        with pytest.raises(ConflictError) as caught:
            await world.service.add_stock(
                WatchlistEntryCreate(stock_id=world.quiet.stock_id),
                watchlist_id=world.watchlist_id,
                owner=world.owner,
            )

        assert caught.value.message == DUPLICATE_STOCK_MESSAGE
        assert caught.value.details["resource"] == ENTRY_RESOURCE

    async def test_the_race_path_rolls_back_before_anything_else(self, world: World) -> None:
        """Postgres aborts the whole transaction on a constraint violation and refuses every
        later statement in it, so the rollback has to come first (``CLAUDE.md`` §4)."""
        world.watchlists.add_entry_error = duplicate_key_violation()

        with pytest.raises(ConflictError):
            await world.service.add_stock(
                WatchlistEntryCreate(stock_id=world.quiet.stock_id),
                watchlist_id=world.watchlist_id,
                owner=world.owner,
            )

        assert (world.session.rollbacks, world.session.commits) == (1, 0)

    async def test_an_unrecognised_integrity_error_is_re_raised_untouched(
        self, world: World
    ) -> None:
        """That one really is a bug, and a bug should be a 500 with a logged traceback —
        not a 409 that sends the client off to fix something that is not wrong."""
        world.watchlists.add_entry_error = IntegrityError(
            "INSERT INTO anvex.watchlist_data ...",
            {},
            Exception('violates foreign key constraint "fk_watchlist_data_stock_id_stocks"'),
        )

        with pytest.raises(IntegrityError):
            await world.service.add_stock(
                WatchlistEntryCreate(stock_id=world.quiet.stock_id),
                watchlist_id=world.watchlist_id,
                owner=world.owner,
            )

        assert world.session.rollbacks == 1


class TestRemoveStock:
    async def test_it_takes_the_stock_off_and_closes_the_gap(self, world: World) -> None:
        await world.service.remove_stock(
            watchlist_id=world.watchlist_id,
            stock_id=world.nvidia.stock_id,
            owner=world.owner,
        )

        detail = await world.service.get_watchlist(
            watchlist_id=world.watchlist_id, owner=world.owner
        )
        assert world.tickers(detail) == ["AAPL", "TSLA"]
        assert [entry.position for entry in detail.entries] == [0, 1]

    async def test_removing_the_last_remaining_stock_empties_the_list(
        self, empty_world: World
    ) -> None:
        await empty_world.service.add_stock(
            WatchlistEntryCreate(stock_id=empty_world.apple.stock_id),
            watchlist_id=empty_world.watchlist_id,
            owner=empty_world.owner,
        )

        await empty_world.service.remove_stock(
            watchlist_id=empty_world.watchlist_id,
            stock_id=empty_world.apple.stock_id,
            owner=empty_world.owner,
        )

        assert empty_world.positions() == {}

    async def test_a_stock_that_is_not_on_the_list_is_not_found(self, world: World) -> None:
        with pytest.raises(NotFoundError) as caught:
            await world.service.remove_stock(
                watchlist_id=world.watchlist_id,
                stock_id=world.quiet.stock_id,
                owner=world.owner,
            )

        assert caught.value.details["resource"] == ENTRY_RESOURCE
        assert world.session.commits == 0

    async def test_it_commits(self, world: World) -> None:
        await world.service.remove_stock(
            watchlist_id=world.watchlist_id,
            stock_id=world.apple.stock_id,
            owner=world.owner,
        )

        assert world.session.commits == 1


class TestReorderStock:
    async def test_moving_the_last_stock_to_the_front(self, world: World) -> None:
        detail = await world.service.reorder_stock(
            WatchlistEntryUpdate(position=0),
            watchlist_id=world.watchlist_id,
            stock_id=world.tesla.stock_id,
            owner=world.owner,
        )

        assert world.tickers(detail) == ["TSLA", "AAPL", "NVDA"]

    async def test_moving_the_first_stock_to_the_back(self, world: World) -> None:
        detail = await world.service.reorder_stock(
            WatchlistEntryUpdate(position=2),
            watchlist_id=world.watchlist_id,
            stock_id=world.apple.stock_id,
            owner=world.owner,
        )

        assert world.tickers(detail) == ["NVDA", "TSLA", "AAPL"]

    async def test_the_stock_that_moves_is_the_one_named_not_the_one_at_an_index(
        self, world: World
    ) -> None:
        """The defect this ticket exists to fix. The old handler accepted ``stock_id`` and
        then ignored it, moving whichever row sat at the client's ``current_index`` — so a
        client one drag behind silently reordered a different stock. Here the only thing
        that decides what moves is the ``stock_id``.
        """
        detail = await world.service.reorder_stock(
            WatchlistEntryUpdate(position=0),
            watchlist_id=world.watchlist_id,
            stock_id=world.nvidia.stock_id,
            owner=world.owner,
        )

        assert world.tickers(detail) == ["NVDA", "AAPL", "TSLA"]

    async def test_moving_a_stock_to_where_it_already_is_rewrites_nothing(
        self, world: World
    ) -> None:
        before = world.positions()

        await world.service.reorder_stock(
            WatchlistEntryUpdate(position=1),
            watchlist_id=world.watchlist_id,
            stock_id=world.nvidia.stock_id,
            owner=world.owner,
        )

        assert world.positions() == before

    async def test_it_returns_the_whole_list_in_its_new_order(self, world: World) -> None:
        """Same shape as ``get_watchlist``, so one client-side reducer handles both."""
        detail = await world.service.reorder_stock(
            WatchlistEntryUpdate(position=0),
            watchlist_id=world.watchlist_id,
            stock_id=world.tesla.stock_id,
            owner=world.owner,
        )

        assert [entry.position for entry in detail.entries] == [0, 1, 2]
        assert detail.watchlist_id == world.watchlist_id

    async def test_a_position_past_the_end_is_refused(self, world: World) -> None:
        with pytest.raises(ValidationError) as caught:
            await world.service.reorder_stock(
                WatchlistEntryUpdate(position=3),
                watchlist_id=world.watchlist_id,
                stock_id=world.apple.stock_id,
                owner=world.owner,
            )

        assert status_for(caught.value) == 422
        assert world.session.commits == 0

    async def test_a_stock_that_is_not_on_the_list_is_not_found(self, world: World) -> None:
        with pytest.raises(NotFoundError) as caught:
            await world.service.reorder_stock(
                WatchlistEntryUpdate(position=0),
                watchlist_id=world.watchlist_id,
                stock_id=world.quiet.stock_id,
                owner=world.owner,
            )

        assert caught.value.details["resource"] == ENTRY_RESOURCE

    async def test_it_repairs_untidy_positions_on_the_way_through(
        self, empty_world: World
    ) -> None:
        """Ordinals with gaps are a state the database can genuinely be in — ``position``
        carries no unique constraint and nothing renumbers behind a caller's back. A reorder
        must renumber the whole list, not patch the rows it thinks moved."""
        watchlist_id = empty_world.watchlist_id
        for stock, position in (
            (empty_world.apple, 5),
            (empty_world.nvidia, 9),
            (empty_world.tesla, 40),
        ):
            empty_world.watchlists.entries.append(
                make_entry(watchlist_id=watchlist_id, stock=stock, position=position)
            )

        detail = await empty_world.service.reorder_stock(
            WatchlistEntryUpdate(position=0),
            watchlist_id=watchlist_id,
            stock_id=empty_world.tesla.stock_id,
            owner=empty_world.owner,
        )

        assert empty_world.tickers(detail) == ["TSLA", "AAPL", "NVDA"]
        assert [entry.position for entry in detail.entries] == [0, 1, 2]


# ---------------------------------------------------------------------------------------
# ownership — the point of the ticket
# ---------------------------------------------------------------------------------------


class TestOwnershipIsolation:
    """Every use case, attacked by the second account.

    Parameterised over the whole surface rather than written out per method, because the
    defect being fixed was precisely that *some* handlers checked and others did not: a
    per-method test that somebody forgets to add for the eighth route reproduces the bug.
    Adding a use case without adding it here leaves it unlisted, which
    :meth:`test_every_use_case_is_covered_by_the_isolation_sweep` turns into a failure.
    """

    @staticmethod
    def attacks(world: World, watchlist_id: uuid.UUID) -> dict[str, Any]:
        """One coroutine factory per use case that takes a ``watchlist_id``."""
        return {
            "get_watchlist": lambda user: world.service.get_watchlist(
                watchlist_id=watchlist_id, owner=user
            ),
            "delete_watchlist": lambda user: world.service.delete_watchlist(
                watchlist_id=watchlist_id, owner=user
            ),
            "add_stock": lambda user: world.service.add_stock(
                WatchlistEntryCreate(stock_id=world.quiet.stock_id),
                watchlist_id=watchlist_id,
                owner=user,
            ),
            "remove_stock": lambda user: world.service.remove_stock(
                watchlist_id=watchlist_id,
                stock_id=world.apple.stock_id,
                owner=user,
            ),
            "reorder_stock": lambda user: world.service.reorder_stock(
                WatchlistEntryUpdate(position=0),
                watchlist_id=watchlist_id,
                stock_id=world.apple.stock_id,
                owner=user,
            ),
        }

    USE_CASES = ("get_watchlist", "delete_watchlist", "add_stock", "remove_stock", "reorder_stock")

    @pytest.mark.parametrize("use_case", USE_CASES)
    async def test_another_account_is_refused(self, world: World, use_case: str) -> None:
        attack = self.attacks(world, world.watchlist_id)[use_case]

        with pytest.raises(NotFoundError) as caught:
            await attack(world.intruder)

        assert status_for(caught.value) == 404

    @pytest.mark.parametrize("use_case", USE_CASES)
    async def test_the_refusal_is_identical_to_one_for_an_id_that_never_existed(
        self, world: World, use_case: str
    ) -> None:
        """The whole point. A 403 — or a differently-worded 404 — would tell an attacker
        which watchlist ids are real, which is the half of the information worth
        protecting."""
        with pytest.raises(NotFoundError) as trespass:
            await self.attacks(world, world.watchlist_id)[use_case](world.intruder)
        with pytest.raises(NotFoundError) as absent:
            await self.attacks(world, MISSING)[use_case](world.intruder)

        assert trespass.value.code == absent.value.code
        assert set(trespass.value.details) == set(absent.value.details)
        assert trespass.value.details["resource"] == absent.value.details["resource"]
        # Only the identifier differs, and it is the one the caller supplied.
        assert trespass.value.details["identifier"] == str(world.watchlist_id)
        assert absent.value.details["identifier"] == str(MISSING)

    @pytest.mark.parametrize("use_case", USE_CASES)
    async def test_the_refusal_never_touches_the_entries(
        self, world: World, use_case: str
    ) -> None:
        """Raised before any query on the child, so response *time* does not answer the
        question either — and so nothing about a list the caller may not see is loaded."""
        with pytest.raises(NotFoundError):
            await self.attacks(world, world.watchlist_id)[use_case](world.intruder)

        assert world.methods_called() == ["get_by_id"]

    @pytest.mark.parametrize("use_case", USE_CASES)
    async def test_the_refusal_changes_nothing(self, world: World, use_case: str) -> None:
        before = world.positions()

        with pytest.raises(NotFoundError):
            await self.attacks(world, world.watchlist_id)[use_case](world.intruder)

        assert world.positions() == before
        assert world.watchlists.watchlists == [world.watchlist, world.other]
        assert (world.session.commits, world.session.rollbacks) == (0, 0)

    @pytest.mark.parametrize("use_case", USE_CASES)
    async def test_the_owner_is_not_refused(self, world: World, use_case: str) -> None:
        """The control. Without it every assertion above would pass on a service that
        refused everybody."""
        await self.attacks(world, world.watchlist_id)[use_case](world.owner)

    def test_every_use_case_is_covered_by_the_isolation_sweep(self) -> None:
        """The sweep is only as good as its list, so the list is derived from the service.

        ``list_mine`` and ``create`` are exempt and named here rather than silently missing:
        neither takes a ``watchlist_id``, so neither has a cross-account case to leak —
        ``create`` writes to the caller's own account and ``list_mine`` *is* the caller's
        own collection.
        """
        exempt = {"create", "list_mine"}
        public = {
            name
            for name in vars(WatchlistService)
            if not name.startswith("_") and callable(getattr(WatchlistService, name))
        }

        assert public - exempt == set(self.USE_CASES)


# ---------------------------------------------------------------------------------------
# vocabulary shared with the rest of the API
# ---------------------------------------------------------------------------------------


def test_the_stock_resource_noun_matches_the_other_services() -> None:
    """A client branching on ``details["resource"]`` must see one spelling of "stock" no
    matter which endpoint refused it."""
    from app.services.stock import RESOURCE as stock_resource
    from app.services.stock_data import RESOURCE as stock_data_resource

    assert STOCK_RESOURCE == stock_resource == stock_data_resource
