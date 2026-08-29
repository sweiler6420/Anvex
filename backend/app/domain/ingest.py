"""Pure rules for the intraday ingest: what to ask for, what to keep, what to write.

This module is everything the old ``AverageInvestorService`` ETL decided *between* the
AlphaVantage response and the ``INSERT`` — pulled out of a pandas pipeline and written as
plain functions over plain data. ``app/clients/alphavantage.py`` (ANV-18) deliberately left
all of it here: the client reports what the vendor said, and what any of it *means to Anvex*
is a rule, so it lives in ``app/domain/`` where it can be tested exhaustively without a
socket or a database.

Five rules, and each one replaces a line of the old script:

============================================  ======================================
old ``update_stock_db.py`` / ``get_data.py``  here
============================================  ======================================
``pd.date_range(...)`` / no windowing at all  :func:`months_to_fetch`
``df.time >= 08:05 & df.time <= 17:00``       :func:`select_session_candles`
``df.date >= max_date & ~(… <= max_time)``    :func:`select_new` / :func:`watermark_for`
``df.round({'open_price': 2, …})``            :func:`quantise_price`
*(nothing — it inserted, so it duplicated)*   :func:`dedupe_candle_rows`
============================================  ======================================

Purity is the point (``CLAUDE.md`` §3): plain data in, plain data out, **no clock read** —
``now`` is a required keyword-only argument — no database, no environment and no vendor
model. ``tests/unit/test_domain_ingest.py`` parses this file to keep that true rather than
trusting this paragraph.

The one import worth justifying
-------------------------------

:data:`~app.models.stock.PRICE_PRECISION` and :data:`~app.models.stock.PRICE_SCALE` come
from ``app/models/stock.py`` rather than being retyped here. That is ``CLAUDE.md`` §4's rule
for schemas ("a validator's length cap is *imported* from the model module's constant, never
retyped") applied to the same problem: widening the column must not leave a stale ``4`` in a
rounding rule. It is also precisely the import ANV-18 could **not** make — the client
layer's AST sweep forbids ``app.models`` — which is why quantising is here and not there.

``zoneinfo`` is likewise not a purity violation. It is a lookup table, not an ambient input:
:func:`session_time` returns the same answer on every machine in every timezone, which is
exactly the property the no-clock rule exists to protect.

The trading-hours window: what 08:05 actually meant
---------------------------------------------------

The old ETL kept ``08:05 <= time <= 17:00`` and hardcoded it. 08:05 looks like a typo — the
US regular session is 09:30-16:00 and nothing happens at five past eight — but it is not.
**AlphaVantage labels an intraday bar with the timestamp at the *end* of its interval**: the
first regular five-minute bar of a US session is ``09:35``, not ``09:30``. So ``08:05`` is
the first five-minute bar whose interval *starts* at 08:00, and ``17:00`` is the last bar
whose interval *ends* at 17:00. The window was never ``[08:05, 17:00]``; it was
**``(08:00, 17:00]`` in bar-coverage terms**, and the ``:05`` was an artefact of the bar
width the script happened to request.

**The decision: preserve the behaviour, re-derive the boundary.** The window stays
:data:`SESSION_OPEN` = 08:00 (**exclusive**) to :data:`SESSION_CLOSE` = 17:00
(**inclusive**), which reproduces the old filter bar-for-bar on the five-minute series it
was written for — and, because it is expressed on the interval rather than on one bar
width, does the right thing for the other four intervals AlphaVantage offers. The old
constant would silently discard ``08:01``-``08:04`` from a one-minute series, and keep
nothing before ``09:00`` from a sixty-minute one.

Preserving rather than "correcting" to the regular session (09:30-16:00) is the other half
of the decision, and it is the conservative direction: AlphaVantage returns extended-hours
bars by default, pre- and post-market prints are real trades, and a chart can always narrow
a series it has. It cannot widen one that was never ingested. What was corrected is the
*two* things that were actually wrong — the hidden bar-width assumption above, and the
hidden **timezone** assumption below.

And the timezone the window is expressed in
-------------------------------------------

A candle's ``time`` is exchange-local wall clock carrying no zone of its own; the zone it is
quoted in arrives separately on :attr:`~app.clients.alphavantage.IntradaySeries.timezone`.
ANV-18 carried that field for exactly this rule. So the window is declared in one named zone
(:data:`SESSION_TIMEZONE`) and a candle quoted in any *other* zone is converted into it
before it is compared — which means the rule keeps meaning "eight in the morning at the
exchange" if AlphaVantage ever answers in UTC, instead of quietly shifting by five hours. It
also means the answer does not depend on the machine's own zone, which a naive
``datetime.combine`` would.
"""

from __future__ import annotations

import datetime as dt
import zoneinfo
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Final, Protocol

from app.models.stock import PRICE_PRECISION, PRICE_SCALE

# ---------------------------------------------------------------------------------------
# What this module needs a "candle" to be
# ---------------------------------------------------------------------------------------


class Candle(Protocol):
    """The shape these rules read. Structural, so nothing here imports a vendor model.

    ``app/clients/alphavantage.py``'s :class:`~app.clients.alphavantage.IntradayCandle`
    satisfies it and so does a seven-line test stub, which is the point: ``app/domain/`` sits
    *below* ``app/clients/`` in ``CLAUDE.md`` §3's dependency order and may not import
    upward. The names are the **vendor's** (``open``, not ``open_price``) because that is
    what the client hands over; renaming them onto Anvex's columns is the service's job.
    """

    @property
    def date(self) -> dt.date: ...

    @property
    def time(self) -> dt.time: ...

    @property
    def open(self) -> Decimal: ...

    @property
    def high(self) -> Decimal: ...

    @property
    def low(self) -> Decimal: ...

    @property
    def close(self) -> Decimal: ...

    @property
    def volume(self) -> int: ...


# ---------------------------------------------------------------------------------------
# Months
# ---------------------------------------------------------------------------------------

#: How AlphaVantage spells the ``month=`` parameter, and therefore how a plan names a month.
MONTH_FORMAT: Final[str] = "%Y-%m"

#: Months to reach back on the **first** ingest of a stock that has no candles at all. Two
#: rather than the old backfill script's forty-three: each month is one call against a free
#: tier that allows five a minute, and a first run that queues forty-three of them starves
#: every other ticker for the best part of an hour. A deeper history is a deliberate
#: backfill with an explicit month list, not something a scheduled job does by surprise.
INITIAL_HISTORY_MONTHS: Final[int] = 2

#: Ceiling on how many months one planning pass may ask for **per stock**. See
#: :func:`months_to_fetch` for why the *newest* months win when the span is longer.
MAX_MONTHS_PER_STOCK: Final[int] = 3


@dataclass(frozen=True, slots=True, order=True)
class Month:
    """A calendar month, which is the only granularity AlphaVantage's ``month=`` accepts.

    Ordered, so "every month from here to now" is a range rather than a date-library
    incantation, and frozen so a plan cannot be edited after it has been reported.
    """

    year: int
    month: int

    def __post_init__(self) -> None:
        if not 1 <= self.month <= 12:
            raise ValueError(f"{self.month} is not a month")

    @classmethod
    def of(cls, day: dt.date) -> Month:
        """The month a date falls in."""
        return cls(day.year, day.month)

    @classmethod
    def parse(cls, text: str) -> Month:
        """``"2024-01"`` → ``Month(2024, 1)``.

        The inverse of :meth:`__str__`, so a month that travelled through a Celery message
        as a string comes back as the same value. A malformed string is a ``ValueError``
        here rather than a vendor round trip that returns an ``Error Message``.
        """
        try:
            parsed = dt.datetime.strptime(text.strip(), MONTH_FORMAT)
        except (AttributeError, ValueError) as error:
            raise ValueError(f"{text!r} is not a YYYY-MM month") from error
        return cls(parsed.year, parsed.month)

    def __str__(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    @property
    def ordinal(self) -> int:
        """Months since year 0, so arithmetic does not have to know about December."""
        return self.year * 12 + (self.month - 1)

    def shift(self, months: int) -> Month:
        """This month, ``months`` later (or earlier, for a negative offset)."""
        moved = self.ordinal + months
        return Month(moved // 12, moved % 12 + 1)


def months_to_fetch(
    *,
    latest: dt.date | None,
    now: dt.datetime,
    history: int = INITIAL_HISTORY_MONTHS,
    limit: int = MAX_MONTHS_PER_STOCK,
) -> tuple[Month, ...]:
    """Which months are worth asking the vendor for, oldest first.

    :param latest: the date of the newest candle already stored, or ``None`` when the stock
        has none at all.
    :param now: the reference time, **timezone-aware**. Converted into
        :data:`SESSION_TIMEZONE` before its month is taken, because ``month=2024-02`` names
        the *exchange's* February — and for four hours either side of a month boundary the
        exchange and UTC disagree about which month it is.
    :param history: how far back a first ingest reaches. Must be at least 1.
    :param limit: the most months one pass may return. Must be at least 1.
    :returns: ``()`` never — there is always at least the current month to refresh.
    :raises ValueError: ``now`` is naive, or ``history``/``limit`` is below 1.

    **Nothing stored** → the current month and :data:`INITIAL_HISTORY_MONTHS` - 1 before it.

    **Something stored** → every month from the newest stored candle's month through the
    current one. Fetching the stored candle's own month again is deliberate and not waste:
    that month is precisely the one that is *incomplete*, and re-requesting it is how the
    days since the last run arrive. Months in between are included so a stock that was
    missed for six weeks does not end up with a hole.

    **Stored data dated in the future** → just the current month. A candle stamped after
    ``now`` means a vendor clock skew or a bad row, and neither is a reason to ask for
    months that have not happened.

    **A span longer than** ``limit`` → the **newest** ``limit`` months. This is the one
    place the rule is lossy, and it is lossy on purpose in the only direction that
    converges: the watermark it is derived from is a *maximum*, so a run that fetched the
    oldest months instead would not move it and the next run would compute the same span
    forever. Taking the newest brings the series up to date in one pass; a gap older than
    the watermark is invisible to a maximum in any case, and closing one is a backfill with
    an explicit month list rather than something a scheduled job can infer.
    """
    _require_aware(now, "now")
    if history < 1:
        raise ValueError("history must be at least one month")
    if limit < 1:
        raise ValueError("limit must be at least one month")

    current = Month.of(now.astimezone(SESSION_ZONE).date())
    first = current.shift(-(history - 1)) if latest is None else min(Month.of(latest), current)
    span = [first.shift(step) for step in range(current.ordinal - first.ordinal + 1)]
    return tuple(span[-limit:])


# ---------------------------------------------------------------------------------------
# The trading-hours window
# ---------------------------------------------------------------------------------------

#: The zone :data:`SESSION_OPEN` and :data:`SESSION_CLOSE` are expressed in — the exchange's
#: own clock. A candle quoted in a *different* zone is converted into this one before it is
#: compared, which is what makes the rule mean "eight in the morning at the exchange" rather
#: than "eight in the morning wherever the vendor felt like quoting".
SESSION_TIMEZONE: Final[str] = "US/Eastern"

#: Resolved once. ``ZoneInfo`` instances are immutable, cached by the standard library and
#: hold no connection — this is a constant, not a resource.
SESSION_ZONE: Final[zoneinfo.ZoneInfo] = zoneinfo.ZoneInfo(SESSION_TIMEZONE)

#: **Exclusive** lower bound on a bar's label, and inclusive upper bound — see the module
#: docstring for why that reproduces the old ``08:05 <= t <= 17:00`` exactly on a
#: five-minute series while also being right for the other four intervals.
SESSION_OPEN: Final[dt.time] = dt.time(8, 0)
SESSION_CLOSE: Final[dt.time] = dt.time(17, 0)


def resolve_zone(timezone: str | None) -> zoneinfo.ZoneInfo:
    """The zone a candle's wall clock is quoted in.

    ``None`` — the vendor omitted its metadata block — is taken to be
    :data:`SESSION_TIMEZONE`, which is what AlphaVantage has always answered for US
    equities. That is a *documented assumption*, not a silent one: it is the only value that
    leaves the old behaviour unchanged, and the alternative (dropping the whole response)
    would turn a missing advisory field into an outage.

    An unrecognised zone name is a :class:`ValueError` rather than the same assumption.
    Guessing there would put every candle of the run in the wrong band and there would be
    nothing in the data to say so afterwards — a wrong number that looks right is the one
    failure mode this whole module exists to design out.
    """
    if timezone is None:
        return SESSION_ZONE
    try:
        return zoneinfo.ZoneInfo(timezone)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError(f"unknown series timezone {timezone!r}") from error


def session_time(candle: Candle, *, zone: zoneinfo.ZoneInfo) -> dt.time:
    """A candle's label as a wall-clock time **in the session's zone**.

    A no-op — and deliberately not merely an equivalent one — when the candle is already
    quoted in that zone: no conversion happens at all, so a DST-ambiguous local time keeps
    the label the vendor gave it. When the zones differ, the label is resolved with
    ``fold=0`` (the standard library's default: the *first* of a repeated hour), which is
    arbitrary for exactly one hour a year and deterministic, which is the property that
    matters here.
    """
    if zone is SESSION_ZONE or zone == SESSION_ZONE:
        return candle.time
    quoted = dt.datetime.combine(candle.date, candle.time, tzinfo=zone)
    return quoted.astimezone(SESSION_ZONE).time()


def in_session(candle: Candle, *, zone: zoneinfo.ZoneInfo) -> bool:
    """Whether this bar covers time Anvex stores: ``(08:00, 17:00]`` at the exchange."""
    at = session_time(candle, zone=zone)
    return SESSION_OPEN < at <= SESSION_CLOSE


def select_session_candles[CandleT: Candle](
    candles: Sequence[CandleT], *, timezone: str | None
) -> tuple[CandleT, ...]:
    """The candles inside the trading window, in the order they arrived.

    Generic over the candle type so the service hands in vendor models and gets vendor
    models back — no lossy domain copy it would have to translate twice (``CLAUDE.md`` §3).

    Order is **preserved rather than sorted**: the vendor lists newest first and sorting
    here would hide that from the caller for no benefit, since the write is keyed on
    ``(stock_id, date, time)`` and does not care.

    :param timezone: :attr:`~app.clients.alphavantage.IntradaySeries.timezone`, straight
        from the vendor. See :func:`resolve_zone` for ``None`` and for an unknown name.
    :raises ValueError: ``timezone`` names a zone that does not exist.
    """
    zone = resolve_zone(timezone)
    return tuple(candle for candle in candles if in_session(candle, zone=zone))


# ---------------------------------------------------------------------------------------
# What is genuinely new
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, order=True)
class Watermark:
    """The newest ``(date, time)`` already stored for a stock.

    A *maximum*, and it is worth being precise about what a maximum can and cannot prove.
    It proves that everything strictly after it is absent. It proves **nothing** about
    anything at or before it — a hole in last Tuesday is entirely compatible with it — which
    is why :func:`watermark_for` refuses to apply it to a month it cannot speak for, and why
    the safety net is the upsert rather than this rule.
    """

    date: dt.date
    time: dt.time

    @property
    def month(self) -> Month:
        return Month.of(self.date)


def watermark_for(month: Month, *, latest: Watermark | None) -> Watermark | None:
    """The watermark that applies to one month's response, if any.

    ``None`` — meaning "keep everything this month returned" — when the stock has no candles
    at all, or when ``month`` is **older** than the watermark's own month. In that second
    case the watermark is genuinely uninformative: it is a maximum, so it says nothing about
    whether January is complete, and filtering January against it would discard the entire
    response. Keeping it all is safe because the write is an
    ``INSERT … ON CONFLICT DO UPDATE`` on the natural key — re-observing a stored candle
    rewrites it with the vendor's current value, which is a correction, not a duplicate.

    For the watermark's own month and anything later, the watermark applies and
    :func:`select_new` trims the response to the part that cannot already be there.
    """
    if latest is None or month < latest.month:
        return None
    return latest


def is_new(candle: Candle, *, watermark: Watermark | None) -> bool:
    """Whether this candle is strictly after everything already stored.

    Compared as a ``(date, time)`` pair rather than as two independent tests, which is the
    bug the old ETL wrote out longhand (``df.date >= max_date`` **and**
    ``~(df.date == max_date & df.time <= max_time)``) and got right — but only by writing
    both halves. A tuple comparison is the same rule with nowhere for a half to go missing.
    """
    if watermark is None:
        return True
    return (candle.date, candle.time) > (watermark.date, watermark.time)


def select_new[CandleT: Candle](
    candles: Sequence[CandleT], *, watermark: Watermark | None
) -> tuple[CandleT, ...]:
    """The candles the database cannot already hold, in the order they arrived.

    A filter, not a correctness guarantee: the guarantee is the upsert. What this buys is
    the rows *not* written — on a normal run of a five-minute series that is the difference
    between rewriting a month of bars and appending an afternoon of them.
    """
    return tuple(candle for candle in candles if is_new(candle, watermark=watermark))


# ---------------------------------------------------------------------------------------
# Fitting a vendor price into the column
# ---------------------------------------------------------------------------------------

#: The smallest difference ``NUMERIC(12, 4)`` can represent: ``Decimal("0.0001")``.
PRICE_QUANTUM: Final[Decimal] = Decimal(1).scaleb(-PRICE_SCALE)

#: The first magnitude the column cannot hold. ``NUMERIC(12, 4)`` has eight digits left of
#: the point, so ``100000000`` overflows and anything below it does not.
PRICE_LIMIT: Final[Decimal] = Decimal(10) ** (PRICE_PRECISION - PRICE_SCALE)


def quantise_price(value: Decimal, *, field: str = "price") -> Decimal:
    """A vendor price as the exact value the ``NUMERIC(12, 4)`` column will hold.

    ANV-18 parsed the vendor's *string* straight into :class:`~decimal.Decimal` and
    deliberately did not round: rounding is lossy and irreversible, and the scale to round
    *to* lives in ``app/models/`` which the client layer may not import. This is where that
    debt is paid, at the last moment before the value becomes a row.

    **Half rounds away from zero** (``ROUND_HALF_UP``), not Python's banker's default,
    because that is what Postgres does when it coerces a value into a ``NUMERIC`` — so the
    number written here is byte-identical to the number the database would have written
    itself, and a ``SELECT`` after an ``INSERT`` returns what the ingest computed rather
    than something a rounding mode away from it.

    :param field: named in the error, so a rejected candle says *which* of five numbers was
        impossible instead of leaving the reader to guess.
    :raises ValueError: the value is not finite, or does not fit the column. Both are
        refusals rather than repairs, and that is the whole point: the old ETL's
        ``pd.to_numeric(errors="coerce")`` turned an unparseable price into a ``NaN`` and
        wrote it, and a ``NUMERIC`` overflow is a ``DataError`` that aborts the transaction
        *after* an arbitrary amount of the batch has been built. Refusing here names the
        field and the value.
    """
    if not value.is_finite():
        raise ValueError(f"{field} is not a finite number: {value}")
    try:
        quantised = value.quantize(PRICE_QUANTUM, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ArithmeticError) as error:
        # `Decimal("1E+40").quantize(...)` cannot be represented in the default context.
        raise ValueError(f"{field} is too large for the column: {value}") from error
    if quantised.copy_abs() >= PRICE_LIMIT:
        raise ValueError(f"{field} overflows NUMERIC({PRICE_PRECISION}, {PRICE_SCALE}): {value}")
    return quantised


# ---------------------------------------------------------------------------------------
# Making a batch safe to hand to `bulk_upsert`
# ---------------------------------------------------------------------------------------

#: The columns one candle is identified by — the unique constraint ANV-7 declared and the
#: ``ON CONFLICT`` target ``StockDataRepo.bulk_upsert`` infers from it. Spelled here rather
#: than imported from ``app/repos/`` because ``app/domain/`` does not depend on the data
#: layer; ``tests/unit/test_domain_ingest.py`` asserts the two agree, so the duplication
#: cannot drift.
NATURAL_KEY: Final[tuple[str, ...]] = ("stock_id", "date", "time")

#: The key of a row, as it appears in :attr:`CandleBatch.duplicates`.
type CandleKey = tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class CandleBatch:
    """Rows that are safe to hand to ``bulk_upsert``, and what was dropped to get there.

    ``duplicates`` names the keys that appeared more than once, in the order they were first
    seen. Reported rather than swallowed, exactly as :class:`~app.domain.politician.
    RosterBatch` reports a repeated roster id: a candle arriving twice is normal (two
    adjacent months overlap at a boundary, and a retried task refetches a month it already
    fetched), so it is not an error — but ``fetched - written`` should be explained rather
    than merely observed.
    """

    rows: tuple[dict[str, Any], ...]
    duplicates: tuple[CandleKey, ...]

    @property
    def has_duplicates(self) -> bool:
        return bool(self.duplicates)

    @property
    def deduplicated(self) -> int:
        """How many rows were dropped before the statement was built."""
        return len(self.duplicates)


def candle_key(row: Mapping[str, Any]) -> CandleKey:
    """The ``(stock_id, date, time)`` a row will conflict on.

    :raises ValueError: the row is missing one of :data:`NATURAL_KEY`. A row without its own
        key is not a candle, and the alternative is a ``NOT NULL`` violation halfway through
        a statement that names a column rather than a row.
    """
    missing = [column for column in NATURAL_KEY if row.get(column) is None]
    if missing:
        raise ValueError(f"candle row is missing {', '.join(missing)}")
    return tuple(row[column] for column in NATURAL_KEY)


def dedupe_candle_rows(rows: Iterable[Mapping[str, Any]]) -> CandleBatch:
    """Collapse a batch to one row per ``(stock_id, date, time)``. **The last one wins.**

    This is not tidiness. ``StockDataRepo.bulk_upsert`` is a single
    ``INSERT … ON CONFLICT DO UPDATE`` and Postgres refuses a statement whose ``VALUES`` hit
    one conflict target twice — ``ON CONFLICT DO UPDATE command cannot affect row a second
    time``. The repo deliberately does no deduplication (``CLAUDE.md`` §3: a repo holds no
    rules, and *which* duplicate wins is a rule), so the batch arrives here first.

    That makes a **single run** safe. It is a different property from the upsert's
    idempotency **across** runs, and neither substitutes for the other: the upsert stops a
    second run inserting duplicates, this stops a first run crashing on one.

    Last wins because that is what the caller would have got had the rows been applied one
    at a time — two sequential upserts of one candle leave the second one's values in the
    table — so a batch must not disagree with the loop it replaces. In practice the later
    row is also the better one: it comes from the more recent month's response, and
    AlphaVantage revises a bar's volume after the close.

    Ordering is the order of **first** appearance, so the same input builds the same
    statement every time and two ingest logs diff cleanly.

    :raises ValueError: a row is missing part of its key — see :func:`candle_key`.
    """
    kept: dict[CandleKey, dict[str, Any]] = {}
    duplicates: list[CandleKey] = []
    for row in rows:
        key = candle_key(row)
        if key in kept and key not in duplicates:
            duplicates.append(key)
        kept[key] = dict(row)
    return CandleBatch(rows=tuple(kept.values()), duplicates=tuple(duplicates))


# ---------------------------------------------------------------------------------------
# Pacing the fan-out
# ---------------------------------------------------------------------------------------

#: AlphaVantage's free tier. Documented as five calls a minute, and the vendor answers the
#: sixth with a ``200`` carrying a ``"Note"`` — which ANV-18's client turns into an
#: ``ExternalServiceError`` with ``details["reason"] == "rate_limited"``.
FREE_TIER_CALLS_PER_MINUTE: Final[int] = 5

#: Seconds between two dispatched calls. Fifteen is four a minute — one clear of the
#: ceiling, so a little jitter in delivery cannot push a run over it. This is the number the
#: old ETL spent as ``time.sleep(10)`` **on the calling thread**; here it is a ``countdown``
#: on a message, so nothing waits.
CALL_SPACING_SECONDS: Final[int] = 15

#: The most vendor calls one scheduled fan-out may dispatch. Twenty at
#: :data:`CALL_SPACING_SECONDS` apart is a five-minute run, which has to finish comfortably
#: inside the beat interval that started it — ``tests/unit/test_jobs_celery_app.py`` asserts
#: that relationship, because the failure mode (two overlapping fan-outs, and therefore
#: double the call rate the spacing was chosen for) is invisible until the roster grows.
MAX_CALLS_PER_RUN: Final[int] = 20


def fan_out_order[KeyT](
    plans: Sequence[tuple[KeyT, Sequence[Month]]], *, limit: int = MAX_CALLS_PER_RUN
) -> tuple[tuple[KeyT, Month], ...]:
    """Flatten per-stock month plans into one dispatch order, and cut it to ``limit``.

    **Newest month first, round-robin across stocks.** Both halves are decisions about what
    a truncated run should have achieved, and the naive alternative — concatenating each
    stock's whole plan in turn — gets both wrong. It would spend the first stock's three
    months before the second stock's *current* month, so a roster of fifteen stocks with a
    twenty-call budget would leave five of them untouched every single run, forever, while
    the first seven were backfilled. Round-robin means every stock is brought up to date
    before any stock is taken back a second month, and the truncation falls on the oldest
    month of the last stock rather than on a whole stock.

    ``plans`` carries each stock's months **oldest first**, as :func:`months_to_fetch`
    returns them; this reverses each before interleaving, so round 0 is every stock's
    current month.

    :param limit: the quota this run is allowed to spend. Must be at least 1.
    :raises ValueError: ``limit`` is below 1.
    """
    if limit < 1:
        raise ValueError("limit must be at least one call")

    ordered: list[tuple[KeyT, Month]] = []
    reversed_plans = [(key, list(reversed(months))) for key, months in plans]
    depth = max((len(months) for _, months in reversed_plans), default=0)
    for round_index in range(depth):
        for key, months in reversed_plans:
            if round_index < len(months):
                ordered.append((key, months[round_index]))
    return tuple(ordered[:limit])


def dispatch_delays(count: int, *, spacing: int = CALL_SPACING_SECONDS) -> tuple[int, ...]:
    """``(0, 15, 30, …)`` — how long each of ``count`` fanned-out calls waits before running.

    The whole of Anvex's rate limiting, and it is arithmetic rather than a sleep. The old
    ETL paced its calls with ``time.sleep(10)`` between them, which holds the calling
    process for the entire run; a prefork Celery child doing the same is a worker slot
    nobody else can have for ten minutes. A delay attached to a *message* costs nothing:
    the work does not exist yet.

    :param count: how many calls are being dispatched. ``0`` is ``()``.
    :param spacing: seconds between consecutive calls.
    :raises ValueError: ``count`` is negative, or ``spacing`` is not positive — a spacing of
        zero is not "as fast as possible", it is "no rate limit", and it should have to be
        spelled that way rather than arrived at by leaving a constant unset.
    """
    if count < 0:
        raise ValueError("cannot dispatch a negative number of calls")
    if spacing <= 0:
        raise ValueError("spacing must be a positive number of seconds")
    return tuple(index * spacing for index in range(count))


# ---------------------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------------------


def _require_aware(moment: dt.datetime, name: str) -> None:
    """``CLAUDE.md`` §4: a naive datetime would silently resolve in the server's own zone."""
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise ValueError(f"{name} must be timezone-aware")


__all__ = [
    "CALL_SPACING_SECONDS",
    "FREE_TIER_CALLS_PER_MINUTE",
    "INITIAL_HISTORY_MONTHS",
    "MAX_CALLS_PER_RUN",
    "MAX_MONTHS_PER_STOCK",
    "MONTH_FORMAT",
    "NATURAL_KEY",
    "PRICE_LIMIT",
    "PRICE_QUANTUM",
    "SESSION_CLOSE",
    "SESSION_OPEN",
    "SESSION_TIMEZONE",
    "SESSION_ZONE",
    "Candle",
    "CandleBatch",
    "CandleKey",
    "Month",
    "Watermark",
    "candle_key",
    "dedupe_candle_rows",
    "dispatch_delays",
    "fan_out_order",
    "in_session",
    "is_new",
    "months_to_fetch",
    "quantise_price",
    "resolve_zone",
    "select_new",
    "select_session_candles",
    "session_time",
    "watermark_for",
]
