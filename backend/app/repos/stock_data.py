"""Queries over the ``stock_data`` candle series.

By far the largest table in the schema, and the one with the sharpest access pattern: a
range read for one stock (ANV-14's charts) and a bulk write of a whole vendor response
(ANV-22's ingest). Both are served by the ``UNIQUE (stock_id, date, time)`` constraint
ANV-7 added — the read uses its index, the write uses it as the ``ON CONFLICT`` target.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Stock, StockData
from app.repos.base import BaseRepo

#: The ``ON CONFLICT`` target for :meth:`StockDataRepo.bulk_upsert` — the columns of the
#: unique constraint ANV-7 declared for exactly this purpose. Naming the columns lets
#: Postgres infer the index, so the statement does not hard-code a constraint name.
CONFLICT_COLUMNS = ("stock_id", "date", "time")

#: What a re-observation of an existing candle is allowed to change. The conflict target
#: itself is excluded by definition, and ``id`` is the row's identity — an upsert must not
#: renumber a candle a foreign key might one day point at.
UPDATABLE_COLUMNS = (
    "open_price",
    "high_price",
    "low_price",
    "close_price",
    "volume",
)


class StockDataRepo(BaseRepo[StockData]):
    """Data access for :class:`app.models.StockData`."""

    model = StockData

    # -----------------------------------------------------------------------------------
    # Lookups
    # -----------------------------------------------------------------------------------

    async def get_by_id(self, session: AsyncSession, candle_id: int) -> StockData | None:
        """The candle with this id, or ``None``. ``stock_data.id`` is a ``BIGSERIAL``."""
        return await self._one_or_none(session, select(StockData).where(StockData.id == candle_id))

    async def get_at(
        self,
        session: AsyncSession,
        stock_id: uuid.UUID,
        date: dt.date,
        time: dt.time,
    ) -> StockData | None:
        """The one candle at this exact ``(stock, date, time)``, or ``None``.

        The natural key. Mostly useful for asserting on an upsert's effect and for a
        service that wants to know whether a specific minute has been ingested.
        """
        return await self._one_or_none(
            session,
            select(StockData).where(
                StockData.stock_id == stock_id,
                StockData.date == date,
                StockData.time == time,
            ),
        )

    async def get_latest_for_stock(
        self, session: AsyncSession, stock_id: uuid.UUID
    ) -> StockData | None:
        """The most recent candle for this stock, or ``None`` when it has none.

        ANV-22 resumes an ingest from here rather than re-fetching a stock's whole
        history. *Where* to resume from — the windowing rule — is pure and lives in
        ``app/domain/``; this only reports the newest row.
        """
        return await self._one_or_none(
            session,
            select(StockData)
            .where(StockData.stock_id == stock_id)
            .order_by(StockData.date.desc(), StockData.time.desc(), StockData.id.desc()),
        )

    # -----------------------------------------------------------------------------------
    # Range reads
    # -----------------------------------------------------------------------------------

    @staticmethod
    def _in_range(
        stmt: Select[tuple[StockData]],
        start: dt.date | None,
        end: dt.date | None,
    ) -> Select[tuple[StockData]]:
        """Apply an **inclusive** ``[start, end]`` date filter; either bound may be open."""
        if start is not None:
            stmt = stmt.where(StockData.date >= start)
        if end is not None:
            stmt = stmt.where(StockData.date <= end)
        return stmt

    @staticmethod
    def _chronological(stmt: Select[tuple[StockData]]) -> Select[tuple[StockData]]:
        """Order oldest-first by ``date``, ``time``, then ``id``.

        A chart plots left to right, so chronological is the useful order. ``id`` breaks
        the tie the unique constraint already makes impossible — it costs nothing and it
        guarantees paging never repeats or skips a row.
        """
        return stmt.order_by(StockData.date.asc(), StockData.time.asc(), StockData.id.asc())

    async def list_for_stock(
        self,
        session: AsyncSession,
        stock_id: uuid.UUID,
        *,
        start: dt.date | None = None,
        end: dt.date | None = None,
        limit: int,
        offset: int = 0,
    ) -> tuple[list[StockData], int]:
        """One chronological window of a stock's candles, plus the total in range.

        The date bounds are inclusive and independently optional, so "everything",
        "everything since", "everything until" and "this window" are one method rather
        than four.
        """
        stmt = self._in_range(select(StockData).where(StockData.stock_id == stock_id), start, end)
        return await self._page(session, self._chronological(stmt), limit=limit, offset=offset)

    async def list_for_ticker(
        self,
        session: AsyncSession,
        ticker_symbol: str,
        *,
        start: dt.date | None = None,
        end: dt.date | None = None,
        limit: int,
        offset: int = 0,
    ) -> tuple[list[StockData], int]:
        """:meth:`list_for_stock`, keyed by ticker instead of id.

        One join rather than a symbol lookup followed by a range query, because that is
        the shape of the request the charts actually make (``/v1/stock-data?ticker=NVDA``).
        An unknown ticker yields ``([], 0)``: a repo reports emptiness, it does not
        distinguish "no such stock" from "no candles" — that is a service's judgement.
        """
        stmt = self._in_range(
            select(StockData)
            .join(Stock, Stock.stock_id == StockData.stock_id)
            .where(Stock.ticker_symbol == ticker_symbol),
            start,
            end,
        )
        return await self._page(session, self._chronological(stmt), limit=limit, offset=offset)

    async def list_for_stock_with_stock(
        self,
        session: AsyncSession,
        stock_id: uuid.UUID,
        *,
        start: dt.date | None = None,
        end: dt.date | None = None,
        limit: int,
        offset: int = 0,
    ) -> tuple[list[StockData], int]:
        """:meth:`list_for_stock` with each candle's ``stock`` eagerly loaded.

        Separate from :meth:`list_for_stock` rather than a flag, because the eager load is
        a real cost and most callers already know which stock they asked about. Reach for
        it when the response needs the ticker beside every candle; touching ``.stock`` on
        a row from :meth:`list_for_stock` raises ``MissingGreenlet``.
        """
        stmt = self._in_range(select(StockData).where(StockData.stock_id == stock_id), start, end)
        stmt = self._chronological(stmt).options(selectinload(StockData.stock))
        return await self._page(session, stmt, limit=limit, offset=offset)

    async def count_for_stock(self, session: AsyncSession, stock_id: uuid.UUID) -> int:
        """How many candles this stock has."""
        return await self._count(
            session, select(StockData.id).where(StockData.stock_id == stock_id)
        )

    # -----------------------------------------------------------------------------------
    # Bulk write
    # -----------------------------------------------------------------------------------

    async def bulk_upsert(self, session: AsyncSession, rows: Iterable[Mapping[str, Any]]) -> int:
        """Insert or update many candles in **one** statement; returns rows written.

        ``INSERT ... ON CONFLICT (stock_id, date, time) DO UPDATE``. That conflict target
        is the unique constraint ANV-7 added for precisely this reason: **idempotency is a
        database rule here, not a code habit.** Re-running the same ingest re-writes the
        same rows instead of duplicating them, whatever the job's retry story looks like,
        and a vendor that revises a candle's volume after the close simply overwrites it.

        Only :data:`UPDATABLE_COLUMNS` are overwritten — the natural key is the match
        condition and ``id`` is identity, so an existing candle keeps its row.

        Each mapping must carry every non-generated column:
        ``{stock_id, date, time, open_price, high_price, low_price, close_price, volume}``.

        Two things the caller owns:

        * **Deduplicate first.** Postgres rejects a statement whose ``VALUES`` hit the same
          conflict target twice ("cannot affect row a second time"), and *which* duplicate
          should win is a rule — so it belongs in ``app/domain/``, not here.
        * **Refresh afterwards if you still hold ORM objects.** This is a Core statement,
          so already-loaded :class:`~app.models.StockData` instances in the session's
          identity map are not updated. Re-query, or ``session.expire_all()``.

        An empty ``rows`` returns ``0`` without touching the database — an empty ``VALUES``
        is a syntax error, and "nothing to ingest" is a perfectly normal outcome.
        """
        values = list(rows)
        if not values:
            return 0

        stmt = pg_insert(StockData).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=list(CONFLICT_COLUMNS),
            set_={column: getattr(stmt.excluded, column) for column in UPDATABLE_COLUMNS},
        ).returning(StockData.id)

        result = await session.execute(stmt)
        return len(result.scalars().all())

    # There is deliberately no bulk delete. `stock_data.stock_id` is ON DELETE CASCADE, so
    # removing a stock removes its series in one statement inside Postgres; anything
    # narrower than that would be a retention *policy*, which nobody has written yet.


#: A stateless, shareable instance. Repos hold no session, so one is enough.
stock_data_repo = StockDataRepo()

__all__ = ["CONFLICT_COLUMNS", "UPDATABLE_COLUMNS", "StockDataRepo", "stock_data_repo"]
