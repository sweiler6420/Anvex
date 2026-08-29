"""Pure rules for asking the candle series a question: which dates, and which window.

``app/repos/stock_data.py`` will happily run any range it is handed and
``app/schemas/pagination.py`` will happily build any envelope. What neither can do is say
whether the *question* made sense. That judgement is arithmetic on plain data — no database,
no request, no clock — so ``CLAUDE.md`` §3 puts it here, where it is cheap to test
exhaustively and where a Celery task reaches the same answer an HTTP caller does.

**The two rules, and what they deliberately do not do.**

*A range is coherent when it can contain a day.* Both bounds are optional and independently
so, which is what makes "everything", "everything since", "everything until" and "this
window" one concept rather than four. The bounds are **inclusive** on both ends — the repo's
``date >= start`` / ``date <= end`` say so — so ``start == end`` is a single trading day and
not the empty set, which is the off-by-one this module exists to make impossible to write
twice.

*An inverted range is the one absurd range.* ``start > end`` describes no day at all, and no
amount of paging will change that, so it is a :class:`~app.domain.errors.ValidationError`
(422) rather than a silently empty page: the caller has a bug and an empty 200 would hide it.
Every *other* range is merely empty — a Sunday, a delisted stock, a window before the ingest
started — and emptiness is a legitimate answer, not a failure.

**There is deliberately no maximum span.** A cap would have to be arbitrary, and it would
refuse "give me everything since 2019", which is a reasonable thing to ask of a chart. The
response is already bounded by :data:`~app.schemas.pagination.MAX_PAGE_LIMIT`, so a huge
range costs a wider ``COUNT`` and nothing else; the row count a client can pull is governed
by paging, which is the layer that should govern it.

**There is deliberately no clock.** Nothing here needs one. The tempting rule — "a range
entirely in the future is empty, so skip the query" — would need to compare the caller's
dates against the *exchange's* local date, and Anvex has no exchange-to-timezone map yet
(``app/schemas/stock_data.py`` says the same thing about the candle timestamp). Guessing with
the server's UTC date would be wrong for any venue ahead of it. If a rule here ever does need
the time, it takes ``today`` as a keyword argument, exactly as ``app/domain/auth.py`` takes
``now``.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Final

from app.domain.errors import ValidationError
from app.schemas.pagination import resolve_page_limit

#: The smallest offset that means anything. A negative one is not a smaller page, it is a
#: nonsense request; see :func:`resolve_window` for why it is clamped rather than refused.
MIN_OFFSET: Final[int] = 0

#: ``details["field"]`` on the one error this module raises. The start bound is named
#: because it is the one the caller most likely mistyped — both dates are in ``details``
#: either way, so a form can highlight whichever it likes.
RANGE_FIELD: Final[str] = "start"

#: How an open bound renders in :meth:`DateRange.label`.
UNBOUNDED_LABEL: Final[str] = "all"


def _as_date(value: dt.date) -> dt.date:
    """Narrow a value that may be a ``datetime`` to the ``date`` the column stores.

    ``datetime`` is a *subclass* of ``date``, so a caller holding one passes every type
    check and then compares badly against a ``DATE`` column. The HTTP edge cannot hand us
    one — the route declares ``dt.date`` and FastAPI parses it — but a job or a script
    holding ``datetime.now()`` absolutely can, and this is the layer they share.
    """
    return value.date() if isinstance(value, dt.datetime) else value


@dataclass(frozen=True, slots=True)
class DateRange:
    """An inclusive ``[start, end]`` window over trading dates. Either bound may be open.

    Frozen because a range is a value: two ranges with the same bounds are the same
    question, and nothing should be able to widen one after it has been validated.

    Construct one through :func:`resolve_date_range` rather than directly — the constructor
    performs no checking, so an inverted range is representable here on purpose. That keeps
    the type a plain value and puts the rule in exactly one function.
    """

    start: dt.date | None = None
    end: dt.date | None = None

    @property
    def is_unbounded(self) -> bool:
        """No bounds at all: every candle the stock has."""
        return self.start is None and self.end is None

    @property
    def is_open_started(self) -> bool:
        """ "Everything up to ``end``" — however far back the series goes."""
        return self.start is None

    @property
    def is_open_ended(self) -> bool:
        """ "Everything from ``start``" — including candles not ingested yet."""
        return self.end is None

    @property
    def is_single_day(self) -> bool:
        """One trading date. Inclusive on both ends, so ``start == end`` is *not* empty."""
        return self.start is not None and self.start == self.end

    @property
    def days(self) -> int | None:
        """Calendar days covered, inclusive, or ``None`` when a bound is open.

        Calendar days, not trading days: which of them the market was open on is a fact
        about a venue's holiday calendar, which this module does not have and must not
        invent.
        """
        if self.start is None or self.end is None:
            return None
        return (self.end - self.start).days + 1

    def contains(self, day: dt.date) -> bool:
        """Whether ``day`` falls in the window. An open bound never excludes anything."""
        day = _as_date(day)
        if self.start is not None and day < self.start:
            return False
        return not (self.end is not None and day > self.end)

    def label(self) -> str:
        """A short, log-safe rendering: ``"2026-01-05..2026-01-09"``, ``"..2026-01-09"``,
        ``"2026-01-05.."`` or ``"all"``."""
        if self.is_unbounded:
            return UNBOUNDED_LABEL
        start = self.start.isoformat() if self.start is not None else ""
        end = self.end.isoformat() if self.end is not None else ""
        return f"{start}..{end}"


@dataclass(frozen=True, slots=True)
class PageWindow:
    """A resolved ``limit``/``offset`` pair: exactly what the repo is about to be asked for.

    Both numbers are already safe to hand to Postgres *and* to
    :class:`~app.schemas.pagination.Page`, which is the point — a service should never carry
    a "maybe resolved" limit around.
    """

    limit: int
    offset: int


@dataclass(frozen=True, slots=True)
class CandleQuery:
    """A whole validated request for candles: which dates, and which window of them."""

    dates: DateRange
    window: PageWindow


def resolve_date_range(*, start: dt.date | None = None, end: dt.date | None = None) -> DateRange:
    """Validate and build the inclusive range the caller described.

    :raises ValidationError: ``start`` falls after ``end``. That range describes no day, so
        answering it with an empty 200 would hide the caller's bug behind a plausible
        response. Every other range — open on either side, a single day, or one that simply
        happens to be empty — is coherent and returns normally.
    """
    start = _as_date(start) if start is not None else None
    end = _as_date(end) if end is not None else None

    if start is not None and end is not None and start > end:
        raise ValidationError(
            f"The date range starting {start.isoformat()} ends earlier, on {end.isoformat()}.",
            field=RANGE_FIELD,
            details={"start": start.isoformat(), "end": end.isoformat()},
        )
    return DateRange(start=start, end=end)


def resolve_window(*, limit: int | None = None, offset: int | None = None) -> PageWindow:
    """The window to actually query for a caller that asked for ``limit``/``offset``.

    ``limit`` is delegated to :func:`~app.schemas.pagination.resolve_page_limit` rather than
    re-derived, so the bounds live beside the :class:`~app.schemas.pagination.Page` that
    enforces them and there is one answer to "how big is a page".

    ``offset`` is **clamped** to :data:`MIN_OFFSET` rather than refused, for the same reason
    the limit is clamped rather than refused (``CLAUDE.md`` §4): the HTTP edge already
    rejects a negative offset with a 422 via ``Query(ge=0)``, so an HTTP client is never
    quietly moved somewhere it did not ask to be — while a job or a script that computed
    ``offset = page * size - size`` into a negative gets the first page instead of a
    ``SQL`` error nobody will read.
    """
    return PageWindow(
        limit=resolve_page_limit(limit),
        offset=MIN_OFFSET if offset is None else max(MIN_OFFSET, offset),
    )


def resolve_candle_query(
    *,
    start: dt.date | None = None,
    end: dt.date | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> CandleQuery:
    """Both rules at once — the single call a service makes before it touches a repo.

    Ordering is deliberate: the range is validated **first**, so a caller that inverted its
    dates is told so whether or not the rest of the request would have worked.
    """
    return CandleQuery(
        dates=resolve_date_range(start=start, end=end),
        window=resolve_window(limit=limit, offset=offset),
    )


__all__ = [
    "MIN_OFFSET",
    "RANGE_FIELD",
    "UNBOUNDED_LABEL",
    "CandleQuery",
    "DateRange",
    "PageWindow",
    "resolve_candle_query",
    "resolve_date_range",
    "resolve_window",
]
