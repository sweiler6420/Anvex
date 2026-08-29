"""``/v1/news`` — live headlines, and everything written about one security.

Written to the handler shape ``app/api/v1/auth.py`` established (``CLAUDE.md`` §3): accept a
validated request, call **one** service method, return a schema. No ``try``, no ``if``, no
session, no ``HTTPException``. Why the routes behave as they do is documented in
``app/services/news.py``.

**What this replaces.** The old ``/v1/news/`` handler was a 290-line function whose body was
a December 2023 JSON literal, returned verbatim to every caller forever, with the real
``requests.get`` commented out beneath it and a live API key in that comment. It required a
bearer token to serve a constant. These two routes make the call it never made; nothing else
about it survives.

**Authenticated, like everything else.** The router it replaces required a token, and a
metered third-party quota is a better reason to keep the requirement than reference data was.

**Route ordering.** ``/top`` is declared before ``/by-symbol/{ticker}``. Neither can shadow
the other — they differ in their first segment — but ``CLAUDE.md`` §4's literal-first rule is
followed anyway so the next reader does not have to re-derive that.

The ticker path parameter is a **plain string**: normalisation is the service's job, not the
edge's (see ``app/services/stock.py``).

**Two failure codes a client has to expect on these routes and nowhere else.** ``not_found``
means the ticker is not a security Anvex knows — never "there is no news", which is a 200
with an empty page. ``external_service_error`` (502) means the vendor call did not produce a
feed, and ``details.reason`` says which kind: ``not_configured`` is Anvex's own fault and is
what a fresh clone with a blank ``NEWSAPI_API_KEY`` gets on every call, ``rate_limited`` is
worth retrying later, and the rest are not.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from app.deps.auth import CurrentUser
from app.deps.news import NewsServiceDep
from app.schemas.errors import ErrorResponse
from app.schemas.news import NewsArticleOut
from app.schemas.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, Page

router = APIRouter(prefix="/news", tags=["news"])

#: Documented on both routes: they are guarded, so a 401 here is ordinary traffic and the
#: client needs to know which ``code`` to branch on — ``token_expired`` means refresh, the
#: rest mean sign in again.
UNAUTHORIZED_RESPONSE = {
    status.HTTP_401_UNAUTHORIZED: {
        "model": ErrorResponse,
        "description": (
            "`unauthorized` (no credentials, or a deleted account), `invalid_token`, "
            "`token_expired`, or `wrong_token_type`."
        ),
    }
}

UPSTREAM_RESPONSE = {
    status.HTTP_502_BAD_GATEWAY: {
        "model": ErrorResponse,
        "description": (
            "`external_service_error` — the news provider did not answer with a feed. "
            "`details.reason` distinguishes the cases: `not_configured` (no "
            "`NEWSAPI_API_KEY` is set in this deployment — `details.setting` names it), "
            "`rate_limited` (worth retrying later), `client_error`, `server_error`, "
            "`transport_error` or `malformed_response`."
        ),
    }
}

NOT_FOUND_RESPONSE = {
    status.HTTP_404_NOT_FOUND: {
        "model": ErrorResponse,
        "description": (
            "`not_found` — no such security. A security that exists but has no coverage is "
            "a 200 with an empty page."
        ),
    }
}


@router.get(
    "/top",
    response_model=Page[NewsArticleOut],
    summary="Top headlines",
    responses={**UNAUTHORIZED_RESPONSE, **UPSTREAM_RESPONSE},
)
async def read_top_stories(
    user: CurrentUser,
    service: NewsServiceDep,
    category: Annotated[
        str | None,
        Query(
            description=(
                "The provider's own slice of the news — `business`, `technology`, "
                "`health`, … Omitted means general news."
            ),
            examples=["business"],
        ),
    ] = None,
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=MAX_PAGE_LIMIT,
            description="Window size. Above the ceiling is a 422, never a silent clamp.",
        ),
    ] = DEFAULT_PAGE_LIMIT,
    offset: Annotated[int, Query(ge=0, description="Articles to skip.")] = 0,
) -> Page[NewsArticleOut]:
    """One window of the current headlines, freshest and most complete first.

    ``total`` counts the **distinct** stories in the batch fetched from the provider, not the
    provider's own match count: one wire story reaches it through every outlet that ran it,
    and those are merged before the window is taken. See ``app/services/news.py``.
    """
    return await service.top_stories(category=category, limit=limit, offset=offset)


@router.get(
    "/by-symbol/{ticker}",
    response_model=Page[NewsArticleOut],
    summary="News about one security",
    responses={**UNAUTHORIZED_RESPONSE, **NOT_FOUND_RESPONSE, **UPSTREAM_RESPONSE},
)
async def read_news_by_symbol(
    ticker: Annotated[
        str,
        Path(description="Ticker symbol, in any casing — it is normalised server-side."),
    ],
    user: CurrentUser,
    service: NewsServiceDep,
    limit: Annotated[
        int,
        Query(ge=1, le=MAX_PAGE_LIMIT, description="Window size."),
    ] = DEFAULT_PAGE_LIMIT,
    offset: Annotated[int, Query(ge=0, description="Articles to skip.")] = 0,
) -> Page[NewsArticleOut]:
    """Coverage of one security. ``aapl`` and ``AAPL`` name the same company.

    The symbol is resolved against Anvex's securities first, so an unknown ticker is a 404
    rather than an empty feed the caller cannot interpret — and so the provider is asked
    about the *company*, not about a three-letter word.
    """
    return await service.for_symbol(ticker=ticker, limit=limit, offset=offset)


__all__ = ["router"]
