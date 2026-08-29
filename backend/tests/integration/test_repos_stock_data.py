"""``StockDataRepo`` against a real Postgres.

``TestBulkUpsert`` is the important class in this module. ANV-22's ingest is only safe to
retry because ``INSERT ... ON CONFLICT (stock_id, date, time) DO UPDATE`` cannot duplicate
a candle, and the way to know that is to run the same batch twice and count the rows.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.exc import DBAPIError, MissingGreenlet
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Stock
from app.repos import StockDataRepo
from tests.factories import StockDataFactory, StockFactory

repo = StockDataRepo()

DAY_ONE = dt.date(2026, 3, 2)
DAY_TWO = dt.date(2026, 3, 3)
DAY_THREE = dt.date(2026, 3, 4)
OPEN_BELL = dt.time(9, 30)


def _candle(stock: Stock, date: dt.date, time: dt.time, close: str) -> dict[str, Any]:
    """One row in the mapping shape :meth:`StockDataRepo.bulk_upsert` expects."""
    price = Decimal(close)
    return {
        "stock_id": stock.stock_id,
        "date": date,
        "time": time,
        "open_price": price - Decimal("0.5000"),
        "high_price": price + Decimal("1.0000"),
        "low_price": price - Decimal("1.0000"),
        "close_price": price,
        "volume": 1_000,
    }


async def _series(session: AsyncSession, stock: Stock) -> None:
    """One candle on each of three consecutive days, inserted out of order."""
    for date in (DAY_TWO, DAY_ONE, DAY_THREE):
        await StockDataFactory().create(session, stock=stock, date=date, time=OPEN_BELL)


class TestLookups:
    async def test_get_by_id(self, db_session: AsyncSession) -> None:
        stock = await StockFactory().create(db_session)
        candle = await StockDataFactory().create(db_session, stock=stock)

        found = await repo.get_by_id(db_session, candle.id)

        assert found is not None
        assert found.id == candle.id

    async def test_get_by_id_is_none_for_an_unknown_id(self, db_session: AsyncSession) -> None:
        assert await repo.get_by_id(db_session, 987_654_321) is None

    async def test_get_at_finds_the_natural_key(self, db_session: AsyncSession) -> None:
        stock = await StockFactory().create(db_session)
        candle = await StockDataFactory().create(
            db_session, stock=stock, date=DAY_ONE, time=OPEN_BELL
        )

        found = await repo.get_at(db_session, stock.stock_id, DAY_ONE, OPEN_BELL)

        assert found is not None
        assert found.id == candle.id

    async def test_get_at_is_none_for_a_minute_never_ingested(
        self, db_session: AsyncSession
    ) -> None:
        stock = await StockFactory().create(db_session)
        await StockDataFactory().create(db_session, stock=stock, date=DAY_ONE, time=OPEN_BELL)

        assert await repo.get_at(db_session, stock.stock_id, DAY_ONE, dt.time(16, 0)) is None

    async def test_get_latest_for_stock_returns_the_newest_candle(
        self, db_session: AsyncSession
    ) -> None:
        """ANV-22 resumes from here, so "newest" must not mean "inserted last"."""
        stock = await StockFactory().create(db_session)
        await _series(db_session, stock)

        latest = await repo.get_latest_for_stock(db_session, stock.stock_id)

        assert latest is not None
        assert latest.date == DAY_THREE

    async def test_get_latest_for_stock_prefers_the_later_time_on_the_same_day(
        self, db_session: AsyncSession
    ) -> None:
        stock = await StockFactory().create(db_session)
        await StockDataFactory().create(db_session, stock=stock, date=DAY_ONE, time=dt.time(16, 0))
        await StockDataFactory().create(db_session, stock=stock, date=DAY_ONE, time=OPEN_BELL)

        latest = await repo.get_latest_for_stock(db_session, stock.stock_id)

        assert latest is not None
        assert latest.time == dt.time(16, 0)

    async def test_get_latest_for_stock_is_none_when_there_are_no_candles(
        self, db_session: AsyncSession
    ) -> None:
        stock = await StockFactory().create(db_session)

        assert await repo.get_latest_for_stock(db_session, stock.stock_id) is None


class TestRangeReads:
    async def test_it_returns_a_stocks_candles_chronologically(
        self, db_session: AsyncSession
    ) -> None:
        stock = await StockFactory().create(db_session)
        await _series(db_session, stock)

        rows, total = await repo.list_for_stock(db_session, stock.stock_id, limit=10)

        assert [row.date for row in rows] == [DAY_ONE, DAY_TWO, DAY_THREE]
        assert total == 3

    async def test_it_does_not_leak_another_stocks_candles(
        self, db_session: AsyncSession
    ) -> None:
        mine = await StockFactory().create(db_session)
        theirs = await StockFactory().create(db_session)
        await _series(db_session, mine)
        await _series(db_session, theirs)

        _, total = await repo.list_for_stock(db_session, mine.stock_id, limit=10)

        assert total == 3

    async def test_the_date_range_is_inclusive_at_both_ends(
        self, db_session: AsyncSession
    ) -> None:
        stock = await StockFactory().create(db_session)
        await _series(db_session, stock)

        rows, total = await repo.list_for_stock(
            db_session, stock.stock_id, start=DAY_ONE, end=DAY_TWO, limit=10
        )

        assert [row.date for row in rows] == [DAY_ONE, DAY_TWO]
        assert total == 2

    async def test_each_bound_is_independently_optional(self, db_session: AsyncSession) -> None:
        stock = await StockFactory().create(db_session)
        await _series(db_session, stock)

        _, since = await repo.list_for_stock(db_session, stock.stock_id, start=DAY_TWO, limit=10)
        _, until = await repo.list_for_stock(db_session, stock.stock_id, end=DAY_TWO, limit=10)

        assert since == 2
        assert until == 2

    async def test_a_range_that_matches_nothing_is_empty(self, db_session: AsyncSession) -> None:
        stock = await StockFactory().create(db_session)
        await _series(db_session, stock)

        rows, total = await repo.list_for_stock(
            db_session, stock.stock_id, start=dt.date(2030, 1, 1), limit=10
        )

        assert (rows, total) == ([], 0)

    async def test_an_unknown_stock_is_empty_not_none(self, db_session: AsyncSession) -> None:
        """A repo reports emptiness; distinguishing "no stock" from "no data" is a service's."""
        rows, total = await repo.list_for_stock(db_session, uuid.uuid4(), limit=10)

        assert (rows, total) == ([], 0)

    async def test_count_for_stock(self, db_session: AsyncSession) -> None:
        stock = await StockFactory().create(db_session)
        await _series(db_session, stock)

        assert await repo.count_for_stock(db_session, stock.stock_id) == 3
        assert await repo.count_for_stock(db_session, uuid.uuid4()) == 0


class TestRangeReadsByTicker:
    async def test_it_resolves_the_symbol_and_the_range_in_one_query(
        self, db_session: AsyncSession
    ) -> None:
        stock = await StockFactory().create(db_session, ticker_symbol="NVDA")
        await _series(db_session, stock)

        rows, total = await repo.list_for_ticker(
            db_session, "NVDA", start=DAY_TWO, end=DAY_THREE, limit=10
        )

        assert [row.date for row in rows] == [DAY_TWO, DAY_THREE]
        assert total == 2

    async def test_it_does_not_match_another_symbol(self, db_session: AsyncSession) -> None:
        nvda = await StockFactory().create(db_session, ticker_symbol="NVDA")
        amd = await StockFactory().create(db_session, ticker_symbol="AMD")
        await _series(db_session, nvda)
        await StockDataFactory().create(db_session, stock=amd, date=DAY_ONE, time=OPEN_BELL)

        _, total = await repo.list_for_ticker(db_session, "AMD", limit=10)

        assert total == 1

    async def test_an_unknown_ticker_is_empty(self, db_session: AsyncSession) -> None:
        rows, total = await repo.list_for_ticker(db_session, "NOPE", limit=10)

        assert (rows, total) == ([], 0)


class TestPaginationBoundaries:
    async def test_limit_windows_the_rows_but_not_the_total(
        self, db_session: AsyncSession
    ) -> None:
        stock = await StockFactory().create(db_session)
        await _series(db_session, stock)

        rows, total = await repo.list_for_stock(db_session, stock.stock_id, limit=2)

        assert [row.date for row in rows] == [DAY_ONE, DAY_TWO]
        assert total == 3

    async def test_offset_continues_the_same_ordering(self, db_session: AsyncSession) -> None:
        stock = await StockFactory().create(db_session)
        await _series(db_session, stock)

        rows, total = await repo.list_for_stock(db_session, stock.stock_id, limit=2, offset=2)

        assert [row.date for row in rows] == [DAY_THREE]
        assert total == 3

    async def test_an_offset_past_the_end_still_reports_the_total(
        self, db_session: AsyncSession
    ) -> None:
        stock = await StockFactory().create(db_session)
        await _series(db_session, stock)

        rows, total = await repo.list_for_stock(db_session, stock.stock_id, limit=10, offset=50)

        assert rows == []
        assert total == 3

    async def test_the_total_respects_the_date_filter_not_the_window(
        self, db_session: AsyncSession
    ) -> None:
        stock = await StockFactory().create(db_session)
        await _series(db_session, stock)

        rows, total = await repo.list_for_stock(
            db_session, stock.stock_id, end=DAY_TWO, limit=1, offset=0
        )

        assert len(rows) == 1
        assert total == 2


class TestEagerLoading:
    async def test_the_plain_list_does_not_load_the_stock(
        self, db_session: AsyncSession
    ) -> None:
        """Stated so the split between the two list methods is not mistaken for an accident."""
        stock = await StockFactory().create(db_session, ticker_symbol="MSFT")
        await StockDataFactory().create(db_session, stock=stock)
        db_session.expunge_all()

        rows, _ = await repo.list_for_stock(db_session, stock.stock_id, limit=10)

        with pytest.raises(MissingGreenlet):
            _ = rows[0].stock.ticker_symbol

    async def test_the_eager_variant_loads_it(self, db_session: AsyncSession) -> None:
        stock = await StockFactory().create(db_session, ticker_symbol="MSFT")
        await StockDataFactory().create(db_session, stock=stock)
        db_session.expunge_all()

        rows, total = await repo.list_for_stock_with_stock(db_session, stock.stock_id, limit=10)

        assert total == 1
        assert rows[0].stock.ticker_symbol == "MSFT"


class TestBulkUpsert:
    """The property ANV-22's idempotency rests on."""

    async def test_it_inserts_a_batch(self, db_session: AsyncSession) -> None:
        stock = await StockFactory().create(db_session)
        rows = [
            _candle(stock, DAY_ONE, dt.time(9, 30), "100.0000"),
            _candle(stock, DAY_ONE, dt.time(9, 35), "101.0000"),
            _candle(stock, DAY_TWO, dt.time(9, 30), "102.0000"),
        ]

        written = await repo.bulk_upsert(db_session, rows)

        assert written == 3
        assert await repo.count_for_stock(db_session, stock.stock_id) == 3

    async def test_running_it_twice_updates_rather_than_duplicating(
        self, db_session: AsyncSession
    ) -> None:
        """The whole point. Same batch, second run: three rows before and after."""
        stock = await StockFactory().create(db_session)
        rows = [
            _candle(stock, DAY_ONE, dt.time(9, 30), "100.0000"),
            _candle(stock, DAY_ONE, dt.time(9, 35), "101.0000"),
            _candle(stock, DAY_TWO, dt.time(9, 30), "102.0000"),
        ]

        first = await repo.bulk_upsert(db_session, rows)
        after_first = await repo.count_for_stock(db_session, stock.stock_id)

        second = await repo.bulk_upsert(db_session, rows)
        after_second = await repo.count_for_stock(db_session, stock.stock_id)

        assert (first, second) == (3, 3)
        assert after_first == after_second == 3

    async def test_a_revised_candle_overwrites_the_stored_one(
        self, db_session: AsyncSession
    ) -> None:
        """A vendor that corrects a close after the bell must not create a second candle."""
        stock = await StockFactory().create(db_session)
        await repo.bulk_upsert(db_session, [_candle(stock, DAY_ONE, OPEN_BELL, "100.0000")])
        original = await repo.get_at(db_session, stock.stock_id, DAY_ONE, OPEN_BELL)
        assert original is not None
        original_id = original.id

        revised = _candle(stock, DAY_ONE, OPEN_BELL, "111.2500")
        revised["volume"] = 9_999
        await repo.bulk_upsert(db_session, [revised])

        # A Core statement leaves the identity map untouched (documented on `bulk_upsert`),
        # so the stored row has to be re-read rather than re-inspected.
        db_session.expunge_all()
        stored = await repo.get_at(db_session, stock.stock_id, DAY_ONE, OPEN_BELL)
        assert stored is not None
        assert stored.close_price == Decimal("111.2500")
        assert stored.volume == 9_999
        assert stored.id == original_id, "the row keeps its identity; it is updated, not replaced"
        assert await repo.count_for_stock(db_session, stock.stock_id) == 1

    async def test_a_partly_overlapping_batch_inserts_only_what_is_new(
        self, db_session: AsyncSession
    ) -> None:
        stock = await StockFactory().create(db_session)
        await repo.bulk_upsert(
            db_session,
            [
                _candle(stock, DAY_ONE, dt.time(9, 30), "100.0000"),
                _candle(stock, DAY_ONE, dt.time(9, 35), "101.0000"),
            ],
        )

        await repo.bulk_upsert(
            db_session,
            [
                _candle(stock, DAY_ONE, dt.time(9, 35), "101.5000"),
                _candle(stock, DAY_ONE, dt.time(9, 40), "102.0000"),
            ],
        )

        assert await repo.count_for_stock(db_session, stock.stock_id) == 3

    async def test_the_conflict_target_is_per_stock(self, db_session: AsyncSession) -> None:
        """Same minute, two stocks — that is not a conflict."""
        first = await StockFactory().create(db_session)
        second = await StockFactory().create(db_session)

        written = await repo.bulk_upsert(
            db_session,
            [
                _candle(first, DAY_ONE, OPEN_BELL, "100.0000"),
                _candle(second, DAY_ONE, OPEN_BELL, "200.0000"),
            ],
        )

        assert written == 2
        assert await repo.count_for_stock(db_session, first.stock_id) == 1
        assert await repo.count_for_stock(db_session, second.stock_id) == 1

    async def test_an_empty_batch_is_a_no_op(self, db_session: AsyncSession) -> None:
        """"Nothing to ingest" is normal, and an empty `VALUES` is a syntax error."""
        assert await repo.bulk_upsert(db_session, []) == 0

    async def test_a_batch_with_internal_duplicates_is_rejected(
        self, db_session: AsyncSession
    ) -> None:
        """Documented caller obligation: deduplicate first, because *which wins* is a rule."""
        stock = await StockFactory().create(db_session)
        duplicate = _candle(stock, DAY_ONE, OPEN_BELL, "100.0000")

        with pytest.raises(DBAPIError, match="cannot affect row a second time"):
            await repo.bulk_upsert(db_session, [duplicate, dict(duplicate)])

    async def test_it_does_not_commit(self, db_session: AsyncSession) -> None:
        stock = await StockFactory().create(db_session)
        await repo.bulk_upsert(db_session, [_candle(stock, DAY_ONE, OPEN_BELL, "100.0000")])

        await db_session.rollback()

        assert await repo.count_for_stock(db_session, stock.stock_id) == 0
