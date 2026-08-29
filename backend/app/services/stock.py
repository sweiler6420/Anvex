"""Reading the ``stocks`` reference table: list, search, resolve by id, resolve by ticker.

Written to the shape ``app/services/auth.py`` and ``app/services/user.py`` established
(``CLAUDE.md`` §3) — collaborators in the constructor, one ``async`` method per use case, a
schema out, ``app.domain.errors`` on the way out.

**This service is read-only, and deliberately so.** Nothing in the product creates a
security by hand yet: ANV-22's ingest will, through this layer, once there is a vendor
client to create one *from*. So there is no ``create`` here and no ``delete`` — and in
particular no attempt to pre-empt the ``ON DELETE RESTRICT`` on ``watchlist_data.stock_id``
(ANV-7), which is documented in ``app/repos/base.py`` for whichever ticket genuinely needs
to delete a stock.

Two rules in this module are the ones every later list endpoint copies.

**The service builds the** :class:`~app.schemas.pagination.Page` **envelope.** A repo
returns ``(rows, total)`` and does not import ``app.schemas`` (``CLAUDE.md`` §3), so
``total`` — counted *before* the window — arrives as a bare integer and is wrapped here.
That is also why ``limit`` is a required keyword on every paginated repo method rather than
defaulting to :data:`~app.schemas.pagination.DEFAULT_PAGE_LIMIT`: the default is a schema
concept, so supplying it is this layer's job.

**Ticker normalisation happens in the service layer** — the rule itself now lives in
:func:`app.domain.stock.normalise_ticker`, moved there by ANV-14 when
``app/services/stock_data.py`` became its second caller, and re-exported from this module so
``from app.services.stock import normalise_ticker`` still resolves.
:meth:`~app.repos.stock.StockRepo.get_by_ticker` is
an exact, case-sensitive match on a unique index — folding case in the repo would turn
every symbol resolution into a sequential scan — so upper-casing and trimming the caller's
``"  aapl "`` is the service's one-line rule. ANV-8's annotated
:data:`~app.schemas.stock.Ticker` already does this for request *bodies*, and it does in
fact apply to a **path** parameter too if you annotate one with it (verified, not assumed).
It is deliberately not relied on: a path parameter is not a body, the API is not the only
caller (a Celery task holds a plain string from a vendor response), and a normalisation
that lives in the request schema cannot help any of them. So the rule lives in the one
place every entry point goes through, and the route passes the raw segment down.
"""

from __future__ import annotations

import uuid
from typing import Final

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import NotFoundError
from app.domain.stock import normalise_ticker
from app.models import Stock
from app.repos.stock import StockRepo, stock_repo
from app.schemas.pagination import Page, resolve_page_limit
from app.schemas.stock import StockOut
from app.settings import Settings

logger = structlog.get_logger("anvex.stocks")

#: The resource name every error in this module reports, so ``details["resource"]`` is the
#: same string whether the caller looked the security up by id or by ticker.
RESOURCE: Final[str] = "stock"


class StockService:
    """Read access to the securities reference table."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        stocks: StockRepo = stock_repo,
    ) -> None:
        self.session = session
        self.settings = settings
        #: Keyword-defaulted to the module-level singleton, which is the seam a unit test
        #: replaces with :class:`tests.helpers.FakeStockRepo` to run without Postgres.
        self.stocks = stocks

    # -----------------------------------------------------------------------------------
    # Use cases
    # -----------------------------------------------------------------------------------

    async def list_stocks(
        self,
        *,
        search: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> Page[StockOut]:
        """One window of the securities list, wrapped in the standard envelope.

        ``search`` is a case-insensitive substring match against ticker **or** company,
        which is how somebody actually looks for a security ("nvda" or "nvidia"); blank or
        ``None`` means no filter at all.

        ``limit`` is resolved by :func:`~app.schemas.pagination.resolve_page_limit`:
        ``None`` becomes :data:`~app.schemas.pagination.DEFAULT_PAGE_LIMIT` and anything
        above :data:`~app.schemas.pagination.MAX_PAGE_LIMIT` is clamped down to it. The
        HTTP edge rejects an over-large ``limit`` with a 422 before it ever reaches here
        (``app/api/v1/stocks.py``) so an HTTP caller is never quietly given a shorter page
        than it asked for; the clamp is what protects the *other* callers — a job or a
        script passing ``limit=10_000`` — from asking Postgres for the whole table and
        then failing :class:`~app.schemas.pagination.Page`'s own ``le`` bound with a 500.

        ``total`` counts every matching row regardless of the window, so an ``offset`` past
        the end is an empty page with a non-zero total rather than a lie about the size of
        the collection.
        """
        window = resolve_page_limit(limit)
        rows, total = await self.stocks.list_stocks(
            self.session, search=search, limit=window, offset=offset
        )
        return self._page(rows, total, limit=window, offset=offset)

    async def get_stock(self, *, stock_id: uuid.UUID) -> StockOut:
        """The security with this id.

        :raises NotFoundError: no stock has that id.
        """
        stock = await self.stocks.get_by_id(self.session, stock_id)
        if stock is None:
            raise NotFoundError(RESOURCE, stock_id)
        return StockOut.model_validate(stock)

    async def get_stock_by_ticker(self, *, ticker: str) -> StockOut:
        """The security this ticker names, in any casing the caller happens to hold.

        The ticker is normalised here — see the module docstring for why that is not left
        to the request schema. The error reports the **canonical** spelling, so a caller
        that searched for ``"aapl"`` is told ``"stock 'AAPL' was not found."`` and can see
        that the lookup was not simply a casing mistake.

        :raises NotFoundError: no stock carries that ticker.
        """
        symbol = normalise_ticker(ticker)
        stock = await self.stocks.get_by_ticker(self.session, symbol)
        if stock is None:
            raise NotFoundError(RESOURCE, symbol)
        return StockOut.model_validate(stock)

    # -----------------------------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------------------------

    @staticmethod
    def _page(rows: list[Stock], total: int, *, limit: int, offset: int) -> Page[StockOut]:
        """Wrap ``(rows, total)`` from the repo in the response envelope.

        The projection to :class:`~app.schemas.stock.StockOut` happens on the way in, so no
        ORM instance ever reaches the API (``CLAUDE.md`` §3), and ``has_more`` is left to
        the schema's computed field rather than derived a second time here.
        """
        return Page[StockOut](
            items=[StockOut.model_validate(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )


__all__ = ["RESOURCE", "StockService", "normalise_ticker"]
