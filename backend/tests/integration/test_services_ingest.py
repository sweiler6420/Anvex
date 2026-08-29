"""``IngestService`` end to end: a real ``AlphaVantageClient`` over ``respx``, real Postgres.

``tests/unit/test_services_ingest.py`` covers the branches against fakes. This module covers
the claims only a database can support, and they are the ones ANV-22 exists to make:

* **The upsert is idempotent against a real unique constraint.** The whole ingest runs
  **twice** on the same payload, and the row count does not move. ``task_acks_late`` plus
  beat re-driving guarantee a second run will happen; a fake agreeing that it is safe proves
  nothing about ``ON CONFLICT (stock_id, date, time) DO UPDATE``.
* **A batch with an internal duplicate really would fail**, which is why the dedupe exists.
  One test drives the raw repo with a duplicated key and asserts Postgres refuses it, so the
  test beside it — the same payload going through the service without complaint — is proof
  the domain rule ran rather than proof it was unnecessary.
* **A revised candle overwrites rather than duplicating**, and only the price columns move.
* **The ``NUMERIC(12, 4)`` round trip is the driver's**, not a ``Decimal`` a fake handed
  back: the quantised value asyncpg returns has to equal the value the domain computed,
  including the trailing zero.

Nothing here has ever touched AlphaVantage. The payload is hand-built from the vendor's
documented ``TIME_SERIES_INTRADAY`` shape and ``mock_http`` refuses to let a request escape.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import pytest
import respx
from httpx import Response
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.alphavantage import AlphaVantageClient, time_series_key
from app.models import Stock, StockData
from app.repos.stock import StockRepo
from app.repos.stock_data import StockDataRepo
from app.services.ingest import IngestService
from app.settings import Settings
from tests.factories import StockDataFactory, StockFactory

QUERY_URL = "https://www.alphavantage.co/query"
MONTH = "2026-03"
DAY = "2026-03-02"

#: Newest first, as AlphaVantage lists them. Five bars: two outside the trading window, and
#: three inside it — one of which carries a fifth decimal place the column cannot hold, so
#: the quantisation is visible in the stored row rather than only in a unit test.
CANDLE_ROWS: dict[str, dict[str, str]] = {
    f"{DAY} 19:55:00": {  # post-market: dropped by the session filter
        "1. open": "186.0000",
        "2. high": "186.1000",
        "3. low": "185.9000",
        "4. close": "186.0500",
        "5. volume": "111",
    },
    f"{DAY} 15:55:00": {
        "1. open": "186.12345",  # rounds *up* to 186.1235
        "2. high": "186.5000",
        "3. low": "185.9900",
        "4. close": "186.4200",
        "5. volume": "52815",
    },
    f"{DAY} 09:35:00": {
        "1. open": "184.0100",
        "2. high": "184.9900",
        "3. low": "183.8800",
        "4. close": "184.7700",
        "5. volume": "104556",
    },
    f"{DAY} 08:05:00": {  # exactly the old lower bound: kept
        "1. open": "183.5000",
        "2. high": "183.6000",
        "3. low": "183.4000",
        "4. close": "183.5500",
        "5. volume": "900",
    },
    f"{DAY} 04:05:00": {  # pre-market: dropped by the session filter
        "1. open": "183.0000",
        "2. high": "183.1000",
        "3. low": "182.9000",
        "4. close": "183.0500",
        "5. volume": "12",
    },
}

#: How many of the five survive ``(08:00, 17:00]`` at the exchange.
IN_SESSION = 3


def intraday_payload(rows: dict[str, Any] | None = None, *, symbol: str = "ANVX") -> dict[str, Any]:
    """A successful ``TIME_SERIES_INTRADAY`` body, hand-built from the published shape."""
    return {
        "Meta Data": {
            "1. Information": "Intraday (5min) open, high, low, close prices and volume",
            "2. Symbol": symbol,
            "3. Last Refreshed": f"{DAY} 19:55:00",
            "4. Interval": "5min",
            "5. Output Size": "Full size",
            "6. Time Zone": "US/Eastern",
        },
        time_series_key("5min"): CANDLE_ROWS if rows is None else rows,
    }


@pytest.fixture
def settings() -> Settings:
    """A configured key, so the client gets past its "not configured" pre-flight."""
    return Settings(
        _env_file=None,
        alphavantage_api_key="integration-test-key",
        jwt_secret_key="integration-test-jwt-secret",
    )


@pytest.fixture
async def stock(db_session: AsyncSession) -> Stock:
    return await StockFactory().create(db_session, ticker_symbol="ANVX")


@pytest.fixture
async def vendor(settings: Settings) -> AsyncIterator[AlphaVantageClient]:
    """The **real** client — only its transport is mocked."""
    client = AlphaVantageClient(settings)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
def service(
    db_session: AsyncSession, settings: Settings, vendor: AlphaVantageClient
) -> IngestService:
    """The real service over the real repos and the real client."""
    return IngestService(
        db_session, settings, client=vendor, stocks=StockRepo(), candles=StockDataRepo()
    )


async def stored(session: AsyncSession, stock: Stock) -> list[StockData]:
    """Every candle the database holds for this stock, chronologically.

    ``populate_existing=True`` is load-bearing rather than defensive. ``bulk_upsert`` is a
    Core statement, so it does **not** update the session's identity map (``CLAUDE.md`` §3
    says so, and the repo's docstring repeats it): without this, a re-read after a rewrite
    would hand back the ORM instance the first run loaded and the assertion would be about
    Python's memory rather than about Postgres.
    """
    result = await session.execute(
        select(StockData)
        .where(StockData.stock_id == stock.stock_id)
        .order_by(StockData.date, StockData.time)
        .execution_options(populate_existing=True)
    )
    return list(result.scalars().all())


async def count(session: AsyncSession, stock: Stock) -> int:
    result = await session.execute(
        select(func.count()).select_from(StockData).where(StockData.stock_id == stock.stock_id)
    )
    return int(result.scalar_one())


# ---------------------------------------------------------------------------------------
# one run
# ---------------------------------------------------------------------------------------


async def test_it_writes_the_in_session_candles_with_anvex_column_names(
    db_session: AsyncSession,
    service: IngestService,
    stock: Stock,
    mock_http: respx.MockRouter,
) -> None:
    mock_http.get(QUERY_URL).mock(return_value=Response(200, json=intraday_payload()))

    report = await service.ingest_month(ticker="anvx", month=MONTH)
    rows = await stored(db_session, stock)

    assert report.fetched == len(CANDLE_ROWS)
    assert report.in_session == IN_SESSION
    assert report.written == IN_SESSION
    assert [row.time for row in rows] == [dt.time(8, 5), dt.time(9, 35), dt.time(15, 55)]
    assert all(row.stock_id == stock.stock_id for row in rows)
    assert all(row.date == dt.date(2026, 3, 2) for row in rows)


async def test_the_quantised_price_survives_the_numeric_round_trip(
    db_session: AsyncSession,
    service: IngestService,
    stock: Stock,
    mock_http: respx.MockRouter,
) -> None:
    """``186.12345`` is not a number ``NUMERIC(12, 4)`` can hold. What comes back has to be
    what the domain computed — including the fourth decimal place and its trailing zero."""
    mock_http.get(QUERY_URL).mock(return_value=Response(200, json=intraday_payload()))

    await service.ingest_month(ticker="ANVX", month=MONTH)
    closing = next(row for row in await stored(db_session, stock) if row.time == dt.time(15, 55))

    assert isinstance(closing.open_price, Decimal)
    assert closing.open_price == Decimal("186.1235")
    assert str(closing.open_price) == "186.1235"
    assert str(closing.close_price) == "186.4200"


async def test_the_month_reaches_the_vendors_query_string(
    service: IngestService, stock: Stock, mock_http: respx.MockRouter
) -> None:
    route = mock_http.get(QUERY_URL).mock(return_value=Response(200, json=intraday_payload()))

    await service.ingest_month(ticker="ANVX", month=MONTH)

    request = route.calls[0].request
    assert request.url.params["month"] == MONTH
    assert request.url.params["symbol"] == "ANVX"
    assert request.url.params["interval"] == "5min"
    assert request.url.params["function"] == "TIME_SERIES_INTRADAY"


# ---------------------------------------------------------------------------------------
# the same run, twice — the proof
# ---------------------------------------------------------------------------------------


async def test_running_the_whole_ingest_twice_adds_no_rows_and_raises_nothing(
    db_session: AsyncSession,
    service: IngestService,
    stock: Stock,
    mock_http: respx.MockRouter,
) -> None:
    """The property ``task_acks_late`` requires, proved against the real constraint.

    A lost broker connection redelivers, and beat re-drives after a lost worker, so a target
    **will** run twice. The second run's watermark now sits at the last candle of the first,
    so nothing is even sent — and the row count is identical either way.
    """
    mock_http.get(QUERY_URL).mock(return_value=Response(200, json=intraday_payload()))

    first = await service.ingest_month(ticker="ANVX", month=MONTH)
    after_first = await count(db_session, stock)
    second = await service.ingest_month(ticker="ANVX", month=MONTH)
    after_second = await count(db_session, stock)

    assert (first.fetched, first.written) == (len(CANDLE_ROWS), IN_SESSION)
    assert after_first == IN_SESSION
    assert second.fresh == 0
    assert second.written == 0
    assert after_second == IN_SESSION


async def test_a_second_run_that_ignores_the_watermark_still_only_rewrites(
    db_session: AsyncSession,
    service: IngestService,
    stock: Stock,
    mock_http: respx.MockRouter,
) -> None:
    """The watermark is an optimisation; the constraint is the guarantee.

    Requesting an **older** month deliberately bypasses the watermark filter (a maximum says
    nothing about a month before it), so every candle is sent again — and the upsert is what
    keeps the row count still.
    """
    mock_http.get(QUERY_URL).mock(return_value=Response(200, json=intraday_payload()))
    await service.ingest_month(ticker="ANVX", month=MONTH)

    # Same payload, requested as an earlier month: nothing is filtered by the watermark.
    replay = await service.ingest_month(ticker="ANVX", month="2026-01")

    assert replay.fresh == IN_SESSION
    assert replay.written == IN_SESSION
    assert await count(db_session, stock) == IN_SESSION


async def test_a_revised_candle_overwrites_rather_than_duplicating(
    db_session: AsyncSession,
    service: IngestService,
    stock: Stock,
    mock_http: respx.MockRouter,
) -> None:
    """AlphaVantage revises a bar's volume after the close, which is the realistic reason
    ``DO UPDATE`` is not ``DO NOTHING``."""
    mock_http.get(QUERY_URL).mock(return_value=Response(200, json=intraday_payload()))
    await service.ingest_month(ticker="ANVX", month=MONTH)

    revised = {key: dict(value) for key, value in CANDLE_ROWS.items()}
    revised[f"{DAY} 09:35:00"]["5. volume"] = "999999"
    revised[f"{DAY} 09:35:00"]["4. close"] = "184.8800"
    mock_http.get(QUERY_URL).mock(return_value=Response(200, json=intraday_payload(revised)))

    await service.ingest_month(ticker="ANVX", month="2026-01")
    opening = next(row for row in await stored(db_session, stock) if row.time == dt.time(9, 35))

    assert await count(db_session, stock) == IN_SESSION
    assert opening.volume == 999_999
    assert opening.close_price == Decimal("184.8800")


async def test_an_existing_candle_keeps_its_row_id_across_a_rewrite(
    db_session: AsyncSession,
    service: IngestService,
    stock: Stock,
    mock_http: respx.MockRouter,
) -> None:
    """``id`` is identity, not data: an upsert must not renumber a candle."""
    mock_http.get(QUERY_URL).mock(return_value=Response(200, json=intraday_payload()))
    await service.ingest_month(ticker="ANVX", month=MONTH)
    before = {(row.date, row.time): row.id for row in await stored(db_session, stock)}

    await service.ingest_month(ticker="ANVX", month="2026-01")
    after = {(row.date, row.time): row.id for row in await stored(db_session, stock)}

    assert after == before


# ---------------------------------------------------------------------------------------
# why the dedupe is not ceremony
# ---------------------------------------------------------------------------------------


async def test_postgres_really_does_refuse_a_batch_with_an_internal_duplicate(
    db_session: AsyncSession, stock: Stock
) -> None:
    """The failure the domain rule exists to prevent, reproduced against the real statement.

    Without this, the test above — the same payload going through the service without
    complaint — would be evidence that dedupe was unnecessary rather than that it ran.
    """
    row = {
        "stock_id": stock.stock_id,
        "date": dt.date(2026, 3, 2),
        "time": dt.time(9, 35),
        "open_price": Decimal("1.0000"),
        "high_price": Decimal("1.0000"),
        "low_price": Decimal("1.0000"),
        "close_price": Decimal("1.0000"),
        "volume": 1,
    }

    with pytest.raises(SQLAlchemyError) as raised:
        await StockDataRepo().bulk_upsert(db_session, [row, dict(row)])

    assert "cannot affect row a second time" in str(raised.value)
    await db_session.rollback()


async def test_a_payload_that_names_one_minute_twice_goes_through_cleanly(
    db_session: AsyncSession,
    service: IngestService,
    stock: Stock,
    mock_http: respx.MockRouter,
) -> None:
    """A single response really can carry two rows for one minute, and this is how.

    JSON keys are unique, but ANV-18's parser ``strip()``s the timestamp before parsing it,
    so ``"… 09:35:00"`` and ``" … 09:35:00 "`` are two distinct keys naming one candle. Sent
    straight to ``bulk_upsert`` that is the ``cannot affect row a second time`` failure
    proved above; through the service it is one row, and the later value wins.
    """
    rows = {
        f"{DAY} 09:35:00": dict(CANDLE_ROWS[f"{DAY} 09:35:00"]),
        f" {DAY} 09:35:00 ": {**CANDLE_ROWS[f"{DAY} 09:35:00"], "4. close": "184.9900"},
    }
    mock_http.get(QUERY_URL).mock(return_value=Response(200, json=intraday_payload(rows)))

    report = await service.ingest_month(ticker="ANVX", month=MONTH)
    stored_rows = await stored(db_session, stock)

    assert report.fetched == 2
    assert report.duplicates == 1
    assert report.written == 1
    assert await count(db_session, stock) == 1
    assert stored_rows[0].close_price == Decimal("184.9900")


# ---------------------------------------------------------------------------------------
# planning against real rows
# ---------------------------------------------------------------------------------------


async def test_a_stock_with_candles_is_planned_from_its_newest_one(
    db_session: AsyncSession, service: IngestService, stock: Stock
) -> None:
    """The watermark comes from ``ORDER BY date DESC, time DESC`` in Postgres, not from
    insertion order — the factory's candles are written oldest-first, and the plan has to
    resume from the newest regardless."""
    await StockDataFactory().create(db_session, stock=stock, date=dt.date(2026, 1, 5))
    await StockDataFactory().create(db_session, stock=stock, date=dt.date(2026, 3, 2))
    await StockDataFactory().create(db_session, stock=stock, date=dt.date(2026, 2, 9))

    targets = await service.plan(now=dt.datetime(2026, 3, 2, 17, 0, tzinfo=dt.UTC))

    assert [target.month for target in targets] == ["2026-03"]
    assert targets[0].ticker == "ANVX"


async def test_a_stock_with_no_candles_is_planned_for_a_bounded_history(
    service: IngestService, stock: Stock
) -> None:
    targets = await service.plan(now=dt.datetime(2026, 3, 2, 17, 0, tzinfo=dt.UTC))

    assert [target.month for target in targets] == ["2026-03", "2026-02"]
