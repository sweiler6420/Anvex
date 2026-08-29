"""``StockRepo`` against a real Postgres.

Two things carry their weight here: the search the old API got wrong (it lower-cased the
term and compared it to upper-case tickers, so it matched nothing), and the pagination
boundaries — ``total`` has to describe the *filter*, not the window, or every "page 3 of
7" the frontend renders is a lie.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.repos import StockRepo
from tests.factories import StockFactory, UserFactory, WatchlistDataFactory, WatchlistFactory

repo = StockRepo()


async def _seed(session: AsyncSession) -> None:
    """Four stocks with deliberately overlapping tickers and company names."""
    await StockFactory().create(session, ticker_symbol="AAPL", company="Apple Inc.")
    await StockFactory().create(session, ticker_symbol="GOOG", company="Alphabet Inc.")
    await StockFactory().create(session, ticker_symbol="GOOGL", company="Alphabet Inc.")
    await StockFactory().create(session, ticker_symbol="NVDA", company="NVIDIA Corporation")


class TestLookups:
    async def test_get_by_id(self, db_session: AsyncSession) -> None:
        stock = await StockFactory().create(db_session)

        found = await repo.get_by_id(db_session, stock.stock_id)

        assert found is not None
        assert found.stock_id == stock.stock_id

    async def test_get_by_id_is_none_for_an_unknown_id(self, db_session: AsyncSession) -> None:
        assert await repo.get_by_id(db_session, uuid.uuid4()) is None

    async def test_get_by_ticker(self, db_session: AsyncSession) -> None:
        await StockFactory().create(db_session, ticker_symbol="MSFT")

        found = await repo.get_by_ticker(db_session, "MSFT")

        assert found is not None
        assert found.ticker_symbol == "MSFT"

    async def test_get_by_ticker_is_exact_and_case_sensitive(
        self, db_session: AsyncSession
    ) -> None:
        """Documented behaviour: normalising a symbol is the service's job, not the repo's."""
        await StockFactory().create(db_session, ticker_symbol="MSFT")

        assert await repo.get_by_ticker(db_session, "msft") is None
        assert await repo.get_by_ticker(db_session, "MSF") is None

    async def test_a_suffixed_ticker_fits(self, db_session: AsyncSession) -> None:
        """`VARCHAR(16)` (ANV-7) — the old `VARCHAR(5)` could not hold this."""
        await StockFactory().create(db_session, ticker_symbol="BRK.B")

        found = await repo.get_by_ticker(db_session, "BRK.B")

        assert found is not None

    async def test_get_by_tickers_returns_the_batch_in_ticker_order(
        self, db_session: AsyncSession
    ) -> None:
        await _seed(db_session)

        found = await repo.get_by_tickers(db_session, ["NVDA", "AAPL", "UNKNOWN"])

        assert [s.ticker_symbol for s in found] == ["AAPL", "NVDA"]

    async def test_get_by_tickers_with_no_tickers_does_not_query(
        self, db_session: AsyncSession
    ) -> None:
        assert await repo.get_by_tickers(db_session, []) == []


class TestListing:
    async def test_it_lists_everything_in_ticker_order(self, db_session: AsyncSession) -> None:
        await _seed(db_session)

        stocks, total = await repo.list_stocks(db_session, limit=10)

        assert [s.ticker_symbol for s in stocks] == ["AAPL", "GOOG", "GOOGL", "NVDA"]
        assert total == 4

    async def test_search_matches_a_ticker_case_insensitively(
        self, db_session: AsyncSession
    ) -> None:
        """The exact case the old `/v1/stock_data` filter could never match."""
        await _seed(db_session)

        stocks, total = await repo.list_stocks(db_session, search="nvda", limit=10)

        assert [s.ticker_symbol for s in stocks] == ["NVDA"]
        assert total == 1

    async def test_search_matches_a_company_name(self, db_session: AsyncSession) -> None:
        await _seed(db_session)

        stocks, total = await repo.list_stocks(db_session, search="alphabet", limit=10)

        assert [s.ticker_symbol for s in stocks] == ["GOOG", "GOOGL"]
        assert total == 2

    async def test_search_matches_a_substring(self, db_session: AsyncSession) -> None:
        await _seed(db_session)

        stocks, _ = await repo.list_stocks(db_session, search="oog", limit=10)

        assert [s.ticker_symbol for s in stocks] == ["GOOG", "GOOGL"]

    async def test_a_blank_search_is_no_filter_rather_than_contains_nothing(
        self, db_session: AsyncSession
    ) -> None:
        await _seed(db_session)

        for blank in (None, "", "   "):
            _, total = await repo.list_stocks(db_session, search=blank, limit=10)
            assert total == 4

    async def test_wildcards_in_the_term_are_escaped(self, db_session: AsyncSession) -> None:
        """Unescaped, `%` would match every row and `_` would match any character."""
        await _seed(db_session)
        await StockFactory().create(db_session, ticker_symbol="PCT", company="100% Holdings")

        stocks, total = await repo.list_stocks(db_session, search="100%", limit=10)

        assert total == 1
        assert stocks[0].ticker_symbol == "PCT"

        # A bare `%` is a search for the character, not for everything: one of five rows.
        matches, total = await repo.list_stocks(db_session, search="%", limit=10)
        assert (total, matches[0].ticker_symbol) == (1, "PCT")

        # `_` is a literal too, so it cannot stand in for the second `A` of AAPL.
        assert (await repo.list_stocks(db_session, search="A_PL", limit=10))[1] == 0

    async def test_a_search_that_matches_nothing_is_empty_not_none(
        self, db_session: AsyncSession
    ) -> None:
        await _seed(db_session)

        stocks, total = await repo.list_stocks(db_session, search="zzzz", limit=10)

        assert stocks == []
        assert total == 0


class TestPaginationBoundaries:
    async def test_limit_bounds_the_window_but_not_the_total(
        self, db_session: AsyncSession
    ) -> None:
        await _seed(db_session)

        stocks, total = await repo.list_stocks(db_session, limit=2)

        assert [s.ticker_symbol for s in stocks] == ["AAPL", "GOOG"]
        assert total == 4

    async def test_offset_walks_the_ordering_without_repeating_a_row(
        self, db_session: AsyncSession
    ) -> None:
        await _seed(db_session)

        first, _ = await repo.list_stocks(db_session, limit=2, offset=0)
        second, _ = await repo.list_stocks(db_session, limit=2, offset=2)

        assert [s.ticker_symbol for s in first] == ["AAPL", "GOOG"]
        assert [s.ticker_symbol for s in second] == ["GOOGL", "NVDA"]

    async def test_an_offset_past_the_end_is_empty_but_still_reports_the_total(
        self, db_session: AsyncSession
    ) -> None:
        """`([], 4)`, not `([], 0)` — that is what lets a client know it paged too far."""
        await _seed(db_session)

        stocks, total = await repo.list_stocks(db_session, limit=10, offset=99)

        assert stocks == []
        assert total == 4

    async def test_the_total_describes_the_filter_not_the_window(
        self, db_session: AsyncSession
    ) -> None:
        await _seed(db_session)

        stocks, total = await repo.list_stocks(db_session, search="alphabet", limit=1)

        assert len(stocks) == 1
        assert total == 2

    async def test_a_limit_of_one_still_pages_the_whole_filtered_set(
        self, db_session: AsyncSession
    ) -> None:
        await _seed(db_session)

        seen = []
        for offset in range(4):
            page, total = await repo.list_stocks(db_session, limit=1, offset=offset)
            assert total == 4
            seen.extend(s.ticker_symbol for s in page)

        assert seen == ["AAPL", "GOOG", "GOOGL", "NVDA"]


class TestUniqueness:
    async def test_ticker_exists(self, db_session: AsyncSession) -> None:
        stock = await StockFactory().create(db_session, ticker_symbol="TSLA")

        assert await repo.ticker_exists(db_session, "TSLA") is True
        assert await repo.ticker_exists(db_session, "TSLQ") is False
        assert (
            await repo.ticker_exists(db_session, "TSLA", exclude_stock_id=stock.stock_id) is False
        )

    async def test_isin_exists(self, db_session: AsyncSession) -> None:
        stock = await StockFactory().create(db_session, isin="US0378331005")

        assert await repo.isin_exists(db_session, "US0378331005") is True
        assert await repo.isin_exists(db_session, "US0000000000") is False
        assert (
            await repo.isin_exists(db_session, "US0378331005", exclude_stock_id=stock.stock_id)
            is False
        )


class TestWrites:
    async def test_create_persists_and_generates_an_id(self, db_session: AsyncSession) -> None:
        stock = await repo.create(
            db_session, ticker_symbol="AMD", company="Advanced Micro Devices", market="NASDAQ"
        )

        assert isinstance(stock.stock_id, uuid.UUID)
        assert stock.isin is None
        assert await repo.get_by_ticker(db_session, "AMD") is not None

    async def test_create_does_not_commit(self, db_session: AsyncSession) -> None:
        """Rolling back must undo it — proof the repo only flushed (`CLAUDE.md` §3)."""
        await repo.create(db_session, ticker_symbol="TEMP", company="Temp", market="NYSE")

        await db_session.rollback()

        assert await repo.get_by_ticker(db_session, "TEMP") is None

    async def test_update_can_clear_a_nullable_column(self, db_session: AsyncSession) -> None:
        """`{"isin": None}` clears it; an absent key leaves it alone (ANV-8)."""
        stock = await StockFactory().create(db_session, isin="US1234567890")

        await repo.update(db_session, stock, {"isin": None})
        assert stock.isin is None

        await repo.update(db_session, stock, {"company": "Renamed Inc."})
        assert stock.company == "Renamed Inc."
        assert stock.isin is None

    async def test_delete_removes_an_unwatched_stock(self, db_session: AsyncSession) -> None:
        stock = await StockFactory().create(db_session)

        await repo.delete(db_session, stock)

        assert await repo.get_by_id(db_session, stock.stock_id) is None

    async def test_deleting_a_watched_stock_raises_integrity_error(
        self, db_session: AsyncSession
    ) -> None:
        """`watchlist_data.stock_id` is ON DELETE RESTRICT; ANV-13 maps this to a 409."""
        user = await UserFactory().create(db_session)
        stock = await StockFactory().create(db_session)
        watchlist = await WatchlistFactory().create(db_session, user=user)
        await WatchlistDataFactory().create(db_session, watchlist=watchlist, stock=stock)

        with pytest.raises(IntegrityError, match="fk_watchlist_data_stock_id_stocks"):
            await repo.delete(db_session, stock)
