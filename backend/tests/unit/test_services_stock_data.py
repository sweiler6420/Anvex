"""Unit tests for ``app/services/stock_data.py`` — the service's own logic, no database.

The fast tier (``CLAUDE.md`` §6): :class:`tests.helpers.FakeStockRepo` and
:class:`tests.helpers.FakeStockDataRepo` stand in for the repos, so every branch below runs
with Docker stopped. What is *not* asserted here is SQL — that the ``date``/``time`` ordering
is stable across pages and that the inclusive bounds really are inclusive in Postgres are
database guarantees, proved in ``tests/integration/``.

Five properties are being pinned, and they are different properties:

1. **An unknown parent is a 404; an empty range is a 200.** The repo cannot tell those apart
   — ``list_for_stock`` on a nonexistent id returns ``([], 0)`` exactly as a quiet week does
   — so the service resolves the stock first. :class:`FakeStockDataRepo` is faithful to that
   ambiguity on purpose, which is what makes a service that skipped the lookup fail here.
2. **The service builds the envelope, and builds it honestly.** ``total`` is counted before
   the window, so an ``offset`` past the end is an empty page with a truthful total.
3. **The domain rules are actually applied**, at the boundary: the resolved limit and the
   inclusive date bounds are asserted on what reached the repo, not merely on what came back.
4. **Ticker normalisation is the service's job.** ``FakeStockRepo.get_by_ticker`` is exact and
   case-sensitive, like the real one, so a service that forgot to upper-case fails.
5. **The wire shape is** :meth:`~app.schemas.stock_data.StockDataPoint.from_row` **'s** — one
   naive ``datetime`` recombined from ``date`` and ``time``, and ``Decimal`` prices. The
   service calls it rather than re-deriving the recombination.
"""

from __future__ import annotations

import ast
import datetime as dt
import uuid
from decimal import Decimal
from pathlib import Path as FilePath
from typing import Any

import pytest

from app.domain.errors import AnvexError, NotFoundError, ValidationError
from app.schemas.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, Page
from app.schemas.stock_data import StockDataPoint
from app.services import stock as stock_service_module
from app.services import stock_data as stock_data_service_module
from app.services.stock_data import RESOURCE, StockDataService
from app.settings import Settings
from tests.helpers import (
    FakeStockDataRepo,
    FakeStockRepo,
    StubSession,
    make_candle,
    make_stock,
)

MONDAY = dt.date(2026, 1, 5)
TUESDAY = dt.date(2026, 1, 6)
WEDNESDAY = dt.date(2026, 1, 7)
THURSDAY = dt.date(2026, 1, 8)
FRIDAY = dt.date(2026, 1, 9)
WEEK = (MONDAY, TUESDAY, WEDNESDAY, THURSDAY, FRIDAY)

OPEN_BELL = dt.time(9, 30)


def build_settings(**overrides: Any) -> Settings:
    """Settings that ignore the developer's ``.env``. This service reads none of them."""
    values: dict[str, Any] = {"jwt_secret_key": "unit-test-jwt-secret"}
    values.update(overrides)
    return Settings(**values)


def build_service(
    *,
    stocks: FakeStockRepo | None = None,
    candles: FakeStockDataRepo | None = None,
) -> tuple[StockDataService, FakeStockRepo, FakeStockDataRepo, StubSession]:
    """A :class:`StockDataService` over in-memory repos and a counting session stub."""
    stock_repo = stocks if stocks is not None else FakeStockRepo()
    candle_repo = candles if candles is not None else FakeStockDataRepo()
    session = StubSession()
    service = StockDataService(
        session=session,  # type: ignore[arg-type]
        settings=build_settings(),
        stocks=stock_repo,  # type: ignore[arg-type]
        candles=candle_repo,  # type: ignore[arg-type]
    )
    return service, stock_repo, candle_repo, session


def one_a_day(stock_id: uuid.UUID, days: tuple[dt.date, ...] = WEEK) -> list[Any]:
    """One candle at the opening bell on each of ``days``."""
    return [
        make_candle(stock_id=stock_id, date=day, close=f"{100 + index}.2500")
        for index, day in enumerate(days)
    ]


def last_call(repo: FakeStockDataRepo) -> dict[str, Any]:
    """The keyword arguments the service passed to the candle repo."""
    return next(argument for name, argument in reversed(repo.calls) if name == "list_for_stock")


@pytest.fixture
def apple() -> Any:
    return make_stock(ticker_symbol="AAPL", company="Apple Inc.")


@pytest.fixture
def week(apple: Any) -> tuple[StockDataService, FakeStockRepo, FakeStockDataRepo, StubSession]:
    """One stock with a candle on each of five consecutive trading days."""
    return build_service(
        stocks=FakeStockRepo(apple),
        candles=FakeStockDataRepo(*one_a_day(apple.stock_id)),
    )


# ---------------------------------------------------------------------------------------
# the envelope
# ---------------------------------------------------------------------------------------


class TestEnvelope:
    async def test_it_returns_a_page_of_points(self, week: Any, apple: Any) -> None:
        service, _, _, _ = week

        page = await service.list_for_stock(stock_id=apple.stock_id)

        assert isinstance(page, Page)
        assert page.total == 5
        assert len(page.items) == 5
        assert all(isinstance(item, StockDataPoint) for item in page.items)

    async def test_the_bounds_are_echoed_back(self, week: Any, apple: Any) -> None:
        service, _, _, _ = week

        page = await service.list_for_stock(stock_id=apple.stock_id, limit=2, offset=1)

        assert page.limit == 2
        assert page.offset == 1

    async def test_has_more_is_computed_not_guessed(self, week: Any, apple: Any) -> None:
        service, _, _, _ = week

        first = await service.list_for_stock(stock_id=apple.stock_id, limit=2, offset=0)
        last = await service.list_for_stock(stock_id=apple.stock_id, limit=2, offset=4)

        assert first.has_more is True
        assert last.has_more is False

    async def test_an_offset_past_the_end_is_empty_with_a_truthful_total(
        self, week: Any, apple: Any
    ) -> None:
        """``total`` is counted before the window, so the collection's size is not a lie."""
        service, _, _, _ = week

        page = await service.list_for_stock(stock_id=apple.stock_id, offset=500)

        assert page.items == []
        assert page.total == 5
        assert page.has_more is False

    async def test_candles_come_back_oldest_first(self, week: Any, apple: Any) -> None:
        service, _, _, _ = week

        page = await service.list_for_stock(stock_id=apple.stock_id)

        assert [point.datetime.date() for point in page.items] == list(WEEK)

    async def test_paging_walks_the_series_without_repeating_a_candle(
        self, week: Any, apple: Any
    ) -> None:
        service, _, _, _ = week
        seen: list[dt.datetime] = []

        for offset in (0, 2, 4):
            page = await service.list_for_stock(stock_id=apple.stock_id, limit=2, offset=offset)
            seen.extend(point.datetime for point in page.items)

        assert len(seen) == len(set(seen)) == 5


# ---------------------------------------------------------------------------------------
# the date range
# ---------------------------------------------------------------------------------------


class TestDateRange:
    async def test_no_bounds_is_the_whole_series(self, week: Any, apple: Any) -> None:
        service, _, candles, _ = week

        page = await service.list_for_stock(stock_id=apple.stock_id)

        assert page.total == 5
        assert last_call(candles)["start"] is None
        assert last_call(candles)["end"] is None

    async def test_both_bounds_are_inclusive(self, week: Any, apple: Any) -> None:
        """Tuesday to Thursday is three candles, not one."""
        service, _, _, _ = week

        page = await service.list_for_stock(stock_id=apple.stock_id, start=TUESDAY, end=THURSDAY)

        assert page.total == 3
        assert [point.datetime.date() for point in page.items] == [
            TUESDAY,
            WEDNESDAY,
            THURSDAY,
        ]

    async def test_a_single_day_returns_that_day(self, week: Any, apple: Any) -> None:
        service, _, _, _ = week

        page = await service.list_for_stock(stock_id=apple.stock_id, start=WEDNESDAY, end=WEDNESDAY)

        assert page.total == 1
        assert page.items[0].datetime.date() == WEDNESDAY

    async def test_an_open_end_means_everything_since(self, week: Any, apple: Any) -> None:
        service, _, _, _ = week

        page = await service.list_for_stock(stock_id=apple.stock_id, start=THURSDAY)

        assert [point.datetime.date() for point in page.items] == [THURSDAY, FRIDAY]

    async def test_an_open_start_means_everything_until(self, week: Any, apple: Any) -> None:
        service, _, _, _ = week

        page = await service.list_for_stock(stock_id=apple.stock_id, end=TUESDAY)

        assert [point.datetime.date() for point in page.items] == [MONDAY, TUESDAY]

    async def test_a_range_with_no_candles_is_an_empty_page_not_an_error(
        self, week: Any, apple: Any
    ) -> None:
        """The rule the ticket turns on: a real stock with nothing in range is a 200."""
        service, _, _, _ = week

        page = await service.list_for_stock(
            stock_id=apple.stock_id, start=dt.date(2030, 1, 1), end=dt.date(2030, 1, 31)
        )

        assert page.items == []
        assert page.total == 0
        assert page.has_more is False

    async def test_an_inverted_range_is_refused(self, week: Any, apple: Any) -> None:
        service, _, candles, _ = week

        with pytest.raises(ValidationError) as raised:
            await service.list_for_stock(stock_id=apple.stock_id, start=FRIDAY, end=MONDAY)

        assert raised.value.field == "start"
        assert candles.calls == [], "nothing should have been queried"

    async def test_an_inverted_range_is_refused_before_the_stock_is_even_resolved(
        self, apple: Any
    ) -> None:
        """Input validation precedes existence: the caller hears about its own bug first,
        and an unknown id is not revealed as a side effect of a malformed request."""
        service, stocks, _, _ = build_service(stocks=FakeStockRepo(apple))

        with pytest.raises(ValidationError):
            await service.list_for_stock(stock_id=uuid.uuid4(), start=FRIDAY, end=MONDAY)

        assert stocks.calls == []

    async def test_the_bounds_reach_the_repo_unchanged(self, week: Any, apple: Any) -> None:
        """Pinned at the boundary, not just at the result — the repo is what applies them."""
        service, _, candles, _ = week

        await service.list_for_stock(stock_id=apple.stock_id, start=MONDAY, end=FRIDAY)

        call = last_call(candles)
        assert call["start"] == MONDAY
        assert call["end"] == FRIDAY

    async def test_a_datetime_bound_is_narrowed_before_it_reaches_the_repo(
        self, week: Any, apple: Any
    ) -> None:
        """The route cannot hand one over, but a Celery task holding ``datetime.now()`` can."""
        service, _, candles, _ = week

        await service.list_for_stock(stock_id=apple.stock_id, start=dt.datetime(2026, 1, 6, 16, 0))

        call = last_call(candles)
        assert call["start"] == TUESDAY
        assert type(call["start"]) is dt.date


# ---------------------------------------------------------------------------------------
# the window
# ---------------------------------------------------------------------------------------


class TestWindow:
    async def test_no_limit_asks_for_the_default_page(self, week: Any, apple: Any) -> None:
        service, _, candles, _ = week

        page = await service.list_for_stock(stock_id=apple.stock_id)

        assert last_call(candles)["limit"] == DEFAULT_PAGE_LIMIT
        assert page.limit == DEFAULT_PAGE_LIMIT

    async def test_an_over_large_limit_is_clamped_before_the_repo_sees_it(
        self, week: Any, apple: Any
    ) -> None:
        """Not cosmetic: ``Page.limit`` carries ``le=MAX_PAGE_LIMIT``, so an unclamped
        10,000 would fail the envelope's own validation and become a 500."""
        service, _, candles, _ = week

        page = await service.list_for_stock(stock_id=apple.stock_id, limit=10_000)

        assert last_call(candles)["limit"] == MAX_PAGE_LIMIT
        assert page.limit == MAX_PAGE_LIMIT

    async def test_a_negative_offset_becomes_the_first_page(self, week: Any, apple: Any) -> None:
        service, _, candles, _ = week

        page = await service.list_for_stock(stock_id=apple.stock_id, offset=-10)

        assert last_call(candles)["offset"] == 0
        assert page.offset == 0

    async def test_the_echoed_limit_is_the_one_that_was_queried(
        self, week: Any, apple: Any
    ) -> None:
        """One number: whatever the repo was asked for is what the envelope reports."""
        service, _, candles, _ = week

        page = await service.list_for_stock(stock_id=apple.stock_id, limit=3, offset=1)

        assert (page.limit, page.offset) == (3, 1)
        assert last_call(candles)["limit"] == 3
        assert last_call(candles)["offset"] == 1


# ---------------------------------------------------------------------------------------
# resolving the parent
# ---------------------------------------------------------------------------------------


class TestByStockId:
    async def test_an_unknown_stock_is_not_found(self, week: Any) -> None:
        """A sub-collection of a parent that does not exist is not an empty collection."""
        service, _, candles, _ = week
        missing = uuid.uuid4()

        with pytest.raises(NotFoundError) as raised:
            await service.list_for_stock(stock_id=missing)

        assert raised.value.details == {
            "resource": RESOURCE,
            "identifier": str(missing),
        }
        assert candles.calls == [], "the candle table is never touched for a missing stock"

    async def test_a_known_stock_with_no_candles_at_all_is_an_empty_page(self, apple: Any) -> None:
        """The other half of the same distinction, and the reason the fake is ambiguous."""
        service, _, _, _ = build_service(stocks=FakeStockRepo(apple))

        page = await service.list_for_stock(stock_id=apple.stock_id)

        assert page.items == []
        assert page.total == 0

    async def test_the_candles_of_another_stock_are_not_returned(self, apple: Any) -> None:
        other = make_stock(ticker_symbol="MSFT", company="Microsoft Corporation")
        service, _, _, _ = build_service(
            stocks=FakeStockRepo(apple, other),
            candles=FakeStockDataRepo(*one_a_day(apple.stock_id), *one_a_day(other.stock_id)),
        )

        page = await service.list_for_stock(stock_id=other.stock_id)

        assert page.total == 5
        assert {point.stock_id for point in page.items} == {other.stock_id}


class TestByTicker:
    async def test_it_resolves_the_symbol_and_returns_the_series(
        self, week: Any, apple: Any
    ) -> None:
        service, _, _, _ = week

        page = await service.list_for_ticker(ticker="AAPL")

        assert page.total == 5
        assert {point.stock_id for point in page.items} == {apple.stock_id}

    @pytest.mark.parametrize("given", ["aapl", "  AAPL ", "AaPl", "\taapl\n"])
    async def test_any_casing_or_padding_resolves(self, week: Any, given: str) -> None:
        service, _, _, _ = week

        page = await service.list_for_ticker(ticker=given)

        assert page.total == 5

    async def test_the_repo_is_asked_for_the_canonical_spelling(
        self, week: Any, apple: Any
    ) -> None:
        """Pinned at the boundary. ``FakeStockRepo.get_by_ticker`` is exact and
        case-sensitive, exactly like the real one, so a missing ``.upper()`` fails here."""
        service, stocks, _, _ = week

        await service.list_for_ticker(ticker="  aapl ")

        assert stocks.calls == [("get_by_ticker", "AAPL")]

    async def test_an_unknown_ticker_is_not_found(self, week: Any) -> None:
        service, _, candles, _ = week

        with pytest.raises(NotFoundError) as raised:
            await service.list_for_ticker(ticker="nope")

        assert candles.calls == []
        assert raised.value.details == {"resource": RESOURCE, "identifier": "NOPE"}

    async def test_the_error_reports_the_canonical_spelling(self, week: Any) -> None:
        """So a caller can see the lookup was not simply a casing mistake."""
        service, _, _, _ = week

        with pytest.raises(NotFoundError) as raised:
            await service.list_for_ticker(ticker=" nope ")

        assert "'NOPE'" in raised.value.message

    async def test_the_range_and_window_apply_by_ticker_too(self, week: Any) -> None:
        service, _, candles, _ = week

        page = await service.list_for_ticker(
            ticker="aapl", start=TUESDAY, end=THURSDAY, limit=2, offset=1
        )

        assert page.total == 3
        assert page.limit == 2
        assert [point.datetime.date() for point in page.items] == [WEDNESDAY, THURSDAY]
        assert last_call(candles)["start"] == TUESDAY

    async def test_an_inverted_range_is_refused_before_the_ticker_is_resolved(
        self, week: Any
    ) -> None:
        service, stocks, _, _ = week

        with pytest.raises(ValidationError):
            await service.list_for_ticker(ticker="AAPL", start=FRIDAY, end=MONDAY)

        assert stocks.calls == []

    async def test_it_queries_by_stock_id_not_by_a_second_ticker_join(
        self, week: Any, apple: Any
    ) -> None:
        """Once the stock is resolved we hold the leading column of the ``(stock_id, date,
        time)`` index; joining ``stocks`` again would be work for nothing. The fake has no
        ``list_for_ticker`` at all, so this is enforced rather than described."""
        service, _, candles, _ = week

        await service.list_for_ticker(ticker="AAPL")

        assert [name for name, _ in candles.calls] == ["list_for_stock"]
        assert last_call(candles)["stock_id"] == apple.stock_id


# ---------------------------------------------------------------------------------------
# the wire shape
# ---------------------------------------------------------------------------------------


class TestPointShape:
    async def test_the_datetime_recombines_the_two_columns(self, apple: Any) -> None:
        service, _, _, _ = build_service(
            stocks=FakeStockRepo(apple),
            candles=FakeStockDataRepo(
                make_candle(stock_id=apple.stock_id, date=MONDAY, time=dt.time(15, 55))
            ),
        )

        page = await service.list_for_stock(stock_id=apple.stock_id)

        assert page.items[0].datetime == dt.datetime(2026, 1, 5, 15, 55)

    async def test_the_datetime_is_naive_on_purpose(self, apple: Any) -> None:
        """``stock_data.time`` is the exchange's local wall clock and carries no zone, so
        stamping ``+00:00`` on a 09:30 New York open would move the candle by hours. This is
        the one datetime in the API without an offset. **Do not "fix" it** — attaching a real
        zone needs an exchange-to-timezone map that does not exist yet."""
        service, _, _, _ = build_service(
            stocks=FakeStockRepo(apple),
            candles=FakeStockDataRepo(
                make_candle(stock_id=apple.stock_id, date=MONDAY, time=OPEN_BELL)
            ),
        )

        page = await service.list_for_stock(stock_id=apple.stock_id)

        assert page.items[0].datetime.tzinfo is None

    async def test_prices_stay_decimal_all_the_way_out(self, apple: Any) -> None:
        """Never ``float``: the column is ``NUMERIC(12, 4)`` and a binary round trip loses
        the fourth decimal place."""
        service, _, _, _ = build_service(
            stocks=FakeStockRepo(apple),
            candles=FakeStockDataRepo(
                make_candle(stock_id=apple.stock_id, date=MONDAY, close="1234.5678")
            ),
        )

        page = await service.list_for_stock(stock_id=apple.stock_id)

        point = page.items[0]
        assert isinstance(point.close_price, Decimal)
        assert point.close_price == Decimal("1234.5678")
        assert point.open_price == Decimal("1234.0678")

    async def test_a_point_carries_the_five_numbers_the_stock_and_the_timestamp(
        self, apple: Any
    ) -> None:
        """No surrogate ``id``: a chart plots a series, and the row's key means nothing to it."""
        service, _, _, _ = build_service(
            stocks=FakeStockRepo(apple),
            candles=FakeStockDataRepo(make_candle(stock_id=apple.stock_id, date=MONDAY)),
        )

        page = await service.list_for_stock(stock_id=apple.stock_id)

        assert set(page.items[0].model_dump()) == {
            "stock_id",
            "datetime",
            "open_price",
            "high_price",
            "low_price",
            "close_price",
            "volume",
        }


# ---------------------------------------------------------------------------------------
# transaction and scope
# ---------------------------------------------------------------------------------------


class TestTransaction:
    async def test_a_read_commits_nothing(self, week: Any, apple: Any) -> None:
        """Nothing here writes, so the session is never asked to close a transaction."""
        service, _, _, session = week

        await service.list_for_stock(stock_id=apple.stock_id)
        await service.list_for_ticker(ticker="AAPL")

        assert session.commits == 0
        assert session.rollbacks == 0


class TestReadOnly:
    """ANV-14 is a read ticket; ANV-22's ingest is what writes a candle."""

    def test_the_service_exposes_no_write_use_case(self) -> None:
        public = {
            name
            for name in dir(StockDataService)
            if not name.startswith("_") and callable(getattr(StockDataService, name))
        }

        assert public == {"list_for_stock", "list_for_ticker"}


# ---------------------------------------------------------------------------------------
# layering
# ---------------------------------------------------------------------------------------


def service_tree() -> ast.Module:
    return ast.parse(FilePath(stock_data_service_module.__file__).read_text(encoding="utf-8"))


class TestLayering:
    """`CLAUDE.md` §3, checked rather than trusted — prose conventions get broken."""

    def test_the_service_imports_no_web_framework(self) -> None:
        modules: set[str] = set()
        for node in ast.walk(service_tree()):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                modules.add(node.module)
        roots = {module.split(".")[0] for module in modules}

        assert "fastapi" not in roots
        assert "starlette" not in roots
        assert "app.api" not in modules
        assert "app.deps" not in modules

    def test_it_reaches_the_ticker_rule_through_domain_not_sideways(self) -> None:
        """Two services sharing a rule import it downward, not from each other."""
        modules: set[str] = set()
        for node in ast.walk(service_tree()):
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                modules.add(node.module)

        assert "app.domain.stock" in modules
        assert not any(module.startswith("app.services.") for module in modules), modules

    def test_the_service_raises_no_http_exception(self) -> None:
        used = {
            node.id if isinstance(node, ast.Name) else node.attr
            for node in ast.walk(service_tree())
            if isinstance(node, ast.Name | ast.Attribute)
        }
        assert "HTTPException" not in used

    def test_every_error_it_raises_is_a_domain_error(self) -> None:
        raised = {
            node.exc.func.id
            for node in ast.walk(service_tree())
            if isinstance(node, ast.Raise)
            and isinstance(node.exc, ast.Call)
            and isinstance(node.exc.func, ast.Name)
        }
        assert raised
        for name in raised:
            assert issubclass(getattr(stock_data_service_module, name), AnvexError), name

    def test_no_sqlalchemy_query_is_written_here(self) -> None:
        """`CLAUDE.md` §3: if you typed `select(` outside `app/repos/`, it is the wrong file."""
        source = FilePath(stock_data_service_module.__file__).read_text(encoding="utf-8")
        assert "select(" not in source

    def test_the_timestamp_is_not_recombined_a_second_time(self) -> None:
        """ANV-8 owns ``date + time``; a copy here is the drift this test exists to stop."""
        source = FilePath(stock_data_service_module.__file__).read_text(encoding="utf-8")
        assert "from_row" in source
        assert "datetime.combine" not in source

    def test_the_resource_name_matches_the_stocks_service(self) -> None:
        """A 404 says ``resource: "stock"`` whichever endpoint refused, so a client
        branching on ``details["resource"]`` sees one spelling."""
        assert RESOURCE == stock_service_module.RESOURCE
