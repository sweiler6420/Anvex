"""Queries over the ``stocks`` table.

Reference data: looked up constantly, written rarely (ANV-13 by hand, ANV-22 during
ingest). The search here is what the old ``/v1/stock_data`` endpoint reached for and got
wrong — it filtered with ``ticker_symbol.contains(func.lower(search))`` against
upper-cased tickers, so it matched nothing unless the search term was already upper case
*and* it silently treated an empty search as "contains ''", i.e. everything.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Stock
from app.repos.base import BaseRepo


class StockRepo(BaseRepo[Stock]):
    """Data access for :class:`app.models.Stock`."""

    model = Stock

    # -----------------------------------------------------------------------------------
    # Lookups
    # -----------------------------------------------------------------------------------

    async def get_by_id(self, session: AsyncSession, stock_id: uuid.UUID) -> Stock | None:
        """The stock with this id, or ``None``."""
        return await self._one_or_none(session, select(Stock).where(Stock.stock_id == stock_id))

    async def get_by_ticker(self, session: AsyncSession, ticker_symbol: str) -> Stock | None:
        """The stock with this exact ticker, or ``None``.

        **Exact, not case-insensitive.** ``ticker_symbol`` is unique and its index serves
        this lookup directly; folding case would turn every symbol resolution into a
        sequential scan. Tickers are canonically upper case, so normalising a
        user-supplied ``"aapl"`` is the service's job — a one-line rule, and rules do not
        live here. :meth:`list_stocks` *is* case-insensitive, because a search box is a
        different question from an identifier.
        """
        return await self._one_or_none(
            session, select(Stock).where(Stock.ticker_symbol == ticker_symbol)
        )

    async def get_by_tickers(
        self, session: AsyncSession, ticker_symbols: Sequence[str]
    ) -> list[Stock]:
        """Every stock whose ticker is in ``ticker_symbols``, in ticker order.

        One query for a batch, so ANV-22's ingest can resolve a whole vendor response's
        symbols to ``stock_id``s without a round trip per symbol. Missing symbols are
        simply absent from the result — reconciling the two lists is the caller's.
        """
        if not ticker_symbols:
            return []
        return await self._all(
            session,
            select(Stock)
            .where(Stock.ticker_symbol.in_(list(ticker_symbols)))
            .order_by(Stock.ticker_symbol),
        )

    async def list_stocks(
        self,
        session: AsyncSession,
        *,
        search: str | None = None,
        limit: int,
        offset: int = 0,
    ) -> tuple[list[Stock], int]:
        """One window of the stock list plus the total matching count.

        ``search`` is a case-insensitive substring match against **ticker or company**,
        which is how a user actually looks for a security ("nvda" or "nvidia"). ``%`` and
        ``_`` in the term are escaped, so a search for ``"100%"`` is a search for that
        text rather than a match-everything wildcard. A ``None`` or blank search means "no
        filter" — not "contains the empty string".

        Ordered by ticker, which is unique, so paging is stable: no row can appear on two
        pages or be skipped between them.
        """
        stmt = select(Stock)
        term = (search or "").strip()
        if term:
            pattern = self._contains(term)
            stmt = stmt.where(
                or_(
                    Stock.ticker_symbol.ilike(pattern, escape="\\"),
                    Stock.company.ilike(pattern, escape="\\"),
                )
            )
        stmt = stmt.order_by(Stock.ticker_symbol)
        return await self._page(session, stmt, limit=limit, offset=offset)

    # -----------------------------------------------------------------------------------
    # Uniqueness
    # -----------------------------------------------------------------------------------

    async def ticker_exists(
        self,
        session: AsyncSession,
        ticker_symbol: str,
        *,
        exclude_stock_id: uuid.UUID | None = None,
    ) -> bool:
        """Whether a stock with this ticker already exists.

        ``exclude_stock_id`` makes the check reusable on an update: "taken by somebody
        else" rather than "taken".
        """
        stmt = select(Stock.stock_id).where(Stock.ticker_symbol == ticker_symbol)
        if exclude_stock_id is not None:
            stmt = stmt.where(Stock.stock_id != exclude_stock_id)
        return await self._exists(session, stmt)

    async def isin_exists(
        self,
        session: AsyncSession,
        isin: str,
        *,
        exclude_stock_id: uuid.UUID | None = None,
    ) -> bool:
        """Whether a stock with this ISIN already exists.

        ``isin`` is nullable-but-unique, and NULLs do not collide in Postgres, so this is
        only ever asked about a real value.
        """
        stmt = select(Stock.stock_id).where(Stock.isin == isin)
        if exclude_stock_id is not None:
            stmt = stmt.where(Stock.stock_id != exclude_stock_id)
        return await self._exists(session, stmt)

    # -----------------------------------------------------------------------------------
    # Writes
    # -----------------------------------------------------------------------------------

    async def create(
        self,
        session: AsyncSession,
        *,
        ticker_symbol: str,
        company: str,
        market: str,
        isin: str | None = None,
    ) -> Stock:
        """Insert a stock and flush, so ``stock_id`` is readable."""
        return await self.add(
            session,
            Stock(ticker_symbol=ticker_symbol, company=company, market=market, isin=isin),
        )

    # `update` and `delete` are inherited unchanged. Deleting a stock somebody watches
    # raises `IntegrityError` at the flush — `watchlist_data.stock_id` is ON DELETE
    # RESTRICT (ANV-7) — and that exception is left alone for ANV-13 to map to a 409.


#: A stateless, shareable instance. Repos hold no session, so one is enough.
stock_repo = StockRepo()

__all__ = ["StockRepo", "stock_repo"]
