"""Builders for :class:`app.models.Stock` and :class:`app.models.StockData`."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from app.models import Stock, StockData
from tests.factories.base import Factory, register

MARKETS = ("NASDAQ", "NYSE", "AMEX")

#: Candles march forward from here, five minutes apart, one sequence step at a time.
BASE_DATE = dt.date(2026, 1, 5)
FIRST_CANDLE_MINUTE = 9 * 60 + 30  # 09:30, the US open
CANDLE_INTERVAL_MINUTES = 5
MINUTES_PER_DAY = 24 * 60


def _alphabetic(n: int, width: int = 4) -> str:
    """``1 -> AAAB``: a fixed-width, strictly increasing letters-only ticker body."""
    letters = []
    for _ in range(width):
        n, remainder = divmod(n, 26)
        letters.append(chr(ord("A") + remainder))
    return "".join(reversed(letters))


@register
class StockFactory(Factory[Stock]):
    """A tradable security.

    ``ticker_symbol`` and ``isin`` are unique columns and so are sequence-derived; faker
    would repeat both within a single test.
    """

    model = Stock

    def defaults(self) -> dict[str, Any]:
        n = self.sequence()
        return {
            "ticker_symbol": _alphabetic(n),
            "company": f"Example Holdings {n} Inc.",
            "market": MARKETS[n % len(MARKETS)],
            "isin": f"US{n:010d}",
        }


@register
class StockDataFactory(Factory[StockData]):
    """One intraday candle.

    The caller supplies the parent — ``StockDataFactory().create(session, stock=stock)``
    or ``stock_id=stock.stock_id`` — because a factory has no session in
    :meth:`~tests.factories.base.Factory.build` and inventing a second ``Stock`` per candle
    would be worse than requiring one word from the test.

    ``(stock_id, date, time)`` is unique, so ``date`` and ``time`` advance with the
    sequence: consecutive candles for one stock never collide, and the run rolls onto the
    next day rather than wrapping back onto an existing minute.
    """

    model = StockData

    def defaults(self) -> dict[str, Any]:
        n = self.sequence()
        elapsed = FIRST_CANDLE_MINUTE + CANDLE_INTERVAL_MINUTES * (n - 1)
        days, minute_of_day = divmod(elapsed, MINUTES_PER_DAY)
        close = Decimal(f"{100 + n}.2500")
        return {
            "date": BASE_DATE + dt.timedelta(days=days),
            "time": dt.time(hour=minute_of_day // 60, minute=minute_of_day % 60),
            "open_price": close - Decimal("0.5000"),
            "high_price": close + Decimal("1.0000"),
            "low_price": close - Decimal("1.0000"),
            "close_price": close,
            "volume": 1_000 * n,
        }


__all__ = ["BASE_DATE", "StockDataFactory", "StockFactory"]
