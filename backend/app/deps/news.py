"""The ``/v1/news`` service factory — wiring, and nothing else.

One seam per resource (``CLAUDE.md`` §3): :func:`get_news_service` resolves a session, a
:class:`~app.settings.Settings` and a vendor client out of the dependency graph, constructs
the service and returns it. Nothing is decided here, which is what makes it the single
dependency a route contract test overrides to swap the whole service for one sitting on an
in-memory repo and a stub client.

Client lifetime — the one thing worth reading here
--------------------------------------------------

This is the first dependency in the repo that builds an ``app/clients/`` object, and a client
is unlike a repo: a repo is a stateless singleton, while a client owns an
``httpx.AsyncClient`` and therefore a connection pool that has to be closed. So
:func:`get_newsapi_client` is a ``yield`` dependency that constructs one per request and
closes it in the ``finally``.

That deliberately gives up the cross-request connection pooling ``CLAUDE.md`` §3 names as the
reason the base owns its client at all — one extra TLS handshake per call — and it buys three
things worth more at this stage: no possibility of a leaked pool, no shared mutable state
between tests, and no edit to ``app/main.py``'s lifespan for a single endpoint. Keeping a
pool warm across requests means an application-scoped client owned by the lifespan and shared
with the Celery worker, which is a decision with a second and third caller (ANV-20's S3 client
and ANV-22's ingest) and belongs to whichever of them needs it first. When that happens, this
factory is the only thing that changes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.newsapi import NewsApiClient
from app.deps.session import get_session
from app.deps.settings import Settings, get_settings_dep
from app.services.news import NewsService


async def get_newsapi_client(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> AsyncIterator[NewsApiClient]:
    """A NewsAPI client for this request, closed when the response is done.

    Constructing one is cheap and opens no socket: the base creates its
    ``httpx.AsyncClient`` lazily on the first call, so a request that never reaches the
    vendor — an unknown ticker, say — costs nothing here.
    """
    client = NewsApiClient(settings)
    try:
        yield client
    finally:
        await client.aclose()


def get_news_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    client: Annotated[NewsApiClient, Depends(get_newsapi_client)],
) -> NewsService:
    """Build a :class:`~app.services.news.NewsService` for this request.

    The stock repo is left to its keyword default — repos are stateless singletons, so the
    only things that genuinely vary per request are the session and the client.
    """
    return NewsService(session, settings, client=client)


#: The annotation a ``/v1/news`` handler uses, so a route signature stays one parameter.
NewsServiceDep = Annotated[NewsService, Depends(get_news_service)]

__all__ = ["NewsServiceDep", "get_news_service", "get_newsapi_client"]
