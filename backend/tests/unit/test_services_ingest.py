"""Unit tests for ``app.services.ingest`` against in-memory fakes.

``IngestService`` is where four layers meet, and every one of its own decisions — resolve
the ticker, ask the vendor for one named month, apply the two domain filters, rename the
columns, quantise, deduplicate, upsert, commit — is testable without a socket or a database.
What is *not* testable here is whether Postgres agrees that the batch is safe, which is
``tests/integration/test_services_ingest.py``'s job and the reason
:class:`tests.helpers.FakeStockDataRepo` refuses an internal duplicate the way Postgres does.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

from app.domain.errors import ExternalServiceError, NotFoundError, ValidationError
from app.domain.ingest import CALL_SPACING_SECONDS, MAX_CALLS_PER_RUN, dispatch_delays
from app.models import StockData
from app.repos.stock_data import UPDATABLE_COLUMNS
from app.services.ingest import (
    BAR_INTERVAL,
    COLUMN_FOR_FIELD,
    PRICE_FIELDS,
    RESOURCE,
    IngestReport,
    IngestService,
    IngestTarget,
    dispatch_plan,
)
from app.settings import Settings
from tests.helpers import (
    FakeAlphaVantageClient,
    FakeStockDataRepo,
    FakeStockRepo,
    StubSession,
    make_bar,
    make_candle,
    make_series,
    make_stock,
    not_configured,
    rate_limited,
)

NOW = dt.datetime(2026, 3, 2, 17, 0, tzinfo=dt.UTC)
DAY = dt.date(2026, 3, 2)
MONTH = "2026-03"


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)


def build(
    *,
    stocks: FakeStockRepo,
    candles: FakeStockDataRepo,
    client: FakeAlphaVantageClient,
    settings: Settings,
) -> tuple[IngestService, StubSession]:
    session = StubSession()
    service = IngestService(
        session,  # type: ignore[arg-type]
        settings,
        client=client,  # type: ignore[arg-type]
        stocks=stocks,  # type: ignore[arg-type]
        candles=candles,  # type: ignore[arg-type]
    )
    return service, session


# ---------------------------------------------------------------------------------------
# the rename table
# ---------------------------------------------------------------------------------------


class TestTheRenameTable:
    """The vendor's words become Anvex's here and nowhere else."""

    def test_it_covers_every_column_the_upsert_writes(self) -> None:
        """A sweep rather than five assertions: a sixth column added to
        ``UPDATABLE_COLUMNS`` and forgotten here would produce a ``NOT NULL`` violation in
        production and nothing at all in a hand-written test."""
        assert set(COLUMN_FOR_FIELD.values()) == set(UPDATABLE_COLUMNS)

    def test_the_rename_plus_the_key_is_every_column_a_row_needs(self) -> None:
        """``bulk_upsert`` requires every non-generated column; ``id`` is a ``BIGSERIAL``."""
        columns = {column.name for column in StockData.__table__.columns} - {"id"}

        assert set(COLUMN_FOR_FIELD.values()) | {"stock_id", "date", "time"} == columns

    def test_only_the_price_columns_are_quantised(self) -> None:
        """``volume`` is a ``BIGINT`` and has no scale to quantise to."""
        assert set(PRICE_FIELDS) == {"open", "high", "low", "close"}
        assert "volume" not in PRICE_FIELDS

    def test_the_bar_width_is_the_one_the_table_was_sized_for(self) -> None:
        assert str(BAR_INTERVAL) == "5min"


# ---------------------------------------------------------------------------------------
# planning
# ---------------------------------------------------------------------------------------


class TestPlan:
    async def test_an_empty_roster_plans_nothing(self, settings: Settings) -> None:
        service, _ = build(
            stocks=FakeStockRepo(),
            candles=FakeStockDataRepo(),
            client=FakeAlphaVantageClient(),
            settings=settings,
        )

        assert await service.plan(now=NOW) == ()

    async def test_a_stock_with_no_candles_gets_a_bounded_history(self, settings: Settings) -> None:
        stock = make_stock(ticker_symbol="AAPL")
        service, _ = build(
            stocks=FakeStockRepo(stock),
            candles=FakeStockDataRepo(),
            client=FakeAlphaVantageClient(),
            settings=settings,
        )

        targets = await service.plan(now=NOW)

        assert targets == (
            IngestTarget(ticker="AAPL", month="2026-03"),
            IngestTarget(ticker="AAPL", month="2026-02"),
        )

    async def test_an_up_to_date_stock_is_one_call(self, settings: Settings) -> None:
        stock = make_stock(ticker_symbol="AAPL")
        candles = FakeStockDataRepo(make_candle(stock_id=stock.stock_id, date=DAY))
        service, _ = build(
            stocks=FakeStockRepo(stock),
            candles=candles,
            client=FakeAlphaVantageClient(),
            settings=settings,
        )

        assert await service.plan(now=NOW) == (IngestTarget(ticker="AAPL", month="2026-03"),)

    async def test_the_current_month_of_every_stock_comes_first(self, settings: Settings) -> None:
        """The fan-out order, seen through the service: a budget too small for the roster is
        spent on breadth before depth."""
        fresh = make_stock(ticker_symbol="AAPL")
        stale = make_stock(ticker_symbol="MSFT")
        candles = FakeStockDataRepo(make_candle(stock_id=fresh.stock_id, date=DAY))
        service, _ = build(
            stocks=FakeStockRepo(fresh, stale),
            candles=candles,
            client=FakeAlphaVantageClient(),
            settings=settings,
        )

        targets = await service.plan(now=NOW)

        assert [target.month for target in targets[:2]] == ["2026-03", "2026-03"]
        assert {target.ticker for target in targets[:2]} == {"AAPL", "MSFT"}

    async def test_the_budget_bounds_the_run(self, settings: Settings) -> None:
        roster = [make_stock(ticker_symbol=f"T{index}") for index in range(15)]
        service, _ = build(
            stocks=FakeStockRepo(*roster),
            candles=FakeStockDataRepo(),
            client=FakeAlphaVantageClient(),
            settings=settings,
        )

        assert len(await service.plan(now=NOW)) == MAX_CALLS_PER_RUN
        assert len(await service.plan(now=NOW, limit=3)) == 3

    async def test_planning_makes_no_vendor_call(self, settings: Settings) -> None:
        """The fan-out spends no quota deciding how to spend quota."""
        client = FakeAlphaVantageClient()
        service, _ = build(
            stocks=FakeStockRepo(make_stock()),
            candles=FakeStockDataRepo(),
            client=client,
            settings=settings,
        )

        await service.plan(now=NOW)

        assert client.calls == []

    async def test_planning_does_not_commit(self, settings: Settings) -> None:
        service, session = build(
            stocks=FakeStockRepo(make_stock()),
            candles=FakeStockDataRepo(),
            client=FakeAlphaVantageClient(),
            settings=settings,
        )

        await service.plan(now=NOW)

        assert session.commits == 0

    async def test_a_naive_now_is_refused_by_the_domain_rule(self, settings: Settings) -> None:
        """Deliberately untranslated: a job that computed a naive clock is a bug in the job."""
        service, _ = build(
            stocks=FakeStockRepo(make_stock()),
            candles=FakeStockDataRepo(),
            client=FakeAlphaVantageClient(),
            settings=settings,
        )

        with pytest.raises(ValueError, match="timezone-aware"):
            await service.plan(now=dt.datetime(2026, 3, 2, 12, 0))

    def test_a_target_serialises_to_task_kwargs(self) -> None:
        assert IngestTarget(ticker="AAPL", month="2026-03").as_message() == {
            "ticker": "AAPL",
            "month": "2026-03",
        }


# ---------------------------------------------------------------------------------------
# ingesting one month
# ---------------------------------------------------------------------------------------


class TestIngestMonth:
    async def test_it_writes_the_renamed_quantised_rows_and_commits(
        self, settings: Settings
    ) -> None:
        stock = make_stock(ticker_symbol="AAPL")
        client = FakeAlphaVantageClient(
            series=make_series(
                make_bar(day=DAY, at=dt.time(9, 35), open="186.421", close="186.42005678")
            )
        )
        candles = FakeStockDataRepo()
        service, session = build(
            stocks=FakeStockRepo(stock), candles=candles, client=client, settings=settings
        )

        report = await service.ingest_month(ticker="AAPL", month=MONTH)

        written = next(row for name, row in candles.calls if name == "bulk_upsert")[0]
        assert written["stock_id"] == stock.stock_id
        assert written["date"] == DAY
        assert written["time"] == dt.time(9, 35)
        # The vendor's `open`/`close` became Anvex's columns, quantised to NUMERIC(12, 4).
        assert str(written["open_price"]) == "186.4210"
        assert str(written["close_price"]) == "186.4201"
        assert report.written == 1
        assert session.commits == 1

    async def test_the_report_narrows_step_by_step(self, settings: Settings) -> None:
        """``fetched=4, written=1`` has to say *where* the other three went."""
        stock = make_stock(ticker_symbol="AAPL")
        client = FakeAlphaVantageClient(
            series=make_series(
                make_bar(day=DAY, at=dt.time(19, 55)),  # outside the window
                make_bar(day=DAY, at=dt.time(15, 0)),  # kept
                make_bar(day=DAY, at=dt.time(9, 35)),  # at the watermark: not new
                make_bar(day=DAY, at=dt.time(7, 0)),  # outside the window
            )
        )
        candles = FakeStockDataRepo(
            make_candle(stock_id=stock.stock_id, date=DAY, time=dt.time(9, 35))
        )
        service, _ = build(
            stocks=FakeStockRepo(stock), candles=candles, client=client, settings=settings
        )

        report = await service.ingest_month(ticker="AAPL", month=MONTH)

        assert report == IngestReport(
            ticker="AAPL",
            month=MONTH,
            fetched=4,
            in_session=2,
            fresh=1,
            written=1,
            duplicates=0,
        )

    async def test_the_month_is_always_named_explicitly(self, settings: Settings) -> None:
        """Never the vendor's "most recent trading days" default: a redelivered message has
        to fetch the same window it fetched the first time."""
        client = FakeAlphaVantageClient(series=make_series())
        service, _ = build(
            stocks=FakeStockRepo(make_stock(ticker_symbol="AAPL")),
            candles=FakeStockDataRepo(),
            client=client,
            settings=settings,
        )

        await service.ingest_month(ticker="AAPL", month=MONTH)

        assert client.calls == [("AAPL", "5min", MONTH)]

    async def test_the_ticker_is_normalised_before_it_reaches_either_neighbour(
        self, settings: Settings
    ) -> None:
        """The repo lookup is exact and case-sensitive so it can use the unique index, and a
        Celery task does not go through a request schema — so the service does it."""
        stocks = FakeStockRepo(make_stock(ticker_symbol="AAPL"))
        client = FakeAlphaVantageClient(series=make_series())
        service, _ = build(
            stocks=stocks, candles=FakeStockDataRepo(), client=client, settings=settings
        )

        await service.ingest_month(ticker="  aapl ", month=MONTH)

        assert ("get_by_ticker", "AAPL") in stocks.calls
        assert client.calls[0][0] == "AAPL"

    async def test_an_untracked_ticker_is_a_404_reported_canonically(
        self, settings: Settings
    ) -> None:
        client = FakeAlphaVantageClient()
        service, _ = build(
            stocks=FakeStockRepo(),
            candles=FakeStockDataRepo(),
            client=client,
            settings=settings,
        )

        with pytest.raises(NotFoundError) as raised:
            await service.ingest_month(ticker="nope", month=MONTH)

        assert raised.value.details == {"resource": RESOURCE, "identifier": "NOPE"}
        assert client.calls == [], "a metered call was spent on a ticker we do not track"

    @pytest.mark.parametrize("month", ["", "2026", "2026-13", "March"])
    async def test_a_malformed_month_is_refused_before_the_vendor_is_called(
        self, settings: Settings, month: str
    ) -> None:
        client = FakeAlphaVantageClient()
        service, _ = build(
            stocks=FakeStockRepo(make_stock()),
            candles=FakeStockDataRepo(),
            client=client,
            settings=settings,
        )

        with pytest.raises(ValidationError) as raised:
            await service.ingest_month(ticker="AAPL", month=month)

        assert raised.value.details["field"] == "month"
        assert client.calls == []

    async def test_an_empty_series_is_a_successful_run_that_wrote_nothing(
        self, settings: Settings
    ) -> None:
        """A month that has not started, or a stock that was not listed yet."""
        service, session = build(
            stocks=FakeStockRepo(make_stock(ticker_symbol="AAPL")),
            candles=FakeStockDataRepo(),
            client=FakeAlphaVantageClient(series=make_series()),
            settings=settings,
        )

        report = await service.ingest_month(ticker="AAPL", month=MONTH)

        assert report.fetched == 0
        assert report.written == 0
        assert session.commits == 1

    async def test_a_response_entirely_outside_trading_hours_writes_nothing(
        self, settings: Settings
    ) -> None:
        client = FakeAlphaVantageClient(
            series=make_series(
                make_bar(day=DAY, at=dt.time(4, 5)), make_bar(day=DAY, at=dt.time(19, 55))
            )
        )
        service, _ = build(
            stocks=FakeStockRepo(make_stock(ticker_symbol="AAPL")),
            candles=FakeStockDataRepo(),
            client=client,
            settings=settings,
        )

        report = await service.ingest_month(ticker="AAPL", month=MONTH)

        assert (report.fetched, report.in_session, report.written) == (2, 0, 0)

    async def test_the_vendors_timezone_is_used_rather_than_assumed(
        self, settings: Settings
    ) -> None:
        """A response quoted in UTC: 13:05 is 08:05 in New York, and 12:05 is 07:05."""
        client = FakeAlphaVantageClient(
            series=make_series(
                make_bar(day=dt.date(2026, 1, 15), at=dt.time(13, 5)),
                make_bar(day=dt.date(2026, 1, 15), at=dt.time(12, 5)),
                timezone="UTC",
            )
        )
        service, _ = build(
            stocks=FakeStockRepo(make_stock(ticker_symbol="AAPL")),
            candles=FakeStockDataRepo(),
            client=client,
            settings=settings,
        )

        report = await service.ingest_month(ticker="AAPL", month="2026-01")

        assert report.in_session == 1

    async def test_the_watermark_is_read_after_the_vendor_call(self, settings: Settings) -> None:
        """The gap between planning and running can be minutes, and a redelivered target can
        be running beside its own first attempt — so the freshest watermark wins."""
        stock = make_stock(ticker_symbol="AAPL")
        candles = FakeStockDataRepo(make_candle(stock_id=stock.stock_id, date=DAY))
        service, _ = build(
            stocks=FakeStockRepo(stock),
            candles=candles,
            client=FakeAlphaVantageClient(series=make_series()),
            settings=settings,
        )

        await service.ingest_month(ticker="AAPL", month=MONTH)

        assert [name for name, _ in candles.calls] == ["get_latest_for_stock", "bulk_upsert"]

    async def test_an_older_month_is_not_filtered_against_the_watermark(
        self, settings: Settings
    ) -> None:
        """A maximum says nothing about January, so filtering would discard the response and
        the hole would never close. The upsert is what makes keeping it all safe."""
        stock = make_stock(ticker_symbol="AAPL")
        candles = FakeStockDataRepo(make_candle(stock_id=stock.stock_id, date=DAY))
        client = FakeAlphaVantageClient(
            series=make_series(make_bar(day=dt.date(2026, 1, 5), at=dt.time(9, 35)))
        )
        service, _ = build(
            stocks=FakeStockRepo(stock), candles=candles, client=client, settings=settings
        )

        report = await service.ingest_month(ticker="AAPL", month="2026-01")

        assert report.fresh == 1
        assert report.written == 1

    async def test_an_internal_duplicate_is_collapsed_rather_than_crashing(
        self, settings: Settings
    ) -> None:
        """The fake refuses a batch with a repeated conflict target, exactly as Postgres
        does — so this passing is proof that the dedupe ran, not that it was unnecessary."""
        bar = make_bar(day=DAY, at=dt.time(15, 0), close="1.0000")
        later = make_bar(day=DAY, at=dt.time(15, 0), close="2.0000")
        client = FakeAlphaVantageClient(series=make_series(bar, later))
        candles = FakeStockDataRepo()
        service, _ = build(
            stocks=FakeStockRepo(make_stock(ticker_symbol="AAPL")),
            candles=candles,
            client=client,
            settings=settings,
        )

        report = await service.ingest_month(ticker="AAPL", month=MONTH)

        assert report.fresh == 2
        assert report.duplicates == 1
        assert report.written == 1
        assert candles.candles[0].close_price.compare(bar.close) != 0  # the later row won

    async def test_running_twice_writes_the_same_rows_rather_than_more(
        self, settings: Settings
    ) -> None:
        """Idempotency across runs, at unit speed. The real proof is against Postgres."""
        stock = make_stock(ticker_symbol="AAPL")
        client = FakeAlphaVantageClient(
            series=make_series(
                make_bar(day=DAY, at=dt.time(15, 0)), make_bar(day=DAY, at=dt.time(15, 5))
            )
        )
        candles = FakeStockDataRepo()
        service, _ = build(
            stocks=FakeStockRepo(stock), candles=candles, client=client, settings=settings
        )

        first = await service.ingest_month(ticker="AAPL", month=MONTH)
        after_first = len(candles.candles)
        second = await service.ingest_month(ticker="AAPL", month=MONTH)

        assert first.written == 2
        assert after_first == 2
        # The second run's watermark now sits at 15:05, so nothing is even sent.
        assert second.fresh == 0
        assert len(candles.candles) == 2

    async def test_a_vendor_failure_propagates_untranslated(self, settings: Settings) -> None:
        """``app/clients/``'s one exit reaches the job, which is the layer that classifies
        it — a service that turned a rate limit into something else would take that away."""
        service, session = build(
            stocks=FakeStockRepo(make_stock(ticker_symbol="AAPL")),
            candles=FakeStockDataRepo(),
            client=FakeAlphaVantageClient(error=rate_limited()),
            settings=settings,
        )

        with pytest.raises(ExternalServiceError) as raised:
            await service.ingest_month(ticker="AAPL", month=MONTH)

        assert raised.value.details["reason"] == "rate_limited"
        # It arrived as a 200 body, so the retry loop had already succeeded and there is no
        # attempt count belonging to it — ANV-18's rule, and the job must not invent one.
        assert "attempts" not in raised.value.details
        assert session.commits == 0

    async def test_a_blank_key_reaches_the_caller_as_not_configured(
        self, settings: Settings
    ) -> None:
        service, _ = build(
            stocks=FakeStockRepo(make_stock(ticker_symbol="AAPL")),
            candles=FakeStockDataRepo(),
            client=FakeAlphaVantageClient(error=not_configured()),
            settings=settings,
        )

        with pytest.raises(ExternalServiceError) as raised:
            await service.ingest_month(ticker="AAPL", month=MONTH)

        assert raised.value.details["setting"] == "ALPHAVANTAGE_API_KEY"

    async def test_a_price_the_column_cannot_hold_is_refused_before_the_write(
        self, settings: Settings
    ) -> None:
        """``NUMERIC`` overflow is a ``DataError`` that aborts a half-built transaction."""
        client = FakeAlphaVantageClient(
            series=make_series(make_bar(day=DAY, at=dt.time(15, 0), high="100000000"))
        )
        service, session = build(
            stocks=FakeStockRepo(make_stock(ticker_symbol="AAPL")),
            candles=FakeStockDataRepo(),
            client=client,
            settings=settings,
        )

        with pytest.raises(ValueError, match="high"):
            await service.ingest_month(ticker="AAPL", month=MONTH)

        assert session.commits == 0

    def test_a_report_is_json_serialisable(self) -> None:
        """A dataclass returned from a task fails at the result backend, inside the worker."""
        import json

        report = IngestReport(
            ticker="AAPL", month=MONTH, fetched=1, in_session=1, fresh=1, written=1, duplicates=0
        )

        assert json.loads(json.dumps(report.as_result())) == report.as_result()


# ---------------------------------------------------------------------------------------
# dispatch pairing
# ---------------------------------------------------------------------------------------


class TestDispatchPlan:
    def test_each_target_is_paired_with_its_own_delay(self) -> None:
        targets = [IngestTarget("AAPL", "2026-03"), IngestTarget("MSFT", "2026-03")]

        messages = dispatch_plan(targets, dispatch_delays(len(targets)))

        assert messages == [
            {"kwargs": {"ticker": "AAPL", "month": "2026-03"}, "countdown": 0},
            {
                "kwargs": {"ticker": "MSFT", "month": "2026-03"},
                "countdown": CALL_SPACING_SECONDS,
            },
        ]

    def test_nothing_to_dispatch_is_no_messages(self) -> None:
        assert dispatch_plan([], []) == []

    def test_a_mismatched_pairing_is_refused_rather_than_truncated(self) -> None:
        """A silent ``zip`` would drop the tail of a fan-out and look like a smaller roster."""
        with pytest.raises(ValueError):
            dispatch_plan([IngestTarget("AAPL", "2026-03")], [])


# ---------------------------------------------------------------------------------------
# the fake keeps the contract it is standing in for
# ---------------------------------------------------------------------------------------


class TestTheFakeRepoIsFaithful:
    """A forgiving fake silently passes the bug the test exists to catch."""

    async def test_it_refuses_an_internal_duplicate_the_way_postgres_does(self) -> None:
        repo = FakeStockDataRepo()
        stock_id = uuid.uuid4()
        row = {
            "stock_id": stock_id,
            "date": DAY,
            "time": dt.time(9, 35),
            "open_price": 1,
            "high_price": 1,
            "low_price": 1,
            "close_price": 1,
            "volume": 1,
        }

        with pytest.raises(RuntimeError, match="cannot affect row a second time"):
            await repo.bulk_upsert(None, [row, dict(row)])

    async def test_an_empty_batch_issues_nothing(self) -> None:
        assert await FakeStockDataRepo().bulk_upsert(None, []) == 0

    async def test_the_watermark_is_the_maximum_not_the_last_added(self) -> None:
        stock_id = uuid.uuid4()
        repo = FakeStockDataRepo(
            make_candle(stock_id=stock_id, date=DAY, time=dt.time(15, 0)),
            make_candle(stock_id=stock_id, date=DAY, time=dt.time(9, 35)),
        )

        latest = await repo.get_latest_for_stock(None, stock_id)

        assert latest is not None
        assert latest.time == dt.time(15, 0)

    async def test_an_unknown_stock_has_no_watermark(self) -> None:
        assert await FakeStockDataRepo().get_latest_for_stock(None, uuid.uuid4()) is None
