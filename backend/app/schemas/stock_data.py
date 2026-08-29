"""Contracts for the ``stock_data`` candle series.

Two output shapes, because there are two genuinely different consumers:

:class:`StockDataOut`
    The row as stored — ``date`` and ``time`` as the separate columns they are. This is
    what an administrative or debugging endpoint returns, and it is the only shape that
    can round-trip back into the table.
:class:`StockDataPoint`
    The row as a chart wants it — one ``datetime``, no surrogate id. ANV-14 builds these;
    the recombination lives here, in the schema layer, so the *query* stays a plain
    ``SELECT`` in a repo and nothing has to invent a synthetic column in SQL.

**The combined ``datetime`` is deliberately naive.** ``stock_data.time`` is the exchange's
local trading clock and carries no zone (``app/models/stock.py`` says so); 09:30 at the New
York open is not 09:30 UTC, so stamping ``+00:00`` on it would be a lie that silently moves
every candle. It is the one datetime in the API without an offset, and it says so in its
own type. Attaching a real zone needs an exchange-to-timezone map that does not exist yet.

There is **no ``StockDataUpdate``**: a candle is an immutable observation. ANV-22 re-ingests
by upserting on ``(stock_id, date, time)``, which replaces a row wholesale; nothing
anywhere edits one field of a printed price.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, Field, NaiveDatetime

from app.models.stock import PRICE_PRECISION, PRICE_SCALE

#: A traded price. ``Decimal``, never ``float`` — the column is ``NUMERIC(12, 4)`` and a
#: round trip through binary floating point loses cents on large positions. The precision
#: bounds are the column's own, so a client sending more than four decimal places is told
#: so at the edge instead of having the value quietly rounded by Postgres.
Price = Annotated[
    Decimal,
    Field(ge=0, max_digits=PRICE_PRECISION, decimal_places=PRICE_SCALE, examples=["1234.5678"]),
]

#: Share count for the interval. Never negative.
Volume = Annotated[int, Field(ge=0, examples=[1_048_576])]


class _Candle(BaseModel):
    """The five numbers every candle shape carries, declared once.

    Whether ``high`` really is the highest of the four is a *rule*, not a shape, so it is
    checked in ``app/domain/`` where ANV-22 can test it against real vendor payloads —
    ``CLAUDE.md`` §3.
    """

    open_price: Price
    high_price: Price
    low_price: Price
    close_price: Price
    volume: Volume


class StockDataCreate(_Candle):
    """One candle on its way in.

    **No ``stock_id``.** The parent comes from the caller — the path parameter of
    ``POST /v1/stocks/{stock_id}/data``, or the stock the ingest job is currently syncing —
    exactly as a child factory takes its parent from the test that builds it. A
    ``stock_id`` in the body as well would be a second source of truth for which security
    this price belongs to, and the two can disagree.
    """

    date: dt.date = Field(description="Trading date, the exchange's local date.")
    time: dt.time = Field(description="Trading time, the exchange's local clock. No zone.")


class StockDataOut(_Candle):
    """One stored candle, with its columns as they are on disk."""

    model_config = ConfigDict(from_attributes=True)

    #: ``BIGSERIAL``, and the one table in Anvex whose key is plain ``id`` rather than
    #: ``<entity>_id``.
    id: int
    stock_id: uuid.UUID
    date: dt.date
    time: dt.time


class StockDataPoint(_Candle):
    """One candle as a chart plots it: a single timestamp and the five numbers.

    Built from a model row with :meth:`from_row`, which is the whole of ANV-14's
    "recombine date and time" step::

        points = [StockDataPoint.from_row(row) for row in await repo.range(...)]
    """

    model_config = ConfigDict(from_attributes=True)

    stock_id: uuid.UUID
    #: **Naive on purpose** — the exchange's local wall clock. See the module docstring.
    datetime: NaiveDatetime = Field(examples=["2026-01-05T09:30:00"])

    @classmethod
    def from_row(cls, row: Any) -> Self:
        """Build a point from a :class:`app.models.StockData` row.

        Takes ``Any`` rather than the model class because ``app/schemas/`` describes the
        wire, not the ORM; anything with the six attributes works, which is also what makes
        this testable without a database.
        """
        return cls(
            stock_id=row.stock_id,
            datetime=dt.datetime.combine(row.date, row.time),
            open_price=row.open_price,
            high_price=row.high_price,
            low_price=row.low_price,
            close_price=row.close_price,
            volume=row.volume,
        )


__all__ = ["Price", "StockDataCreate", "StockDataOut", "StockDataPoint", "Volume"]
