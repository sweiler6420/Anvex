"""Reading a stock's candle series: one chronological window, by id or by ticker.

Written to the shape ``app/services/stock.py`` established (``CLAUDE.md`` §3) — collaborators
in the constructor defaulting to the repo singletons, one ``async`` method per use case,
keyword-only arguments, a schema out, ``app.domain.errors`` on the way out.

**Read-only, like ANV-13.** Nothing here writes a candle: an observation arrives from a
vendor, and ANV-22's ingest will persist it through
:meth:`~app.repos.stock_data.StockDataRepo.bulk_upsert` rather than over HTTP. A unit test
pins the public surface to the two read methods, so the scope boundary is enforced rather
than remembered.

**The wire shape is the whole point of this ticket.** The endpoint it replaces built a
combined timestamp in SQL (``(StockData.date + StockData.time).label("datetime")``) and the
charting widgets consume that shape. It is preserved exactly — but the recombination happens
in :meth:`~app.schemas.stock_data.StockDataPoint.from_row` (ANV-8), which this service
*calls*. The query stays a plain ``SELECT`` in a repo, no synthetic column is invented in
SQL, and there is one implementation of "date + time" in the codebase rather than two that
can drift.

That ``datetime`` is **naive on purpose**: ``stock_data.time`` is the exchange's local
trading clock, so stamping ``+00:00`` on a 09:30 New York open would move every candle by
hours. It is the only datetime in the API without an offset, and
``tests/api/test_stock_data.py`` asserts ``tzinfo is None`` so the next reader does not
"fix" it. Prices are ``Decimal`` and serialise as quoted JSON strings (``"1234.5678"``),
which is what keeps the fourth decimal place a JSON number would lose.

**Three rules divide cleanly between the layers, and this module is the seam.**

*What is a sensible question* is :mod:`app.domain.stock_data`: the inclusive date range, the
inverted range that is a 422, and the resolved ``limit``/``offset``. Pure, so it is tested
exhaustively without a database and a Celery task gets the same answers.

*What the rows are* is :class:`~app.repos.stock_data.StockDataRepo`, which returns
``(rows, total)`` with ``total`` counted **before** the window — so an offset past the end is
an empty page with a truthful total, never an implied end of the series.

*What an empty result means* is here, and it is the judgement a repo deliberately refuses to
make. **An unknown stock is a 404; a known stock with no candles in range is an empty page.**
The repo cannot tell those apart — ``list_for_stock`` on a nonexistent id returns ``([], 0)``
just as a quiet Sunday does — so the stock is resolved first, through
:class:`~app.repos.stock.StockRepo`, and its absence is a
:class:`~app.domain.errors.NotFoundError`. A sub-collection of a parent that does not exist
is not an empty collection, and answering ``GET /v1/stocks/{unknown}/data`` with ``200 []``
would tell a chart the security is real and simply untraded. There is no ownership question
here to leak: a security belongs to nobody, so §4's "refuse with a 404, not a 403" rule has
nothing to hide and this 404 is the plain kind.

**The ticker route deliberately does not use** :meth:`~app.repos.stock_data.StockDataRepo.
list_for_ticker`. That method exists to answer a symbol in one join, which is right for a
caller happy to treat "no such ticker" and "no candles" alike — a job, a backfill sweep. This
service is not that caller: it has to resolve the stock anyway to produce the 404, and once
it holds the row it also holds ``stock_id``, the leading column of the ``(stock_id, date,
time)`` unique index that serves the range read. Joining ``stocks`` a second time to reach
rows we can already address directly would be work for nothing.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Final

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import NotFoundError
from app.domain.stock import normalise_ticker
from app.domain.stock_data import CandleQuery, resolve_candle_query
from app.models import Stock, StockData
from app.repos.stock import StockRepo, stock_repo
from app.repos.stock_data import StockDataRepo, stock_data_repo
from app.schemas.pagination import Page
from app.schemas.stock_data import StockDataPoint
from app.settings import Settings

logger = structlog.get_logger("anvex.stock_data")

#: The resource a 404 from this module names. It is ``"stock"``, not ``"stock_data"``,
#: because the thing that was not found *is* the security — an existing stock with no
#: candles is a 200. Deliberately the same string as ``app.services.stock.RESOURCE``, and a
#: unit test asserts they stay equal, so a client branching on ``details["resource"]`` sees
#: one spelling no matter which endpoint refused it.
RESOURCE: Final[str] = "stock"


class StockDataService:
    """Read access to the intraday candle series."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        stocks: StockRepo = stock_repo,
        candles: StockDataRepo = stock_data_repo,
    ) -> None:
        self.session = session
        self.settings = settings
        #: Resolving the parent is a use case of this service, not a reason to call the
        #: stock *service* — one service reaching into another's methods would make the two
        #: impossible to test apart. Both repos are keyword-defaulted to their singletons,
        #: which is the seam a unit test replaces with an in-memory fake.
        self.stocks = stocks
        self.candles = candles

    # -----------------------------------------------------------------------------------
    # Use cases
    # -----------------------------------------------------------------------------------

    async def list_for_stock(
        self,
        *,
        stock_id: uuid.UUID,
        start: dt.date | None = None,
        end: dt.date | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> Page[StockDataPoint]:
        """One chronological window of this stock's candles, oldest first.

        ``start`` and ``end`` are **inclusive** and independently optional, so the same
        method answers "everything", "everything since", "everything until" and "this
        window".

        :raises ValidationError: ``start`` falls after ``end`` — checked before anything is
            queried, so a caller with inverted dates is told so whether or not the stock
            exists.
        :raises NotFoundError: no stock has that id. A stock that exists but has no candles
            in range is an empty page, not an error.
        """
        query = resolve_candle_query(start=start, end=end, limit=limit, offset=offset)
        stock = await self.stocks.get_by_id(self.session, stock_id)
        if stock is None:
            raise NotFoundError(RESOURCE, stock_id)
        return await self._window(stock, query)

    async def list_for_ticker(
        self,
        *,
        ticker: str,
        start: dt.date | None = None,
        end: dt.date | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> Page[StockDataPoint]:
        """:meth:`list_for_stock`, keyed by the symbol a human actually types.

        The ticker is normalised here — ``" aapl "`` and ``AAPL`` name the same series — for
        the reason ``app/services/stock.py`` sets out: the repo lookup is exact and
        case-sensitive so it can use the unique index, and the API is not the only caller, so
        the rule lives in the layer every caller goes through. The 404 reports the
        **canonical** spelling, so a caller who searched for ``"aapl"`` can see the lookup
        was not simply a casing mistake.

        :raises ValidationError: ``start`` falls after ``end``.
        :raises NotFoundError: no stock carries that ticker.
        """
        query = resolve_candle_query(start=start, end=end, limit=limit, offset=offset)
        symbol = normalise_ticker(ticker)
        stock = await self.stocks.get_by_ticker(self.session, symbol)
        if stock is None:
            raise NotFoundError(RESOURCE, symbol)
        return await self._window(stock, query)

    # -----------------------------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------------------------

    async def _window(self, stock: Stock, query: CandleQuery) -> Page[StockDataPoint]:
        """Run the resolved query against a stock that is already known to exist.

        The one place the repo is asked for candles, so both use cases page, order and
        project identically — the difference between them is only how the parent was found.
        """
        rows, total = await self.candles.list_for_stock(
            self.session,
            stock.stock_id,
            start=query.dates.start,
            end=query.dates.end,
            limit=query.window.limit,
            offset=query.window.offset,
        )
        logger.debug(
            "stock_data.window",
            stock_id=str(stock.stock_id),
            ticker=stock.ticker_symbol,
            dates=query.dates.label(),
            returned=len(rows),
            total=total,
        )
        return self._page(rows, total, query=query)

    @staticmethod
    def _page(rows: list[StockData], total: int, *, query: CandleQuery) -> Page[StockDataPoint]:
        """Wrap ``(rows, total)`` from the repo in the response envelope.

        The projection happens on the way in, so no ORM instance ever reaches the API
        (``CLAUDE.md`` §3), and it goes through
        :meth:`~app.schemas.stock_data.StockDataPoint.from_row` rather than rebuilding the
        combined timestamp here — that recombination has exactly one implementation, in the
        schema layer that documents why it is naive. ``has_more`` is left to the envelope's
        computed field rather than derived a second time.
        """
        return Page[StockDataPoint](
            items=[StockDataPoint.from_row(row) for row in rows],
            total=total,
            limit=query.window.limit,
            offset=query.window.offset,
        )


__all__ = ["RESOURCE", "StockDataService"]
