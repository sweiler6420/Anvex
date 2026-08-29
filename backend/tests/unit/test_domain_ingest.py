"""Unit tests for ``app.domain.ingest`` — the heart of ANV-22 and the old ETL's worst area.

Every rule here was a line in ``AverageInvestorService`` that nothing could test: the month
list was a ``pd.date_range`` in a ``__main__`` block, the trading-hours filter was two
``datetime.strptime`` calls inside a DataFrame mask, the "what is new" rule was two boolean
masks that had to agree, and the rounding was a magic ``2``. All five are pure functions
now, so the boundaries are cheap to pin — and pinning them is the point, because a candle
silently dropped or silently rounded is not a failure anybody notices.

The stub below is seven lines and imports nothing from ``app.clients``. That is the test for
the layering as much as for the rule: if these functions could only be exercised with a
vendor model, they would be in the wrong package (``CLAUDE.md`` §3).
"""

from __future__ import annotations

import ast
import datetime as dt
import uuid
import zoneinfo
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import ClassVar

import pytest

from app.domain import ingest as domain_ingest
from app.domain.ingest import (
    CALL_SPACING_SECONDS,
    FREE_TIER_CALLS_PER_MINUTE,
    INITIAL_HISTORY_MONTHS,
    MAX_CALLS_PER_RUN,
    MAX_MONTHS_PER_STOCK,
    NATURAL_KEY,
    PRICE_LIMIT,
    PRICE_QUANTUM,
    SESSION_CLOSE,
    SESSION_OPEN,
    SESSION_TIMEZONE,
    SESSION_ZONE,
    CandleBatch,
    Month,
    Watermark,
    candle_key,
    dedupe_candle_rows,
    dispatch_delays,
    fan_out_order,
    in_session,
    is_new,
    months_to_fetch,
    quantise_price,
    resolve_zone,
    select_new,
    select_session_candles,
    session_time,
    watermark_for,
)
from app.models.stock import PRICE_PRECISION, PRICE_SCALE
from app.repos.stock_data import CONFLICT_COLUMNS

#: A Monday lunchtime in UTC, comfortably inside March in every zone that matters.
NOW = dt.datetime(2026, 3, 2, 17, 0, tzinfo=dt.UTC)

STOCK = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER_STOCK = uuid.UUID("22222222-2222-2222-2222-222222222222")


def source_tree() -> ast.Module:
    return ast.parse(Path(domain_ingest.__file__).read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Bar:
    """The smallest thing that satisfies :class:`~app.domain.ingest.Candle`.

    Deliberately not :class:`~app.clients.alphavantage.IntradayCandle`: if these rules needed
    the vendor's model, they would be in the wrong layer.
    """

    date: dt.date = dt.date(2026, 3, 2)
    time: dt.time = dt.time(9, 35)
    open: Decimal = Decimal("100")
    high: Decimal = Decimal("101")
    low: Decimal = Decimal("99")
    close: Decimal = Decimal("100.5")
    volume: int = 1_000


def row(
    *,
    stock_id: uuid.UUID = STOCK,
    day: dt.date = dt.date(2026, 3, 2),
    at: dt.time = dt.time(9, 35),
    close: str = "100.5000",
) -> dict[str, object]:
    """A ``stock_data`` row as the service builds it, for the dedupe rules."""
    return {
        "stock_id": stock_id,
        "date": day,
        "time": at,
        "open_price": Decimal("100.0000"),
        "high_price": Decimal("101.0000"),
        "low_price": Decimal("99.0000"),
        "close_price": Decimal(close),
        "volume": 1_000,
    }


# ---------------------------------------------------------------------------------------
# purity
# ---------------------------------------------------------------------------------------


class TestPurity:
    """``app/domain/`` is pure by rule, and a convention that lives only in prose gets
    broken — so it is parsed out of the source, as ``test_domain_news.py`` does."""

    def test_it_imports_no_framework_no_orm_and_no_vendor(self) -> None:
        """One ``app`` import is allowed, and it is the one ANV-18 could not make.

        ``app.models.stock`` carries ``PRICE_PRECISION``/``PRICE_SCALE``. Importing them is
        ``CLAUDE.md`` §4's "never retype a column's constant" rule, and it is precisely the
        import the client layer's AST sweep forbids — which is *why* quantising is here.
        """
        modules: set[str] = set()
        for node in ast.walk(source_tree()):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                modules.add(node.module)
        roots = {module.split(".")[0] for module in modules}

        assert "fastapi" not in roots
        assert "starlette" not in roots
        assert "sqlalchemy" not in roots
        assert "httpx" not in roots
        assert "pandas" not in roots
        assert "app.settings" not in modules
        assert {module for module in modules if module.startswith("app")} == {"app.models.stock"}

    def test_it_never_reads_a_clock(self) -> None:
        """``now`` is injected, which is what makes every month boundary below exact."""
        clock_calls = {"now", "utcnow", "today", "monotonic", "perf_counter", "time_ns"}
        offenders = [
            name
            for node in ast.walk(source_tree())
            if isinstance(node, ast.Call)
            for name in [
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else getattr(node.func, "id", None)
            ]
            if name in clock_calls
        ]

        assert offenders == [], f"domain/ingest.py must take a clock as a parameter: {offenders}"

    def test_it_writes_no_sql_reads_no_environment_and_never_sleeps(self) -> None:
        source = Path(domain_ingest.__file__).read_text(encoding="utf-8")

        assert "select(" not in source
        assert "get_settings" not in source
        assert "os.environ" not in source
        # The old ETL's `time.sleep(10)`. Pacing here is arithmetic, so nothing waits — an
        # AST check rather than a substring one, because the module *discusses* that sleep.
        sleeps = [
            node
            for node in ast.walk(source_tree())
            if isinstance(node, ast.Call)
            and (
                getattr(node.func, "attr", None) == "sleep"
                or getattr(node.func, "id", None) == "sleep"
            )
        ]
        assert sleeps == []


# ---------------------------------------------------------------------------------------
# Month
# ---------------------------------------------------------------------------------------


class TestMonth:
    def test_it_round_trips_through_the_vendors_spelling(self) -> None:
        assert str(Month.parse("2024-01")) == "2024-01"
        assert Month.parse("2024-01") == Month(2024, 1)

    def test_a_single_digit_month_is_padded(self) -> None:
        """AlphaVantage wants ``2024-01``; ``2024-1`` is a 200 with an ``Error Message``."""
        assert str(Month(2024, 1)) == "2024-01"

    def test_surrounding_whitespace_is_not_a_different_month(self) -> None:
        assert Month.parse("  2024-07 ") == Month(2024, 7)

    @pytest.mark.parametrize("text", ["", "2024", "2024-13", "24-01", "January", "2024-01-02"])
    def test_a_malformed_month_is_refused_here_rather_than_by_the_vendor(self, text: str) -> None:
        with pytest.raises(ValueError, match="YYYY-MM"):
            Month.parse(text)

    def test_a_month_outside_one_to_twelve_cannot_be_constructed(self) -> None:
        with pytest.raises(ValueError, match="not a month"):
            Month(2024, 13)

    def test_it_knows_which_month_a_date_falls_in(self) -> None:
        assert Month.of(dt.date(2026, 3, 31)) == Month(2026, 3)

    @pytest.mark.parametrize(
        ("start", "offset", "expected"),
        [
            (Month(2026, 3), 0, Month(2026, 3)),
            (Month(2026, 3), 1, Month(2026, 4)),
            (Month(2026, 12), 1, Month(2027, 1)),
            (Month(2026, 1), -1, Month(2025, 12)),
            (Month(2026, 3), -14, Month(2025, 1)),
            (Month(2026, 3), 24, Month(2028, 3)),
        ],
    )
    def test_shifting_crosses_a_year_boundary(
        self, start: Month, offset: int, expected: Month
    ) -> None:
        assert start.shift(offset) == expected

    def test_months_order_chronologically(self) -> None:
        """Ordering is what makes "every month from here to now" a range."""
        assert Month(2025, 12) < Month(2026, 1) < Month(2026, 2)
        assert min(Month(2026, 5), Month(2026, 2)) == Month(2026, 2)


# ---------------------------------------------------------------------------------------
# which months to fetch
# ---------------------------------------------------------------------------------------


class TestMonthsToFetch:
    def test_nothing_stored_reaches_back_for_a_bounded_history(self) -> None:
        assert months_to_fetch(latest=None, now=NOW) == (Month(2026, 2), Month(2026, 3))
        assert len(months_to_fetch(latest=None, now=NOW)) == INITIAL_HISTORY_MONTHS

    def test_a_history_of_one_asks_only_for_the_current_month(self) -> None:
        assert months_to_fetch(latest=None, now=NOW, history=1) == (Month(2026, 3),)

    def test_one_candle_stored_this_month_re_requests_that_month(self) -> None:
        """The stored month is the *incomplete* one, so refetching it is the whole point."""
        assert months_to_fetch(latest=dt.date(2026, 3, 2), now=NOW) == (Month(2026, 3),)

    def test_up_to_date_is_still_one_call_not_zero(self) -> None:
        """There is always something to do: today's bars since the last run."""
        assert months_to_fetch(latest=dt.date(2026, 3, 31), now=NOW) == (Month(2026, 3),)

    def test_a_stock_last_seen_in_the_previous_month_gets_both(self) -> None:
        assert months_to_fetch(latest=dt.date(2026, 2, 27), now=NOW) == (
            Month(2026, 2),
            Month(2026, 3),
        )

    def test_a_gap_is_covered_month_by_month(self) -> None:
        """A stock missed for six weeks must not end up with a hole in the middle."""
        assert months_to_fetch(latest=dt.date(2026, 1, 9), now=NOW) == (
            Month(2026, 1),
            Month(2026, 2),
            Month(2026, 3),
        )

    def test_a_span_longer_than_the_limit_keeps_the_newest_months(self) -> None:
        """The lossy direction is the only one that converges — see the docstring.

        The watermark is a *maximum*, so a run that fetched the oldest months instead would
        not move it and the next run would compute the same span forever.
        """
        months = months_to_fetch(latest=dt.date(2023, 5, 1), now=NOW)

        assert len(months) == MAX_MONTHS_PER_STOCK
        assert months == (Month(2026, 1), Month(2026, 2), Month(2026, 3))

    def test_data_dated_in_the_future_asks_only_for_the_current_month(self) -> None:
        """A clock skew upstream is not a reason to request months that have not happened."""
        assert months_to_fetch(latest=dt.date(2027, 8, 4), now=NOW) == (Month(2026, 3),)

    def test_the_current_month_is_the_exchanges_not_utcs(self) -> None:
        """Four hours a month, UTC and New York disagree about which month it is.

        02:00 UTC on 1 March is 21:00 on 28 February in ``US/Eastern``, and ``month=2026-03``
        would ask AlphaVantage for a month the exchange has not started.
        """
        boundary = dt.datetime(2026, 3, 1, 2, 0, tzinfo=dt.UTC)

        assert months_to_fetch(latest=None, now=boundary, history=1) == (Month(2026, 2),)

    def test_the_answer_does_not_depend_on_how_now_is_spelled(self) -> None:
        """The same instant in two zones is the same month."""
        tokyo = NOW.astimezone(zoneinfo.ZoneInfo("Asia/Tokyo"))

        assert months_to_fetch(latest=None, now=tokyo) == months_to_fetch(latest=None, now=NOW)

    def test_a_naive_now_is_refused(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            months_to_fetch(latest=None, now=dt.datetime(2026, 3, 2, 12, 0))

    @pytest.mark.parametrize("history", [0, -1])
    def test_a_history_below_one_month_is_refused(self, history: int) -> None:
        with pytest.raises(ValueError, match="history"):
            months_to_fetch(latest=None, now=NOW, history=history)

    @pytest.mark.parametrize("limit", [0, -3])
    def test_a_limit_below_one_month_is_refused(self, limit: int) -> None:
        with pytest.raises(ValueError, match="limit"):
            months_to_fetch(latest=None, now=NOW, limit=limit)

    def test_the_result_is_always_oldest_first(self) -> None:
        months = months_to_fetch(latest=dt.date(2026, 1, 9), now=NOW)

        assert list(months) == sorted(months)


# ---------------------------------------------------------------------------------------
# the trading-hours window
# ---------------------------------------------------------------------------------------


class TestTheSessionWindow:
    """The 08:05-17:00 filter, re-derived.

    AlphaVantage labels a bar with the timestamp at the *end* of its interval, so the old
    constant ``08:05`` was the first five-minute bar covering 08:00. The window is therefore
    ``(08:00, 17:00]`` on the label, which reproduces the old behaviour bar-for-bar on a
    five-minute series and is also right for the other four intervals.
    """

    def test_the_window_is_the_old_one_expressed_on_the_interval(self) -> None:
        assert (dt.time(8, 0), dt.time(17, 0)) == (SESSION_OPEN, SESSION_CLOSE)
        assert SESSION_TIMEZONE == "US/Eastern"

    @pytest.mark.parametrize(
        ("at", "kept"),
        [
            (dt.time(0, 0), False),
            (dt.time(4, 0), False),  # AlphaVantage's own pre-market start
            (dt.time(7, 55), False),
            (dt.time(8, 0), False),  # exactly the open: a bar *ending* at 08:00 covers 07:55
            (dt.time(8, 5), True),  # the old lower bound, unchanged
            (dt.time(9, 35), True),  # the first regular-session five-minute bar
            (dt.time(16, 0), True),
            (dt.time(16, 59, 59), True),
            (dt.time(17, 0), True),  # exactly the close, inclusive — the old upper bound
            (dt.time(17, 5), False),
            (dt.time(20, 0), False),  # AlphaVantage's own post-market end
            (dt.time(23, 59), False),
        ],
    )
    def test_each_edge_of_the_day(self, at: dt.time, kept: bool) -> None:
        assert in_session(Bar(time=at), zone=SESSION_ZONE) is kept

    def test_a_one_minute_bar_at_eight_oh_one_is_kept(self) -> None:
        """The correction. The old hardcoded ``08:05`` silently dropped 08:01-08:04."""
        assert in_session(Bar(time=dt.time(8, 1)), zone=SESSION_ZONE) is True

    def test_an_hourly_bar_at_nine_is_kept_and_at_eight_is_not(self) -> None:
        """A 60-minute bar labelled 08:00 covers 07:00-08:00, which is outside the window."""
        assert in_session(Bar(time=dt.time(9, 0)), zone=SESSION_ZONE) is True
        assert in_session(Bar(time=dt.time(8, 0)), zone=SESSION_ZONE) is False

    def test_a_vendor_quoting_utc_is_converted_not_assumed(self) -> None:
        """The zone ANV-18 carried, used. 13:05 UTC is 08:05 in New York in March.

        Hardcoding the zone — which the old ETL did by omission — would keep 08:05 UTC, five
        hours of trading away from what the rule means.
        """
        utc = resolve_zone("UTC")
        winter = dt.date(2026, 1, 15)

        assert in_session(Bar(date=winter, time=dt.time(13, 5)), zone=utc) is True
        assert in_session(Bar(date=winter, time=dt.time(12, 5)), zone=utc) is False
        assert in_session(Bar(date=winter, time=dt.time(22, 5)), zone=utc) is False

    def test_a_zone_that_is_not_the_machines_gives_the_same_answer_anywhere(self) -> None:
        """Tokyo's 22:05 on 15 January is 08:05 the same morning in New York.

        Computed from the two named zones and nothing else, so the result cannot depend on
        the developer's own ``TZ`` — which a bare ``datetime.combine`` would have let it.
        """
        tokyo = resolve_zone("Asia/Tokyo")
        bar = Bar(date=dt.date(2026, 1, 15), time=dt.time(22, 5))

        assert session_time(bar, zone=tokyo) == dt.time(8, 5)
        assert in_session(bar, zone=tokyo) is True

    def test_a_candle_already_in_the_session_zone_is_not_converted_at_all(self) -> None:
        """Not merely "converted to the same thing" — the label is returned untouched.

        01:30 on the morning US clocks go back happens twice, so a conversion would have to
        pick one. A candle already quoted at the exchange keeps the label the vendor gave it.
        """
        ambiguous = Bar(date=dt.date(2026, 11, 1), time=dt.time(1, 30))

        assert session_time(ambiguous, zone=SESSION_ZONE) == dt.time(1, 30)

    def test_a_missing_vendor_timezone_falls_back_to_the_exchange(self) -> None:
        """A missing advisory field is not an outage — ANV-18 made ``timezone`` optional."""
        assert resolve_zone(None) is SESSION_ZONE

    def test_an_unknown_timezone_is_refused_rather_than_guessed(self) -> None:
        """Guessing would put every candle of the run in the wrong band, silently."""
        with pytest.raises(ValueError, match="unknown series timezone"):
            resolve_zone("Mars/Olympus_Mons")

    def test_selecting_keeps_the_vendors_order(self) -> None:
        """Newest first, as AlphaVantage lists them. Sorting here would hide that for nothing."""
        bars = [
            Bar(time=dt.time(16, 0)),
            Bar(time=dt.time(7, 0)),
            Bar(time=dt.time(9, 35)),
            Bar(time=dt.time(20, 0)),
        ]

        kept = select_session_candles(bars, timezone="US/Eastern")

        assert [bar.time for bar in kept] == [dt.time(16, 0), dt.time(9, 35)]

    def test_selecting_returns_the_types_it_was_given(self) -> None:
        """Generic over the candle type, so the service never translates a vendor model."""
        bars = [Bar(time=dt.time(9, 35))]

        assert select_session_candles(bars, timezone=None)[0] is bars[0]

    def test_an_empty_series_is_an_empty_selection(self) -> None:
        assert select_session_candles([], timezone="US/Eastern") == ()

    def test_a_day_of_only_extended_hours_prints_can_be_entirely_filtered_away(self) -> None:
        """``fetched`` well above zero with ``written`` zero is a real, correct outcome."""
        bars = [Bar(time=dt.time(4, 5)), Bar(time=dt.time(19, 55))]

        assert select_session_candles(bars, timezone="US/Eastern") == ()


# ---------------------------------------------------------------------------------------
# what is genuinely new
# ---------------------------------------------------------------------------------------


class TestWhatIsNew:
    WATERMARK = Watermark(date=dt.date(2026, 3, 2), time=dt.time(12, 0))

    def test_everything_is_new_when_nothing_is_stored(self) -> None:
        assert is_new(Bar(date=dt.date(1999, 1, 4)), watermark=None) is True

    @pytest.mark.parametrize(
        ("day", "at", "expected"),
        [
            (dt.date(2026, 3, 2), dt.time(12, 5), True),  # same day, later
            (dt.date(2026, 3, 2), dt.time(12, 0), False),  # exactly the watermark
            (dt.date(2026, 3, 2), dt.time(11, 55), False),  # same day, earlier
            (dt.date(2026, 3, 3), dt.time(9, 35), True),  # next day, *earlier* clock time
            (dt.date(2026, 3, 1), dt.time(23, 55), False),  # previous day, later clock time
        ],
    )
    def test_the_comparison_is_on_the_pair_not_on_two_fields(
        self, day: dt.date, at: dt.time, expected: bool
    ) -> None:
        """The two rows that matter are 3 and 4.

        The old ETL wrote this as ``df.date >= max_date`` **and**
        ``~(df.date == max_date & df.time <= max_time)`` — correct, but only because both
        halves were present. A tuple comparison has nowhere for a half to go missing.
        """
        assert is_new(Bar(date=day, time=at), watermark=self.WATERMARK) is expected

    def test_selecting_keeps_order_and_returns_the_types_it_was_given(self) -> None:
        bars = [
            Bar(date=dt.date(2026, 3, 3), time=dt.time(9, 35)),
            Bar(date=dt.date(2026, 3, 2), time=dt.time(11, 0)),
            Bar(date=dt.date(2026, 3, 2), time=dt.time(15, 0)),
        ]

        kept = select_new(bars, watermark=self.WATERMARK)

        assert [(bar.date, bar.time) for bar in kept] == [
            (dt.date(2026, 3, 3), dt.time(9, 35)),
            (dt.date(2026, 3, 2), dt.time(15, 0)),
        ]

    def test_no_watermark_keeps_everything_in_order(self) -> None:
        bars = [Bar(time=dt.time(9, 35)), Bar(time=dt.time(9, 30))]

        assert select_new(bars, watermark=None) == tuple(bars)

    def test_a_watermark_knows_its_own_month(self) -> None:
        assert self.WATERMARK.month == Month(2026, 3)


class TestWhichWatermarkApplies:
    LATEST = Watermark(date=dt.date(2026, 3, 2), time=dt.time(12, 0))

    def test_a_stock_with_no_candles_has_no_watermark_for_any_month(self) -> None:
        assert watermark_for(Month(2026, 3), latest=None) is None

    def test_the_watermarks_own_month_is_filtered(self) -> None:
        assert watermark_for(Month(2026, 3), latest=self.LATEST) is self.LATEST

    def test_a_later_month_is_filtered_too(self) -> None:
        """Everything after a maximum really is absent, whichever month it lands in."""
        assert watermark_for(Month(2026, 4), latest=self.LATEST) is self.LATEST

    def test_an_older_month_is_not_filtered_at_all(self) -> None:
        """A maximum says nothing about whether January is complete.

        Filtering January against a March watermark would discard the entire response and
        the hole would never close. Keeping it all is safe because the write is an upsert.
        """
        assert watermark_for(Month(2026, 1), latest=self.LATEST) is None

    def test_an_old_months_candles_therefore_all_survive(self) -> None:
        january = [Bar(date=dt.date(2026, 1, 5), time=dt.time(9, 35))]

        applied = watermark_for(Month(2026, 1), latest=self.LATEST)

        assert select_new(january, watermark=applied) == tuple(january)


# ---------------------------------------------------------------------------------------
# quantisation
# ---------------------------------------------------------------------------------------


class TestQuantisePrice:
    def test_the_quantum_and_the_ceiling_come_from_the_column(self) -> None:
        """Imported, never retyped: widening the column must not leave a stale 4 here."""
        assert Decimal("0.0001") == PRICE_QUANTUM
        assert PRICE_SCALE == 4
        assert Decimal(10) ** (PRICE_PRECISION - PRICE_SCALE) == PRICE_LIMIT

    def test_a_value_that_already_fits_is_unchanged(self) -> None:
        assert quantise_price(Decimal("186.4200")) == Decimal("186.4200")

    def test_the_scale_is_padded_out_rather_than_trimmed(self) -> None:
        """``NUMERIC(12, 4)`` stores four places, and the API serialises the Decimal's own
        exponent — so ``100.5`` must become ``"100.5000"`` before it is written."""
        assert str(quantise_price(Decimal("100.5"))) == "100.5000"

    def test_a_value_that_rounds_up(self) -> None:
        assert quantise_price(Decimal("186.42005678")) == Decimal("186.4201")

    def test_a_value_that_rounds_down(self) -> None:
        assert quantise_price(Decimal("186.42004999")) == Decimal("186.4200")

    def test_a_half_rounds_away_from_zero_not_to_even(self) -> None:
        """The discriminating case, and the reason the rounding mode is stated.

        Python's default is banker's rounding, which would make this ``1.0000``. Postgres
        rounds half away from zero when it coerces into a ``NUMERIC``, so quantising the
        other way would write a number one tick away from what the database would have
        written itself — and a ``SELECT`` after the ``INSERT`` would disagree with the job.
        """
        assert quantise_price(Decimal("1.00005")) == Decimal("1.0001")
        assert quantise_price(Decimal("-1.00005")) == Decimal("-1.0001")

    def test_precision_beyond_the_column_is_lost_deliberately_and_only_there(self) -> None:
        """The client kept full vendor precision on purpose; this is where it is spent."""
        exact = Decimal("1234.56789012345")

        quantised = quantise_price(exact)

        assert quantised == Decimal("1234.5679")
        assert quantised != exact
        assert -quantised.as_tuple().exponent == PRICE_SCALE

    def test_the_largest_value_the_column_holds_is_accepted(self) -> None:
        assert quantise_price(Decimal("99999999.9999")) == Decimal("99999999.9999")

    def test_a_value_that_overflows_only_after_rounding_is_still_refused(self) -> None:
        """The edge that a naive magnitude check before rounding would miss."""
        with pytest.raises(ValueError, match="overflows NUMERIC"):
            quantise_price(Decimal("99999999.99999"))

    @pytest.mark.parametrize("value", ["100000000", "-100000000", "1E+12"])
    def test_a_value_the_column_cannot_hold_is_refused_not_truncated(self, value: str) -> None:
        """A ``DataError`` mid-statement aborts a transaction that is already half built."""
        with pytest.raises(ValueError, match=r"overflow|too large"):
            quantise_price(Decimal(value))

    def test_an_absurd_exponent_is_refused_rather_than_raising_invalidoperation(self) -> None:
        """``Decimal("1E+40").quantize(...)`` cannot be represented in the default context."""
        with pytest.raises(ValueError, match="too large"):
            quantise_price(Decimal("1E+40"))

    @pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
    def test_a_non_finite_price_is_refused(self, value: str) -> None:
        """The pandas bug, one layer on: ``errors="coerce"`` wrote ``NaN`` into ``NUMERIC``.

        ANV-18 rejects these at the parser; this is the second gate, for a value that was
        constructed rather than parsed.
        """
        with pytest.raises(ValueError, match="finite"):
            quantise_price(Decimal(value))

    def test_the_error_names_the_field_that_was_impossible(self) -> None:
        """Five numbers arrive per candle; "which one" should not need a debugger."""
        with pytest.raises(ValueError, match="high"):
            quantise_price(Decimal("NaN"), field="high")


# ---------------------------------------------------------------------------------------
# batch dedupe
# ---------------------------------------------------------------------------------------


class TestDedupeCandleRows:
    def test_the_key_is_the_repos_conflict_target(self) -> None:
        """Spelled in both places; asserted equal so the duplication cannot drift."""
        assert NATURAL_KEY == CONFLICT_COLUMNS

    def test_an_empty_batch_is_an_empty_batch(self) -> None:
        batch = dedupe_candle_rows([])

        assert batch == CandleBatch(rows=(), duplicates=())
        assert batch.has_duplicates is False
        assert batch.deduplicated == 0

    def test_a_clean_batch_passes_through_in_order(self) -> None:
        rows = [row(at=dt.time(9, 35)), row(at=dt.time(9, 40))]

        batch = dedupe_candle_rows(rows)

        assert [r["time"] for r in batch.rows] == [dt.time(9, 35), dt.time(9, 40)]
        assert batch.duplicates == ()

    def test_the_last_occurrence_wins(self) -> None:
        """What the caller would have got had the rows been upserted one at a time — and in
        practice the later row is the better one, from the more recent month's response."""
        batch = dedupe_candle_rows([row(close="100.0000"), row(close="102.0000")])

        assert len(batch.rows) == 1
        assert batch.rows[0]["close_price"] == Decimal("102.0000")

    def test_ordering_is_first_appearance_so_two_runs_diff_cleanly(self) -> None:
        rows = [
            row(at=dt.time(9, 35), close="1.0000"),
            row(at=dt.time(9, 40)),
            row(at=dt.time(9, 35), close="2.0000"),
        ]

        batch = dedupe_candle_rows(rows)

        assert [r["time"] for r in batch.rows] == [dt.time(9, 35), dt.time(9, 40)]
        assert batch.rows[0]["close_price"] == Decimal("2.0000")

    def test_a_key_repeated_three_times_is_reported_once(self) -> None:
        batch = dedupe_candle_rows([row(), row(), row()])

        assert batch.duplicates == ((STOCK, dt.date(2026, 3, 2), dt.time(9, 35)),)
        assert batch.deduplicated == 1
        assert batch.has_duplicates is True

    def test_the_same_minute_for_two_stocks_is_two_candles(self) -> None:
        """``stock_id`` is the leading column of the constraint — this is not a duplicate."""
        batch = dedupe_candle_rows([row(), row(stock_id=OTHER_STOCK)])

        assert len(batch.rows) == 2
        assert batch.duplicates == ()

    def test_two_adjacent_months_overlapping_collapse_to_one_row(self) -> None:
        """The realistic source of an internal duplicate, and the one the fan-out creates."""
        boundary = dt.date(2026, 3, 1)
        january = row(day=boundary, at=dt.time(9, 35), close="1.0000")
        february = row(day=boundary, at=dt.time(9, 35), close="1.0100")

        batch = dedupe_candle_rows([january, february])

        assert len(batch.rows) == 1
        assert batch.rows[0]["close_price"] == Decimal("1.0100")

    @pytest.mark.parametrize("column", ["stock_id", "date", "time"])
    def test_a_row_missing_part_of_its_key_is_refused_and_named(self, column: str) -> None:
        """The alternative is a ``NOT NULL`` violation naming a column but not a row."""
        broken = row()
        broken[column] = None

        with pytest.raises(ValueError, match=column):
            dedupe_candle_rows([broken])

    def test_the_rows_are_copies_so_the_batch_cannot_be_mutated_from_outside(self) -> None:
        original = row()

        batch = dedupe_candle_rows([original])
        original["close_price"] = Decimal("0.0000")

        assert batch.rows[0]["close_price"] == Decimal("100.5000")

    def test_candle_key_is_the_tuple_the_statement_conflicts_on(self) -> None:
        assert candle_key(row()) == (STOCK, dt.date(2026, 3, 2), dt.time(9, 35))


# ---------------------------------------------------------------------------------------
# pacing the fan-out
# ---------------------------------------------------------------------------------------


class TestDispatchDelays:
    def test_the_spacing_stays_under_the_free_tiers_ceiling(self) -> None:
        """Four calls a minute, one clear of five, so delivery jitter cannot push it over."""
        assert 60 / CALL_SPACING_SECONDS < FREE_TIER_CALLS_PER_MINUTE

    def test_nothing_to_dispatch_is_no_delays(self) -> None:
        assert dispatch_delays(0) == ()

    def test_the_first_call_goes_immediately(self) -> None:
        assert dispatch_delays(1) == (0,)

    def test_delays_are_evenly_spaced(self) -> None:
        assert dispatch_delays(4, spacing=15) == (0, 15, 30, 45)

    def test_a_full_run_fits_inside_the_beat_interval(self) -> None:
        """Asserted again in ``test_jobs_celery_app.py`` against the schedule itself."""
        assert dispatch_delays(MAX_CALLS_PER_RUN)[-1] == (MAX_CALLS_PER_RUN - 1) * (
            CALL_SPACING_SECONDS
        )

    def test_a_negative_count_is_refused(self) -> None:
        with pytest.raises(ValueError, match="negative"):
            dispatch_delays(-1)

    @pytest.mark.parametrize("spacing", [0, -5])
    def test_a_non_positive_spacing_is_refused(self, spacing: int) -> None:
        """Zero is not "as fast as possible", it is "no rate limit", and it should be typed."""
        with pytest.raises(ValueError, match="positive"):
            dispatch_delays(3, spacing=spacing)


class TestFanOutOrder:
    PLANS: ClassVar[list[tuple[str, tuple[Month, ...]]]] = [
        ("AAPL", (Month(2026, 1), Month(2026, 2), Month(2026, 3))),
        ("MSFT", (Month(2026, 3),)),
        ("NVDA", (Month(2026, 2), Month(2026, 3))),
    ]

    def test_nothing_planned_is_nothing_to_dispatch(self) -> None:
        assert fan_out_order([]) == ()

    def test_every_stocks_current_month_comes_before_any_stocks_second(self) -> None:
        ordered = fan_out_order(self.PLANS)

        assert ordered[:3] == (
            ("AAPL", Month(2026, 3)),
            ("MSFT", Month(2026, 3)),
            ("NVDA", Month(2026, 3)),
        )

    def test_later_rounds_skip_the_stocks_that_have_run_out(self) -> None:
        ordered = fan_out_order(self.PLANS)

        assert ordered[3:] == (
            ("AAPL", Month(2026, 2)),
            ("NVDA", Month(2026, 2)),
            ("AAPL", Month(2026, 1)),
        )

    def test_a_budget_smaller_than_the_roster_still_touches_the_front_of_it(self) -> None:
        """The failure the naive flatten would produce: some stocks never ingested at all."""
        ordered = fan_out_order(self.PLANS, limit=2)

        assert ordered == (("AAPL", Month(2026, 3)), ("MSFT", Month(2026, 3)))

    def test_the_truncation_falls_on_the_oldest_month_of_the_last_stock(self) -> None:
        assert len(fan_out_order(self.PLANS, limit=5)) == 5
        assert fan_out_order(self.PLANS, limit=5)[-1] == ("NVDA", Month(2026, 2))

    def test_a_single_stock_is_dispatched_newest_month_first(self) -> None:
        ordered = fan_out_order([("AAPL", (Month(2026, 1), Month(2026, 2)))])

        assert ordered == (("AAPL", Month(2026, 2)), ("AAPL", Month(2026, 1)))

    def test_a_stock_with_no_months_contributes_nothing(self) -> None:
        assert fan_out_order([("AAPL", ())]) == ()

    def test_a_budget_below_one_call_is_refused(self) -> None:
        with pytest.raises(ValueError, match="limit"):
            fan_out_order(self.PLANS, limit=0)

    def test_the_default_budget_is_the_modules_own(self) -> None:
        many = [(f"T{index}", (Month(2026, 3),)) for index in range(MAX_CALLS_PER_RUN + 5)]

        assert len(fan_out_order(many)) == MAX_CALLS_PER_RUN


# ---------------------------------------------------------------------------------------
# the pieces working together
# ---------------------------------------------------------------------------------------


class TestTheRulesCompose:
    def test_a_realistic_response_narrows_step_by_step(self) -> None:
        """What the service does, without the service: filter, filter, quantise, dedupe."""
        day = dt.date(2026, 3, 2)
        bars = [
            Bar(date=day, time=dt.time(19, 55)),  # post-market: dropped by the window
            Bar(date=day, time=dt.time(15, 0), close=Decimal("100.12345")),
            Bar(date=day, time=dt.time(11, 0)),  # at the watermark: dropped as not new
            Bar(date=day, time=dt.time(7, 0)),  # pre-08:00: dropped by the window
        ]
        watermark = Watermark(date=day, time=dt.time(11, 0))

        traded = select_session_candles(bars, timezone="US/Eastern")
        fresh = select_new(traded, watermark=watermark)
        batch = dedupe_candle_rows(
            {
                "stock_id": STOCK,
                "date": bar.date,
                "time": bar.time,
                "close_price": quantise_price(bar.close, field="close"),
            }
            for bar in fresh
        )

        assert len(traded) == 2
        assert len(fresh) == 1
        assert batch.rows[0]["close_price"] == Decimal("100.1235")
        assert batch.duplicates == ()

    def test_replacing_a_bars_time_is_all_it_takes_to_move_it_out_of_the_window(self) -> None:
        """A sanity check on the stub itself, so the cases above cannot pass vacuously."""
        inside = Bar(time=dt.time(9, 35))

        assert in_session(inside, zone=SESSION_ZONE) is True
        assert in_session(replace(inside, time=dt.time(3, 0)), zone=SESSION_ZONE) is False
