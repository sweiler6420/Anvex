"""``StockDataService`` against real Postgres, over seeded candles.

``tests/unit/test_services_stock_data.py`` covers the branches against fakes; this module
covers the claims only a database can support, and they are the ones the charts depend on:

* **The ordering is the database's, not the fake's.** ``date``, then ``time``, then ``id`` —
  and the fixture below deliberately inserts a stock's candles *out of order*, so a service
  relying on insertion order fails here.
* **The inclusive bounds are inclusive in SQL**, at the exact endpoints, across a day
  boundary and within a single trading day.
* **The five ``NUMERIC(12, 4)`` prices survive the round trip as ``Decimal``**, trailing zero
  and all. That is a driver and column fact: a fake holding a ``Decimal`` proves nothing
  about what asyncpg gives back.
* **The combined ``datetime`` is built from the two stored columns** and is naive, which is
  what the ingest and the charts both assume.

``StockDataFactory`` marches ``(date, time)`` forward in five-minute steps from 09:30 and
rolls onto the next day, so a run of candles for one stock is a genuine intraday series with
no collisions on the ``(stock_id, date, time)`` unique constraint.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import NotFoundError, ValidationError
from app.models import Stock
from app.repos.stock import StockRepo
from app.repos.stock_data import StockDataRepo
from app.schemas.pagination import DEFAULT_PAGE_LIMIT
from app.services.stock_data import StockDataService
from app.settings import Settings
from tests.factories import StockDataFactory, StockFactory

DAY_ONE = dt.date(2026, 3, 2)
DAY_TWO = dt.date(2026, 3, 3)
DAY_THREE = dt.date(2026, 3, 4)
DAYS = (DAY_ONE, DAY_TWO, DAY_THREE)

OPEN_BELL = dt.time(9, 30)
MIDDAY = dt.time(12, 0)
CLOSING_AUCTION = dt.time(15, 55)
TIMES = (OPEN_BELL, MIDDAY, CLOSING_AUCTION)


def build_service(session: AsyncSession) -> StockDataService:
    """The real service over the real repos — only the session comes from the harness."""
    return StockDataService(
        session,
        Settings(jwt_secret_key="integration-test-jwt-secret"),
        stocks=StockRepo(),
        candles=StockDataRepo(),
    )


@pytest.fixture
async def stock(db_session: AsyncSession) -> Stock:
    return await StockFactory().create(db_session, ticker_symbol="ANVX")


@pytest.fixture
async def series(db_session: AsyncSession, stock: Stock) -> list[dt.datetime]:
    """Three candles on each of three consecutive days, **inserted out of order**.

    Nine rows whose natural chronological order is nothing like their insertion order, so
    "the service returns them oldest first" is a claim about the ``ORDER BY`` rather than
    about how the fixture happened to write them.
    """
    stamps: list[dt.datetime] = []
    for time in reversed(TIMES):
        for date in reversed(DAYS):
            await StockDataFactory().create(db_session, stock=stock, date=date, time=time)
            stamps.append(dt.datetime.combine(date, time))
    return sorted(stamps)


class TestOrdering:
    async def test_the_series_comes_back_oldest_first(
        self, db_session: AsyncSession, stock: Stock, series: list[dt.datetime]
    ) -> None:
        page = await build_service(db_session).list_for_stock(stock_id=stock.stock_id)

        assert page.total == 9
        assert [point.datetime for point in page.items] == series

    async def test_paging_walks_the_series_without_repeating_or_skipping(
        self, db_session: AsyncSession, stock: Stock, series: list[dt.datetime]
    ) -> None:
        """The ``id`` tie-breaker earning its place: three windows, nine distinct candles."""
        service = build_service(db_session)
        seen: list[dt.datetime] = []

        for offset in (0, 3, 6):
            page = await service.list_for_stock(
                stock_id=stock.stock_id, limit=3, offset=offset
            )
            seen.extend(point.datetime for point in page.items)

        assert seen == series

    async def test_another_stocks_candles_are_never_included(
        self, db_session: AsyncSession, stock: Stock, series: list[dt.datetime]
    ) -> None:
        other = await StockFactory().create(db_session)
        for date in DAYS:
            await StockDataFactory().create(db_session, stock=other, date=date, time=MIDDAY)

        page = await build_service(db_session).list_for_stock(stock_id=other.stock_id)

        assert page.total == 3
        assert {point.stock_id for point in page.items} == {other.stock_id}


class TestDateRange:
    async def test_the_bounds_are_inclusive_at_both_endpoints(
        self, db_session: AsyncSession, stock: Stock, series: list[dt.datetime]
    ) -> None:
        page = await build_service(db_session).list_for_stock(
            stock_id=stock.stock_id, start=DAY_ONE, end=DAY_THREE
        )

        assert page.total == 9

    async def test_a_narrower_range_excludes_the_days_outside_it(
        self, db_session: AsyncSession, stock: Stock, series: list[dt.datetime]
    ) -> None:
        page = await build_service(db_session).list_for_stock(
            stock_id=stock.stock_id, start=DAY_TWO, end=DAY_TWO
        )

        assert page.total == 3
        assert {point.datetime.date() for point in page.items} == {DAY_TWO}

    async def test_a_single_day_keeps_every_candle_in_that_day(
        self, db_session: AsyncSession, stock: Stock, series: list[dt.datetime]
    ) -> None:
        """The bound is on ``date``, so the whole trading day comes back, not one candle."""
        page = await build_service(db_session).list_for_stock(
            stock_id=stock.stock_id, start=DAY_ONE, end=DAY_ONE
        )

        assert [point.datetime.time() for point in page.items] == list(TIMES)

    async def test_an_open_end_means_everything_since(
        self, db_session: AsyncSession, stock: Stock, series: list[dt.datetime]
    ) -> None:
        page = await build_service(db_session).list_for_stock(
            stock_id=stock.stock_id, start=DAY_TWO
        )

        assert page.total == 6

    async def test_an_open_start_means_everything_until(
        self, db_session: AsyncSession, stock: Stock, series: list[dt.datetime]
    ) -> None:
        page = await build_service(db_session).list_for_stock(
            stock_id=stock.stock_id, end=DAY_TWO
        )

        assert page.total == 6

    async def test_a_range_before_the_series_is_an_empty_page(
        self, db_session: AsyncSession, stock: Stock, series: list[dt.datetime]
    ) -> None:
        page = await build_service(db_session).list_for_stock(
            stock_id=stock.stock_id, start=dt.date(2020, 1, 1), end=dt.date(2020, 12, 31)
        )

        assert page.items == []
        assert page.total == 0

    async def test_an_inverted_range_never_reaches_postgres(
        self, db_session: AsyncSession, stock: Stock
    ) -> None:
        with pytest.raises(ValidationError):
            await build_service(db_session).list_for_stock(
                stock_id=stock.stock_id, start=DAY_THREE, end=DAY_ONE
            )


class TestResolvingTheStock:
    async def test_an_unknown_stock_id_is_not_found(self, db_session: AsyncSession) -> None:
        with pytest.raises(NotFoundError):
            await build_service(db_session).list_for_stock(stock_id=uuid.uuid4())

    async def test_a_real_stock_with_no_candles_is_an_empty_page(
        self, db_session: AsyncSession, stock: Stock
    ) -> None:
        """The distinction the repo cannot make, made against real rows."""
        page = await build_service(db_session).list_for_stock(stock_id=stock.stock_id)

        assert page.items == []
        assert page.total == 0
        assert page.limit == DEFAULT_PAGE_LIMIT

    async def test_a_ticker_resolves_in_any_casing(
        self, db_session: AsyncSession, stock: Stock, series: list[dt.datetime]
    ) -> None:
        """The stored spelling is exact and the unique index serves the lookup; the
        upper-casing is the service's, so this proves both halves at once."""
        page = await build_service(db_session).list_for_ticker(ticker="  anvx ")

        assert page.total == 9
        assert {point.stock_id for point in page.items} == {stock.stock_id}

    async def test_an_unknown_ticker_is_not_found(self, db_session: AsyncSession) -> None:
        with pytest.raises(NotFoundError) as raised:
            await build_service(db_session).list_for_ticker(ticker="nosuchticker")

        assert raised.value.details["identifier"] == "NOSUCHTICKER"


class TestTheRoundTrip:
    @pytest.fixture
    async def priced(self, db_session: AsyncSession, stock: Stock) -> Stock:
        """One candle whose prices exercise all four decimal places, trailing zero included."""
        await StockDataFactory().create(
            db_session,
            stock=stock,
            date=DAY_ONE,
            time=OPEN_BELL,
            open_price=Decimal("1234.0678"),
            high_price=Decimal("1235.5678"),
            low_price=Decimal("1233.5670"),
            close_price=Decimal("1234.5678"),
            volume=2_048,
        )
        return stock

    async def test_prices_come_back_as_decimals_with_four_places(
        self, db_session: AsyncSession, priced: Stock
    ) -> None:
        """A driver and column fact: ``NUMERIC(12, 4)`` through asyncpg, not a float."""
        page = await build_service(db_session).list_for_stock(stock_id=priced.stock_id)

        point = page.items[0]
        assert isinstance(point.close_price, Decimal)
        assert point.close_price == Decimal("1234.5678")
        assert str(point.low_price) == "1233.5670", "the trailing zero survives"
        assert point.volume == 2_048

    async def test_the_datetime_is_recombined_from_the_two_columns(
        self, db_session: AsyncSession, priced: Stock
    ) -> None:
        page = await build_service(db_session).list_for_stock(stock_id=priced.stock_id)

        assert page.items[0].datetime == dt.datetime.combine(DAY_ONE, OPEN_BELL)

    async def test_the_datetime_is_naive_out_of_postgres_too(
        self, db_session: AsyncSession, priced: Stock
    ) -> None:
        """``date`` is a ``DATE`` and ``time`` is a ``TIME`` without a zone, so nothing in
        the round trip can attach one. **Naive is correct here** — see
        ``app/schemas/stock_data.py``."""
        page = await build_service(db_session).list_for_stock(stock_id=priced.stock_id)

        assert page.items[0].datetime.tzinfo is None

    async def test_a_read_commits_nothing(
        self, db_session: AsyncSession, priced: Stock
    ) -> None:
        await build_service(db_session).list_for_stock(stock_id=priced.stock_id)

        assert db_session.in_transaction()
