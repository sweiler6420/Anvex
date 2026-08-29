"""Ingesting intraday candles: the use case the whole ``AverageInvestorService`` repo was.

This is the seam ``CLAUDE.md`` §3's worked example describes, with all four collaborators
present for the first time in the codebase:

* ``app/clients/alphavantage.py`` answers *how do we fetch it* — and knows nothing else.
* ``app/domain/ingest.py`` answers *what are the rules* — and does no I/O.
* ``app/repos/stock_data.py`` answers *how do we store it* — and holds no rules.
* this module answers *what does the app do*, owns the clock and owns the transaction.

The old ETL had all four in one 300-line script with the ticker interpolated into its SQL.
Nothing here is new behaviour; what is new is that each decision is somewhere it can be
tested on its own.

Two things this layer owns that neither neighbour would take
-----------------------------------------------------------

**The vendor's words become Anvex's here.** ANV-18's
:class:`~app.clients.alphavantage.IntradayCandle` is spelled ``open``/``high``/``low``/
``close`` and carries no ``stock_id``, deliberately: a vendor does not know Anvex's tables,
and a client that returned ``open_price`` would be encoding our schema into its parser. A
repo will not do it either — it takes the columns it has. So :data:`COLUMN_FOR_FIELD` lives
here, in the one layer that is allowed to know both spellings, and it is the *only* place
the rename happens.

**The transaction is one.** Every candle of one month lands in a single
``INSERT … ON CONFLICT DO UPDATE`` inside a single transaction, and the commit is here — a
repo does not commit (``CLAUDE.md`` §3). A month that fails halfway leaves the table exactly
as it was, and beat re-drives it.

Idempotency has two halves and they are different mechanisms
------------------------------------------------------------

The task will run twice: ``task_acks_late`` redelivers on a lost connection and beat
re-drives after a lost worker (ANV-21). Two separate things make that safe, and neither
substitutes for the other:

* **Across runs** — ``bulk_upsert``'s ``ON CONFLICT (stock_id, date, time) DO UPDATE`` on
  ANV-7's real unique constraint. A second run rewrites the rows it wrote the first time.
* **Within one run** — :func:`~app.domain.ingest.dedupe_candle_rows`. Postgres rejects a
  statement whose ``VALUES`` hit one conflict target twice, so an internal duplicate is
  ``ON CONFLICT DO UPDATE command cannot affect row a second time`` — a hard failure, not a
  duplicate row. Two adjacent months overlapping at a boundary is enough to produce one.

An empty response is a normal outcome
-------------------------------------

``candles=()`` is a legitimate answer (ANV-18 chose it over an error) and it means "nothing
traded in the window you asked about" — a month that has not started, a stock that was not
listed yet, a weekend-only window. So is a response whose every candle is outside trading
hours, or older than what is already stored. All three end as ``written=0``, which is a
successful run of a job that had nothing to do, and :class:`IngestReport` says at which of
the four steps the rows went so ``fetched=390, written=0`` is diagnosable rather than
merely surprising.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.alphavantage import AlphaVantageClient, IntradayCandle, IntradayInterval
from app.domain.errors import NotFoundError, ValidationError
from app.domain.ingest import (
    MAX_CALLS_PER_RUN,
    Month,
    Watermark,
    dedupe_candle_rows,
    fan_out_order,
    months_to_fetch,
    quantise_price,
    select_new,
    select_session_candles,
    watermark_for,
)
from app.domain.stock import normalise_ticker
from app.models import Stock
from app.repos.stock import StockRepo, stock_repo
from app.repos.stock_data import StockDataRepo, stock_data_repo
from app.schemas.pagination import MAX_PAGE_LIMIT
from app.settings import Settings

logger = structlog.get_logger("anvex.ingest")

#: The resource a 404 from this module names. Deliberately ``"stock"`` — the thing that was
#: not found is the security, and it is the same spelling ``app/services/stock.py``,
#: ``app/services/stock_data.py`` and ``app/services/news.py`` use, so a client branching on
#: ``details["resource"]`` sees one string whichever caller refused it.
RESOURCE: Final[str] = "stock"

#: The bar width Anvex ingests. The old ETL's, and the one ANV-7's table was sized for.
BAR_INTERVAL: Final[IntradayInterval] = IntradayInterval.FIVE_MINUTES

#: Vendor field → Anvex column. **The only place the rename happens** — see the module
#: docstring for why neither neighbour would host it. ``volume`` is spelled the same in both
#: vocabularies and is mapped explicitly anyway, so the table below is the whole record of
#: what a candle becomes rather than four fifths of it.
COLUMN_FOR_FIELD: Final[Mapping[str, str]] = {
    "open": "open_price",
    "high": "high_price",
    "low": "low_price",
    "close": "close_price",
    "volume": "volume",
}

#: The fields that are prices, and therefore the ones quantised to the column's scale.
#: **Derived** from the mapping above rather than listed again, so a fifth price column
#: cannot be added to one and forgotten in the other — ``volume`` is a ``BIGINT`` and has no
#: scale to quantise to.
PRICE_FIELDS: Final[tuple[str, ...]] = tuple(
    field for field, column in COLUMN_FOR_FIELD.items() if column.endswith("_price")
)


@dataclass(frozen=True, slots=True)
class IngestReport:
    """What one call to :meth:`IngestService.ingest_month` did, step by step.

    Deliberately **not** an ``app/schemas/`` model: nothing serves this over HTTP, and
    ``CLAUDE.md`` §3 reserves that package for the API's public shape. It is the return
    value of an operational method, exactly like ``app/services/politician.py``'s
    ``SeedReport``, and :meth:`as_result` is what turns it into the JSON a Celery result
    backend can hold.

    The four counts narrow monotonically, which is the point — a run that fetched a full
    month and wrote nothing says *where* the rows went:

    ``fetched`` → ``in_session`` (the trading-hours window) → ``fresh`` (newer than the
    watermark) → ``written`` (what the statement touched, after ``duplicates`` were
    collapsed).
    """

    ticker: str
    month: str
    fetched: int
    in_session: int
    fresh: int
    written: int
    duplicates: int

    def as_result(self) -> dict[str, Any]:
        """A JSON-serialisable view, for a Celery task's return value.

        ``task_serializer="json"`` is a promise something eventually has to keep: a
        dataclass, a ``Decimal`` or a ``datetime`` returned from a task fails inside the
        worker at the result backend, long after the code that produced it looked fine.
        """
        return {
            "ticker": self.ticker,
            "month": self.month,
            "fetched": self.fetched,
            "in_session": self.in_session,
            "fresh": self.fresh,
            "written": self.written,
            "duplicates": self.duplicates,
        }


@dataclass(frozen=True, slots=True)
class IngestTarget:
    """One unit of work: one symbol, one month, and therefore **exactly one vendor call**.

    That one-to-one relationship is what makes the quota arithmetic honest. If a target
    covered a symbol's whole month plan, the fan-out's spacing would pace batches of calls
    rather than calls, and the only way to space the calls *inside* one would be to sleep —
    which is what the old ETL did, and what holds a prefork child hostage for the duration.
    """

    ticker: str
    month: str

    def as_message(self) -> dict[str, str]:
        """The task kwargs that run this target. Strings, because a message is JSON."""
        return {"ticker": self.ticker, "month": self.month}


class IngestService:
    """Filling the candle series from AlphaVantage, one symbol-month at a time."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        client: AlphaVantageClient,
        stocks: StockRepo = stock_repo,
        candles: StockDataRepo = stock_data_repo,
    ) -> None:
        self.session = session
        self.settings = settings
        #: Required rather than keyword-defaulted, unlike a repo: a client owns a connection
        #: pool and therefore a lifetime, so there is no module-level singleton to default
        #: to (``CLAUDE.md`` §3). Constructing one opens no socket — the base builds its
        #: ``httpx.AsyncClient`` lazily — so :meth:`plan`, which never calls the vendor,
        #: costs nothing for holding one.
        self.client = client
        self.stocks = stocks
        self.candles = candles

    # -----------------------------------------------------------------------------------
    # Use cases
    # -----------------------------------------------------------------------------------

    async def plan(
        self, *, now: dt.datetime | None = None, limit: int = MAX_CALLS_PER_RUN
    ) -> tuple[IngestTarget, ...]:
        """What this run should fetch: one target per vendor call, in dispatch order.

        Every tracked security is a candidate; "tracked" means a row in ``stocks``, which is
        the same set every other part of Anvex means by it. A stock with no candles is
        planned for :data:`~app.domain.ingest.INITIAL_HISTORY_MONTHS` months and a stock
        that is up to date for one — the current month, which is the incomplete one.

        The result is ordered and truncated by :func:`~app.domain.ingest.fan_out_order`, so
        a budget that cannot cover the whole roster is spent on every stock's *current*
        month before any stock's second.

        :param now: the reference time. **Read here when omitted** — ``CLAUDE.md`` §4 makes a
            service the only layer allowed to read a clock, so the task above does not, and
            the value is read **once** and handed to every ``months_to_fetch`` call in the
            loop. Passing one is the seam a test uses to sit on a month boundary, the same
            way ``PoliticianService.seed_roster(rows=…)`` is a seam for a file.
        :raises ValueError: an injected ``now`` is naive. Raised out of the domain rule and
            deliberately untranslated: nothing but a caller's own bug can produce it.
        """
        at = now if now is not None else dt.datetime.now(dt.UTC)
        stocks, total = await self.stocks.list_stocks(self.session, limit=MAX_PAGE_LIMIT)
        plans: list[tuple[str, tuple[Month, ...]]] = []
        for stock in stocks:
            latest = await self.candles.get_latest_for_stock(self.session, stock.stock_id)
            plans.append(
                (
                    stock.ticker_symbol,
                    months_to_fetch(latest=latest.date if latest else None, now=at),
                )
            )

        targets = tuple(
            IngestTarget(ticker=ticker, month=str(month))
            for ticker, month in fan_out_order(plans, limit=limit)
        )
        logger.info(
            "ingest.planned",
            tracked=total,
            considered=len(stocks),
            targets=len(targets),
            truncated=sum(len(months) for _, months in plans) - len(targets),
        )
        return targets

    async def ingest_month(self, *, ticker: str, month: str) -> IngestReport:
        """Fetch one symbol-month, filter it, and write what survives. **Idempotent.**

        Safe to call twice with the same arguments — that is not incidental, it is the
        property ``task_acks_late`` requires (``CLAUDE.md`` §3), and both halves of how it
        is achieved are in the module docstring.

        The ticker is normalised here rather than at any edge, for the reason
        ``app/services/stock.py`` sets out: the repo lookup is exact and case-sensitive so
        it can use the unique index, and a Celery task does not go through a request schema.

        **It reads no clock, and that is the point.** Every input is on the message, so the
        same message produces the same fetch whether it runs now or after a redelivery ten
        minutes later. The one time-dependent decision in this module — which months are
        worth asking for — belongs to :meth:`plan`, which is why it is the method that reads
        the clock and this one is not.

        :param month: ``YYYY-MM``. Always explicit, never left to the vendor's "most recent
            trading days" default, so the same message fetches the same window whenever it
            is redelivered.
        :returns: what happened at each step — see :class:`IngestReport`.
        :raises ValidationError: ``month`` is not ``YYYY-MM``. A 422 rather than a vendor
            round trip that comes back as an ``Error Message``.
        :raises NotFoundError: no security carries that ticker. Ingest fills the series of
            securities Anvex already tracks; creating one from a vendor response is a
            different decision and nobody has taken it.
        :raises ExternalServiceError: the vendor failed, or ``ALPHAVANTAGE_API_KEY`` is
            blank. The second is the state of a fresh clone and says so in
            ``details["reason"]``, which is what lets the job decline to retry it.
        """
        window = self._month(month)
        symbol = normalise_ticker(ticker)
        stock = await self.stocks.get_by_ticker(self.session, symbol)
        if stock is None:
            raise NotFoundError(RESOURCE, symbol)

        series = await self.client.fetch_intraday(symbol, interval=BAR_INTERVAL, month=str(window))
        traded = select_session_candles(series.candles, timezone=series.timezone)
        latest = await self._watermark(stock)
        fresh = select_new(traded, watermark=watermark_for(window, latest=latest))

        batch = dedupe_candle_rows(self._row(candle, stock_id=stock.stock_id) for candle in fresh)
        written = await self.candles.bulk_upsert(self.session, batch.rows)
        await self.session.commit()

        report = IngestReport(
            ticker=stock.ticker_symbol,
            month=str(window),
            fetched=len(series.candles),
            in_session=len(traded),
            fresh=len(fresh),
            written=written,
            duplicates=batch.deduplicated,
        )
        logger.info(
            "ingest.month",
            stock_id=str(stock.stock_id),
            timezone=series.timezone,
            **report.as_result(),
        )
        return report

    # -----------------------------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------------------------

    @staticmethod
    def _month(month: str) -> Month:
        """``"2024-01"`` as a :class:`~app.domain.ingest.Month`, or a 422.

        The domain raises a plain ``ValueError`` because ``app/domain/ingest.py`` describes a
        rule and not a request; translating it is the calling service's job, exactly as it
        is for an ``app/utils/`` builtin (``CLAUDE.md`` §4). Uncaught, a malformed month
        would be a 500 for an argument that was simply wrong.
        """
        try:
            return Month.parse(month)
        except ValueError as error:
            raise ValidationError(str(error), field="month") from error

    async def _watermark(self, stock: Stock) -> Watermark | None:
        """The newest candle already stored for this stock, as a domain value.

        Read *after* the vendor call rather than before it. The gap between planning and
        running a target can be minutes, and a target that was redelivered can be running
        beside its own first attempt — so the freshest possible watermark is the one taken
        closest to the write.
        """
        newest = await self.candles.get_latest_for_stock(self.session, stock.stock_id)
        return None if newest is None else Watermark(date=newest.date, time=newest.time)

    @staticmethod
    def _row(candle: IntradayCandle, *, stock_id: uuid.UUID) -> dict[str, Any]:
        """One vendor candle as one ``stock_data`` row.

        Three things happen here and nowhere else: the vendor's field names become Anvex's
        columns (:data:`COLUMN_FOR_FIELD`), the parent's id is attached, and every price is
        quantised to the column's scale by :func:`~app.domain.ingest.quantise_price`. The
        client handed over full vendor precision on purpose (ANV-18) and this is the last
        moment before the value is a row.
        """
        row: dict[str, Any] = {
            "stock_id": stock_id,
            "date": candle.date,
            "time": candle.time,
        }
        for field, column in COLUMN_FOR_FIELD.items():
            value = getattr(candle, field)
            row[column] = quantise_price(value, field=field) if field in PRICE_FIELDS else value
        return row


def dispatch_plan(targets: Sequence[IngestTarget], delays: Sequence[int]) -> list[dict[str, Any]]:
    """Pair each target with the delay it should be dispatched after.

    A three-line helper with a reason: it is what lets the fan-out task's dispatch loop be
    tested without a broker, and it is the one place ``strict=True`` guards the invariant
    that there is exactly one delay per target — a silently truncated ``zip`` would drop the
    tail of a fan-out and look like a smaller roster.
    """
    return [
        {"kwargs": target.as_message(), "countdown": delay}
        for target, delay in zip(targets, delays, strict=True)
    ]


__all__ = [
    "BAR_INTERVAL",
    "COLUMN_FOR_FIELD",
    "PRICE_FIELDS",
    "RESOURCE",
    "IngestReport",
    "IngestService",
    "IngestTarget",
    "dispatch_plan",
]
