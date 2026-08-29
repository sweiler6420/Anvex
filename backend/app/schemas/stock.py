"""Contracts for the ``stocks`` reference table.

Tickers and ISINs are trimmed and upper-cased on the way in. A security has one canonical
spelling — ``aapl`` and ``AAPL`` are the same instrument — and ``ticker_symbol`` is unique,
so without normalisation the table would happily hold both and every lookup would have to
guess. Doing it in the annotated type rather than in a service means *every* entry point
(API body, seed loader, ingest job) gets it, because they all build the same schema.

Length ceilings are imported from ``app/models/stock.py`` rather than restated, so a
``VARCHAR`` widening cannot leave a stale number behind in a validator.

``isin`` is the only nullable column on this table: AlphaVantage does not return one, so
requiring it would make it impossible for ANV-22's ingest to create a stock at all.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from app.models.stock import (
    COMPANY_MAX_LENGTH,
    ISIN_LENGTH,
    MARKET_MAX_LENGTH,
    TICKER_MAX_LENGTH,
)


def _canonical(value: Any) -> Any:
    """Trim and upper-case an identifier; leave anything that is not a string alone.

    Runs *before* the length constraint so ``" aapl "`` is accepted and stored as
    ``"AAPL"`` rather than rejected for the width of its own whitespace. Non-strings pass
    through untouched so pydantic reports the real type error instead of an
    ``AttributeError``.
    """
    return value.strip().upper() if isinstance(value, str) else value


#: Wide enough for suffixed and class-share tickers (``BRK.B``, ``BTC-USD``).
Ticker = Annotated[
    str,
    BeforeValidator(_canonical),
    Field(min_length=1, max_length=TICKER_MAX_LENGTH, examples=["AAPL"]),
]

Company = Annotated[
    str, Field(min_length=1, max_length=COMPANY_MAX_LENGTH, examples=["Apple Inc."])
]

Market = Annotated[str, Field(min_length=1, max_length=MARKET_MAX_LENGTH, examples=["NASDAQ"])]

#: An ISIN is exactly twelve characters by definition, so this is a fixed width rather than
#: a ceiling. The check digit is deliberately not validated: a vendor feed with one bad
#: digit should reach us and be visible, not vanish at the edge.
Isin = Annotated[
    str,
    BeforeValidator(_canonical),
    Field(min_length=ISIN_LENGTH, max_length=ISIN_LENGTH, examples=["US0378331005"]),
]


class StockCreate(BaseModel):
    """``POST /v1/stocks`` — add a security to the reference table."""

    ticker_symbol: Ticker
    company: Company
    market: Market
    isin: Isin | None = Field(default=None, description="Null where the vendor supplies none.")


class StockUpdate(BaseModel):
    """``PATCH /v1/stocks/{stock_id}`` — correct a security's details.

    ``isin`` is genuinely nullable, so ``null`` here means "clear it" while an absent key
    means "leave it alone". The service must therefore apply this with
    ``model_dump(exclude_unset=True)`` — reading the attribute alone cannot tell the two
    apart. The other three columns are ``NOT NULL``, so ``None`` for them only ever means
    "unchanged".
    """

    ticker_symbol: Ticker | None = None
    company: Company | None = None
    market: Market | None = None
    isin: Isin | None = None


class StockOut(BaseModel):
    """The public shape of a security."""

    model_config = ConfigDict(from_attributes=True)

    stock_id: uuid.UUID
    ticker_symbol: str
    company: str
    market: str
    #: The one nullable column in this table.
    isin: str | None


__all__ = ["Company", "Isin", "Market", "StockCreate", "StockOut", "StockUpdate", "Ticker"]
