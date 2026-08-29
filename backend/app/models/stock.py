"""The ``stocks`` and ``stock_data`` tables.

``stocks`` is reference data — one row per tradable security. ``stock_data`` is the
intraday candle series ANV-22's ingest job writes; it is by far the largest table in the
schema, so its constraints and indexes are chosen for that job's access pattern rather
than for symmetry with the others.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Date,
    ForeignKey,
    Index,
    Numeric,
    String,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:  # pragma: no cover - import cycle broken for type checkers only
    from app.models.watchlist import WatchlistData

#: Room for suffixed and class-share tickers (``BRK.B``, ``RDS.A``, ``BTC-USD``) that the
#: old ``VARCHAR(5)`` silently truncated or rejected.
TICKER_MAX_LENGTH = 16

COMPANY_MAX_LENGTH = 150
MARKET_MAX_LENGTH = 150

#: An ISIN is exactly 12 characters by definition.
ISIN_LENGTH = 12

#: Prices are stored to four decimal places. The old ``NUMERIC(8, 2)`` overflows at
#: 1,000,000.00 — Berkshire Hathaway class A already trades within one order of magnitude
#: of that, and an overflow is a hard ``DataError`` mid-ingest, not a rounding artefact.
PRICE_PRECISION = 12
PRICE_SCALE = 4


class Stock(Base):
    """A tradable security."""

    __tablename__ = "stocks"

    stock_id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    ticker_symbol: Mapped[str] = mapped_column(String(TICKER_MAX_LENGTH), unique=True)
    #: Deliberately **not** unique. The old model declared it so (the migration never
    #: applied it), but dual-class listings genuinely share a company name — GOOG and
    #: GOOGL are both "Alphabet Inc.", as are BRK.A and BRK.B. Indexed instead, because
    #: ANV-13 searches on it.
    company: Mapped[str] = mapped_column(String(COMPANY_MAX_LENGTH), index=True)
    market: Mapped[str] = mapped_column(String(MARKET_MAX_LENGTH))
    #: Nullable: the old model required it while the migration that added the column made
    #: it nullable, and AlphaVantage — the only source ANV-18/ANV-22 have — does not return
    #: an ISIN. Requiring it would make it impossible for ingest to create a stock at all.
    #: Unique where present; NULLs do not collide in Postgres.
    isin: Mapped[str | None] = mapped_column(String(ISIN_LENGTH), unique=True)

    data: Mapped[list[StockData]] = relationship(
        back_populates="stock",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    # `passive_deletes="all"` hands the decision entirely to Postgres. The FK is
    # ON DELETE RESTRICT, so deleting a stock somebody is watching must *fail*; without
    # this the ORM would helpfully load the membership rows first and try to NULL their
    # `stock_id`, turning a deliberate refusal into a confusing NOT NULL violation.
    watchlist_entries: Mapped[list[WatchlistData]] = relationship(
        back_populates="stock",
        passive_deletes="all",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Stock {self.ticker_symbol!r}>"


class StockData(Base):
    """One intraday candle for one stock.

    ``date`` and ``time`` are kept as separate columns rather than one ``timestamptz``
    because that is the shape the frontend charts and the ingest parser already speak
    (ANV-14 recombines them into a ``datetime`` in the response schema). The time is the
    exchange's local trading time, so it carries no zone of its own.
    """

    __tablename__ = "stock_data"
    __table_args__ = (
        # The idempotency key for ANV-22's upsert: one candle per stock per timestamp.
        # Its backing index is also what serves "this stock, this date range" — which is
        # every read ANV-14 performs — so a second index on the same columns would be
        # pure duplication and is deliberately not declared.
        UniqueConstraint("stock_id", "date", "time"),
        # Cross-stock date windows (a whole trading day, a backfill sweep) filter on
        # `date` alone and cannot use the leading column of the constraint above.
        Index(None, "date"),
    )

    #: ``BIGSERIAL``. The old schema referenced a hand-created ``avg_inv.stock_data_seq``
    #: that no migration ever created.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    stock_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stocks.stock_id", ondelete="CASCADE"),
    )
    date: Mapped[dt.date] = mapped_column(Date)
    time: Mapped[dt.time] = mapped_column(Time)
    open_price: Mapped[Decimal] = mapped_column(Numeric(PRICE_PRECISION, PRICE_SCALE))
    high_price: Mapped[Decimal] = mapped_column(Numeric(PRICE_PRECISION, PRICE_SCALE))
    low_price: Mapped[Decimal] = mapped_column(Numeric(PRICE_PRECISION, PRICE_SCALE))
    close_price: Mapped[Decimal] = mapped_column(Numeric(PRICE_PRECISION, PRICE_SCALE))
    volume: Mapped[int] = mapped_column(BigInteger)

    stock: Mapped[Stock] = relationship(back_populates="data")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<StockData {self.stock_id} {self.date} {self.time}>"
