"""Unit tests for ``app/domain/stock_data.py`` — the paging and date-range rules.

The purest and therefore the most exhaustively tested tier (``CLAUDE.md`` §3/§6): no
fixtures, no I/O, no database, no clock. Every case below is arithmetic on plain values, so
the whole module runs with Docker stopped and in milliseconds.

What is being pinned:

* **Inclusive on both ends.** ``start == end`` is one trading day, not the empty set — the
  off-by-one this module exists so nobody writes twice.
* **Open bounds are independent.** Four shapes (unbounded, open start, open end, closed) out
  of two optional values, and each one is a legitimate question.
* **An inverted range is the one absurd range** and is a ``ValidationError`` — a 422 — rather
  than a silently empty 200, because the caller has a bug and an empty page would hide it.
* **The window is resolved here** so the service never carries a "maybe resolved" limit, with
  the limit delegated to ``resolve_page_limit`` rather than re-derived.
* **Purity**, checked by parsing the source rather than trusted to prose.
"""

from __future__ import annotations

import ast
import dataclasses
import datetime as dt
from pathlib import Path

import pytest

from app.domain import stock_data as domain_stock_data
from app.domain.errors import AnvexError, ValidationError
from app.domain.stock_data import (
    MIN_OFFSET,
    RANGE_FIELD,
    UNBOUNDED_LABEL,
    CandleQuery,
    DateRange,
    PageWindow,
    resolve_candle_query,
    resolve_date_range,
    resolve_window,
)
from app.middleware.errors import status_for
from app.schemas.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT

MONDAY = dt.date(2026, 1, 5)
FRIDAY = dt.date(2026, 1, 9)
NEXT_MONDAY = dt.date(2026, 1, 12)


def source_tree() -> ast.Module:
    return ast.parse(Path(domain_stock_data.__file__).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------------------
# purity
# ---------------------------------------------------------------------------------------


class TestPurity:
    """``app/domain/`` is pure by rule, and a convention that lives only in prose gets
    broken — so this is parsed out of the source, exactly as ``test_domain_auth.py`` does."""

    def test_it_imports_no_framework_no_orm_and_no_settings(self) -> None:
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
        assert "app.settings" not in modules
        # Downward-only: the error hierarchy and the paging rule, nothing that does I/O.
        # ``app.domain.pagination`` replaced ``app.schemas.pagination`` here in ANV-16, when
        # ``resolve_window`` gained a third caller and moved to its own aggregate-neutral
        # module. The bounds are still the schema's, one hop further down; the re-export is
        # covered by ``tests/unit/test_domain_pagination.py``.
        assert {module for module in modules if module.startswith("app")} == {
            "app.domain.errors",
            "app.domain.pagination",
        }

    def test_it_never_reads_a_clock(self) -> None:
        """No rule here needs the time, and the module says so; this makes that true."""
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

        assert offenders == [], (
            f"domain/stock_data.py must take a clock as a parameter: {offenders}"
        )

    def test_it_writes_no_sql(self) -> None:
        source = Path(domain_stock_data.__file__).read_text(encoding="utf-8")
        assert "select(" not in source
        assert "get_settings" not in source


# ---------------------------------------------------------------------------------------
# DateRange — the value
# ---------------------------------------------------------------------------------------


class TestDateRangeShape:
    def test_a_closed_range_is_neither_open_nor_unbounded(self) -> None:
        window = DateRange(start=MONDAY, end=FRIDAY)

        assert not window.is_unbounded
        assert not window.is_open_started
        assert not window.is_open_ended
        assert not window.is_single_day

    def test_no_bounds_is_unbounded(self) -> None:
        window = DateRange()

        assert window.is_unbounded
        assert window.is_open_started
        assert window.is_open_ended
        assert window.days is None

    def test_an_open_start_means_everything_up_to_the_end(self) -> None:
        window = DateRange(end=FRIDAY)

        assert window.is_open_started
        assert not window.is_open_ended
        assert not window.is_unbounded
        assert window.days is None

    def test_an_open_end_means_everything_from_the_start(self) -> None:
        window = DateRange(start=MONDAY)

        assert window.is_open_ended
        assert not window.is_open_started
        assert window.days is None

    def test_one_day_is_a_single_day_not_an_empty_set(self) -> None:
        """The inclusive bounds, stated as a property. This is the off-by-one."""
        window = DateRange(start=MONDAY, end=MONDAY)

        assert window.is_single_day
        assert window.days == 1

    def test_an_open_range_is_never_a_single_day(self) -> None:
        assert not DateRange(start=MONDAY).is_single_day
        assert not DateRange(end=MONDAY).is_single_day
        assert not DateRange().is_single_day

    @pytest.mark.parametrize(
        ("start", "end", "expected"),
        [
            (MONDAY, MONDAY, 1),
            (MONDAY, dt.date(2026, 1, 6), 2),
            (MONDAY, FRIDAY, 5),
            (MONDAY, NEXT_MONDAY, 8),
            (dt.date(2026, 2, 28), dt.date(2026, 3, 1), 2),
        ],
    )
    def test_days_counts_both_ends(self, start: dt.date, end: dt.date, expected: int) -> None:
        assert DateRange(start=start, end=end).days == expected

    def test_it_is_a_value_two_equal_ranges_are_the_same_question(self) -> None:
        assert DateRange(start=MONDAY, end=FRIDAY) == DateRange(start=MONDAY, end=FRIDAY)

    def test_it_is_frozen_so_a_validated_range_cannot_be_widened(self) -> None:
        window = DateRange(start=MONDAY, end=FRIDAY)

        with pytest.raises(dataclasses.FrozenInstanceError):
            window.end = NEXT_MONDAY  # type: ignore[misc]


class TestDateRangeContains:
    @pytest.mark.parametrize(
        ("day", "expected"),
        [
            (dt.date(2026, 1, 4), False),
            (MONDAY, True),
            (dt.date(2026, 1, 7), True),
            (FRIDAY, True),
            (dt.date(2026, 1, 10), False),
        ],
    )
    def test_a_closed_range_includes_both_endpoints(self, day: dt.date, expected: bool) -> None:
        assert DateRange(start=MONDAY, end=FRIDAY).contains(day) is expected

    def test_an_unbounded_range_contains_everything(self) -> None:
        window = DateRange()

        assert window.contains(dt.date(1970, 1, 1))
        assert window.contains(dt.date(2999, 12, 31))

    def test_an_open_start_excludes_nothing_early(self) -> None:
        window = DateRange(end=FRIDAY)

        assert window.contains(dt.date(1999, 1, 1))
        assert not window.contains(NEXT_MONDAY)

    def test_an_open_end_excludes_nothing_late(self) -> None:
        window = DateRange(start=MONDAY)

        assert not window.contains(dt.date(2026, 1, 4))
        assert window.contains(dt.date(2999, 12, 31))

    def test_a_single_day_range_contains_exactly_that_day(self) -> None:
        window = DateRange(start=MONDAY, end=MONDAY)

        assert window.contains(MONDAY)
        assert not window.contains(dt.date(2026, 1, 6))

    def test_a_datetime_is_narrowed_to_its_date(self) -> None:
        """``datetime`` subclasses ``date``, so it passes every type check and then compares
        against the wrong thing. 23:59 on the last day is still in an inclusive range."""
        window = DateRange(start=MONDAY, end=FRIDAY)

        assert window.contains(dt.datetime(2026, 1, 9, 23, 59, 59))
        assert not window.contains(dt.datetime(2026, 1, 10, 0, 0, 1))


class TestDateRangeLabel:
    @pytest.mark.parametrize(
        ("window", "expected"),
        [
            (DateRange(), UNBOUNDED_LABEL),
            (DateRange(start=MONDAY, end=FRIDAY), "2026-01-05..2026-01-09"),
            (DateRange(start=MONDAY), "2026-01-05.."),
            (DateRange(end=FRIDAY), "..2026-01-09"),
            (DateRange(start=MONDAY, end=MONDAY), "2026-01-05..2026-01-05"),
        ],
    )
    def test_it_renders_every_shape(self, window: DateRange, expected: str) -> None:
        assert window.label() == expected


# ---------------------------------------------------------------------------------------
# resolve_date_range — the rule
# ---------------------------------------------------------------------------------------


class TestResolveDateRange:
    def test_a_coherent_range_passes_through(self) -> None:
        assert resolve_date_range(start=MONDAY, end=FRIDAY) == DateRange(start=MONDAY, end=FRIDAY)

    def test_no_bounds_at_all_is_valid(self) -> None:
        assert resolve_date_range() == DateRange()

    def test_an_open_start_is_valid(self) -> None:
        assert resolve_date_range(end=FRIDAY) == DateRange(end=FRIDAY)

    def test_an_open_end_is_valid(self) -> None:
        assert resolve_date_range(start=MONDAY) == DateRange(start=MONDAY)

    def test_a_single_day_is_valid(self) -> None:
        """``start == end`` is the boundary of the rule: coherent, and one day wide."""
        window = resolve_date_range(start=MONDAY, end=MONDAY)

        assert window.is_single_day
        assert window.days == 1

    def test_one_day_inverted_is_already_absurd(self) -> None:
        """The other side of that boundary, one day away."""
        with pytest.raises(ValidationError):
            resolve_date_range(start=dt.date(2026, 1, 6), end=MONDAY)

    def test_an_inverted_range_is_refused(self) -> None:
        with pytest.raises(ValidationError) as raised:
            resolve_date_range(start=FRIDAY, end=MONDAY)

        error = raised.value
        assert error.code == "validation_error"
        assert error.field == RANGE_FIELD
        assert error.details == {
            "field": RANGE_FIELD,
            "start": "2026-01-09",
            "end": "2026-01-05",
        }

    def test_the_message_names_both_dates(self) -> None:
        with pytest.raises(ValidationError) as raised:
            resolve_date_range(start=FRIDAY, end=MONDAY)

        assert "2026-01-09" in raised.value.message
        assert "2026-01-05" in raised.value.message

    def test_it_is_a_422_not_a_500(self) -> None:
        """The whole reason it is a domain error: the caller's request is wrong, not ours."""
        with pytest.raises(ValidationError) as raised:
            resolve_date_range(start=FRIDAY, end=MONDAY)

        assert issubclass(type(raised.value), AnvexError)
        assert status_for(raised.value) == 422

    def test_an_inversion_is_only_possible_with_both_bounds(self) -> None:
        """One open bound cannot invert anything, however far apart the years are."""
        assert resolve_date_range(start=dt.date(2999, 1, 1)).start == dt.date(2999, 1, 1)
        assert resolve_date_range(end=dt.date(1900, 1, 1)).end == dt.date(1900, 1, 1)

    @pytest.mark.parametrize(
        ("start", "end"),
        [
            (dt.date(1900, 1, 1), dt.date(2999, 12, 31)),
            (dt.date(1, 1, 1), dt.date(9999, 12, 31)),
        ],
    )
    def test_an_enormous_range_is_coherent_and_deliberately_uncapped(
        self, start: dt.date, end: dt.date
    ) -> None:
        """There is no maximum span: paging bounds the response, so a wide range costs a
        wider ``COUNT`` and nothing else. Refusing one would refuse "everything since 2019"."""
        window = resolve_date_range(start=start, end=end)

        assert window.start == start
        assert window.end == end

    def test_a_datetime_bound_is_narrowed_to_its_date(self) -> None:
        """A job holding ``datetime.now()`` passes every type check; the column is a DATE."""
        window = resolve_date_range(
            start=dt.datetime(2026, 1, 5, 16, 0), end=dt.datetime(2026, 1, 9, 9, 30)
        )

        assert window == DateRange(start=MONDAY, end=FRIDAY)
        assert type(window.start) is dt.date
        assert type(window.end) is dt.date

    def test_datetimes_that_invert_only_by_their_time_do_not_invert(self) -> None:
        """16:00 on Monday to 09:30 on Monday is the same *date* twice — a single day, not
        an inversion. Narrowing before comparing is what makes that true."""
        window = resolve_date_range(
            start=dt.datetime(2026, 1, 5, 16, 0), end=dt.datetime(2026, 1, 5, 9, 30)
        )

        assert window.is_single_day


# ---------------------------------------------------------------------------------------
# resolve_window — the paging rule
# ---------------------------------------------------------------------------------------


class TestResolveWindow:
    def test_nothing_asked_for_is_the_default_page_from_the_start(self) -> None:
        assert resolve_window() == PageWindow(limit=DEFAULT_PAGE_LIMIT, offset=MIN_OFFSET)

    def test_a_sensible_request_passes_through(self) -> None:
        assert resolve_window(limit=25, offset=100) == PageWindow(limit=25, offset=100)

    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            (None, DEFAULT_PAGE_LIMIT),
            (1, 1),
            (MAX_PAGE_LIMIT, MAX_PAGE_LIMIT),
            (MAX_PAGE_LIMIT + 1, MAX_PAGE_LIMIT),
            (10_000, MAX_PAGE_LIMIT),
            (0, 1),
            (-5, 1),
        ],
    )
    def test_the_limit_is_delegated_to_the_pagination_bounds(
        self, given: int | None, expected: int
    ) -> None:
        """Not re-derived here: ``resolve_page_limit`` lives beside the ``Page`` that would
        otherwise reject an unclamped value with a 500."""
        assert resolve_window(limit=given).limit == expected

    @pytest.mark.parametrize(
        ("given", "expected"),
        [(None, 0), (0, 0), (1, 1), (5_000, 5_000), (-1, 0), (-999, 0)],
    )
    def test_a_negative_offset_is_clamped_to_the_first_page(
        self, given: int | None, expected: int
    ) -> None:
        """Clamped, not refused: the HTTP edge already rejects it with ``Query(ge=0)``, so
        this only protects a job whose page arithmetic went negative."""
        assert resolve_window(offset=given).offset == expected

    def test_the_result_is_always_valid_for_the_envelope(self) -> None:
        """Whatever it is handed, both numbers satisfy ``Page``'s own bounds."""
        for limit in (None, -1, 0, 1, 50, MAX_PAGE_LIMIT, 10_000):
            window = resolve_window(limit=limit, offset=-7)

            assert 1 <= window.limit <= MAX_PAGE_LIMIT
            assert window.offset >= 0

    def test_it_is_frozen(self) -> None:
        window = resolve_window()

        with pytest.raises(dataclasses.FrozenInstanceError):
            window.limit = 1  # type: ignore[misc]


# ---------------------------------------------------------------------------------------
# resolve_candle_query — both rules, in order
# ---------------------------------------------------------------------------------------


class TestResolveCandleQuery:
    def test_it_carries_both_halves(self) -> None:
        query = resolve_candle_query(start=MONDAY, end=FRIDAY, limit=10, offset=20)

        assert query == CandleQuery(
            dates=DateRange(start=MONDAY, end=FRIDAY),
            window=PageWindow(limit=10, offset=20),
        )

    def test_an_empty_request_is_the_whole_series_first_page(self) -> None:
        query = resolve_candle_query()

        assert query.dates.is_unbounded
        assert query.window == PageWindow(limit=DEFAULT_PAGE_LIMIT, offset=MIN_OFFSET)

    def test_the_range_is_validated_before_the_window_is_resolved(self) -> None:
        """An inverted range is refused even when the paging is nonsense too — the caller
        hears about the date bug rather than having it silently clamped away."""
        with pytest.raises(ValidationError) as raised:
            resolve_candle_query(start=FRIDAY, end=MONDAY, limit=10_000, offset=-1)

        assert raised.value.field == RANGE_FIELD
