"""Serving news: the front page, and everything written about one security.

Written to the shape ``app/services/stock.py`` established (``CLAUDE.md`` §3) —
collaborators in the constructor, one ``async`` method per use case, keyword-only arguments,
a schema out, ``app.domain.errors`` on the way out. It is the first service in the repo that
composes a **client** with a repo and a domain rule, which is the shape §3's worked example
describes and the reason the layering exists at all:

* ``app/clients/newsapi.py`` answers *how do we fetch it* — and knows nothing else.
* ``app/domain/news.py`` answers *what are the rules* — and does no I/O.
* ``app/repos/stock.py`` answers *how do we store it* — for the one thing Anvex does store.
* This module answers *what does the app do*, and owns the clock.

Nothing is persisted. There is no ``NewsRepo`` and no ``news`` table: an article is a
third-party document with its own lifecycle, and storing a copy would buy a cache and a
staleness problem in exchange for a licensing question. Every request is a live read.

Paging is local, and the count is honest about what it counts
-------------------------------------------------------------

``CLAUDE.md`` §4 requires a list endpoint to return :class:`~app.schemas.pagination.Page`,
and the envelope's ``total`` has a fixed meaning: every row matching the request, counted
before the window. NewsAPI's own paging cannot supply that here, because de-duplication
happens **after** the fetch — the vendor's ``totalResults`` counts a story once per outlet
that ran it, and asking for "articles 20-29 of the de-duplicated set" is not a question the
vendor can be asked.

So the service fetches **one** vendor page of :data:`VENDOR_PAGE_SIZE`, de-duplicates and
ranks it, and windows the result itself. ``total`` is therefore the number of *distinct*
stories in that page, which is exactly the number ``offset`` and ``limit`` index into — the
envelope stays internally consistent, and ``has_more`` means what it says. The alternative,
forwarding the vendor's count, would produce a ``total`` of 3,412 above a collection that
ends at 74 and a ``has_more`` that lies on the last page.

One vendor page is not a limitation in practice: :data:`VENDOR_PAGE_SIZE` is NewsAPI's own
maximum, and a hundred headlines de-duplicate to more than any client will scroll. It *is* a
deliberate ceiling, and a caller that needs the archive wants a different endpoint.

``by-symbol`` resolves the ticker first, and that is not only for the 404
------------------------------------------------------------------------

:meth:`NewsService.for_symbol` looks the security up in ``stocks`` before it calls NewsAPI,
and an unknown ticker is a :class:`~app.domain.errors.NotFoundError` rather than an empty
feed. Three reasons, in increasing order of importance:

1. **The vendor cannot tell you.** ``/v2/everything?q="ZZZZ"`` answers ``{"status": "ok",
   "totalResults": 0, "articles": []}`` — byte-identical to a real company nobody wrote
   about this week. Only the stocks table can distinguish a typo from a quiet week, so if
   this service does not ask, the question can never be answered. This is ANV-14's rule for
   a nested collection (``CLAUDE.md`` §4: a missing parent is a 404, an empty child is a
   200), applied to a child that lives upstream instead of in Postgres.
2. **It costs nothing and saves a call.** The lookup is a unique-index hit; the request it
   avoids is a round trip against a metered quota.
3. **It makes the query better, which is the real reason.** ``q="CAT"`` returns articles
   about cats; ``q="CAT" OR "Caterpillar Inc."`` returns articles about the company. The
   company name only exists in the stocks row, so resolving is not a precondition of the
   good query — it *is* the good query. Querying the vendor blind would mean shipping the
   worse product to avoid a 404.

The ticker is normalised here rather than at the edge, for the reason
``app/services/stock.py`` sets out: the repo lookup is exact and case-sensitive so it can use
the unique index, and the API is not the only caller.
"""

from __future__ import annotations

import datetime as dt
from typing import Final

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.newsapi import MAX_PAGE_SIZE, NewsApiClient, NewsArticle
from app.domain.errors import NotFoundError
from app.domain.news import dedupe_and_rank, search_terms
from app.domain.pagination import PageWindow, resolve_window
from app.domain.stock import normalise_ticker
from app.repos.stock import StockRepo, stock_repo
from app.schemas.news import NewsArticleOut
from app.schemas.pagination import Page
from app.settings import Settings

logger = structlog.get_logger("anvex.news")

#: The resource a 404 from this module names. Deliberately ``"stock"`` and not ``"news"``:
#: the thing that was not found *is* the security, and a real security nobody wrote about is
#: a 200 with an empty page. The same spelling ``app.services.stock`` and
#: ``app.services.stock_data`` use, so a client branching on ``details["resource"]`` sees one
#: string whichever endpoint refused it — asserted by a unit test.
RESOURCE: Final[str] = "stock"

#: How many articles to ask the vendor for per call. NewsAPI's own maximum: see the module
#: docstring for why one page is fetched and windowed locally rather than paged upstream.
VENDOR_PAGE_SIZE: Final[int] = MAX_PAGE_SIZE


class NewsService:
    """Live news, ranked and de-duplicated. Reads only, and stores nothing."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        client: NewsApiClient,
        stocks: StockRepo = stock_repo,
    ) -> None:
        self.session = session
        self.settings = settings
        #: Required rather than keyword-defaulted, unlike a repo: a client owns a connection
        #: pool and therefore a lifetime, so there is no module-level singleton to default
        #: to. ``app/deps/news.py`` builds and closes one per request and is the seam a route
        #: test overrides.
        self.client = client
        self.stocks = stocks

    # -----------------------------------------------------------------------------------
    # Use cases
    # -----------------------------------------------------------------------------------

    async def top_stories(
        self,
        *,
        category: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> Page[NewsArticleOut]:
        """The front page: one window of the ranked, de-duplicated headlines.

        :param category: NewsAPI's own slice — ``business``, ``technology``, … ``None`` is
            general news, which is what the endpoint this replaces served.
        :raises ExternalServiceError: the vendor failed, or no key is configured. The second
            is the default state of a fresh clone and says so in ``details["reason"]``.
        """
        window = resolve_window(limit=limit, offset=offset)
        feed = await self.client.fetch_top_headlines(category=category, page_size=VENDOR_PAGE_SIZE)
        return self._page(feed.articles, window, query=category or "top")

    async def for_symbol(
        self,
        *,
        ticker: str,
        limit: int | None = None,
        offset: int | None = None,
    ) -> Page[NewsArticleOut]:
        """Everything written about one security.

        The ticker is resolved against ``stocks`` first — see the module docstring for the
        three reasons, of which building a query out of the company name is the real one.

        :raises NotFoundError: no security carries that ticker. Reported with the
            **canonical** spelling, so a caller who searched for ``"aapl"`` can see the
            lookup was not simply a casing mistake.
        :raises ExternalServiceError: as above.
        """
        window = resolve_window(limit=limit, offset=offset)
        symbol = normalise_ticker(ticker)
        stock = await self.stocks.get_by_ticker(self.session, symbol)
        if stock is None:
            raise NotFoundError(RESOURCE, symbol)

        query = search_terms(stock.ticker_symbol, stock.company)
        feed = await self.client.fetch_everything(query, page_size=VENDOR_PAGE_SIZE)
        return self._page(feed.articles, window, query=query)

    # -----------------------------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------------------------

    def _page(
        self,
        articles: tuple[NewsArticle, ...],
        window: PageWindow,
        *,
        query: str,
    ) -> Page[NewsArticleOut]:
        """Rank, de-duplicate, window, and wrap in the response envelope.

        The clock is read **once**, here, and injected into the domain rule — ``CLAUDE.md``
        §4 makes a service the only layer allowed to read one, which is also what makes every
        recency assertion in ``tests/unit/test_domain_news.py`` testable without a ``sleep``.
        """
        ranked = dedupe_and_rank(articles, now=dt.datetime.now(dt.UTC))
        page = ranked[window.offset : window.offset + window.limit]
        logger.debug(
            "news.window",
            query=query,
            fetched=len(articles),
            distinct=len(ranked),
            returned=len(page),
        )
        return Page[NewsArticleOut](
            items=[NewsArticleOut.model_validate(article) for article in page],
            # Everything that survived de-duplication, which is what `offset` indexes into.
            # Not the vendor's `totalResults` — see the module docstring.
            total=len(ranked),
            limit=window.limit,
            offset=window.offset,
        )


__all__ = ["RESOURCE", "VENDOR_PAGE_SIZE", "NewsService"]
