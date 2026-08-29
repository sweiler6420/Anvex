"""Route contract tests for ``/v1/news``.

The API tier (``CLAUDE.md`` §6): real routers, real middleware, real error envelope, **no
database**. ``get_news_service`` and ``get_auth_service`` are redirected at real services
sitting on an in-memory repo, so the route, the guard, the service's own branches and the
envelope are all genuinely under test with Docker stopped.

This module differs from its siblings in one deliberate way: the service it installs holds a
**real** :class:`~app.clients.newsapi.NewsApiClient` with ``respx`` in front of it, rather
than a fake. Everything below the route except the socket is therefore the shipping code —
the parser, the 200-that-means-failure check, the error taxonomy, the domain's ranking, the
projection onto ``NewsArticleOut``, and the middleware that turns
:class:`~app.domain.errors.ExternalServiceError` into a 502 body. The two behaviours this
ticket had to *decide* are exactly the two that only show up end to end:

* **an unknown ticker is a 404**, not an empty feed, and the vendor is never called for one;
* **an unconfigured key is a 502 that says so**, in the response body, with no log-reading
  and no round trip.

All payloads are hand-built from NewsAPI's documented shape — see
``tests/integration/test_client_newsapi.py``. No key exists anywhere in this repository.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator
from typing import Any

import pytest
import respx
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.clients.newsapi import EVERYTHING_PATH, TOP_HEADLINES_PATH, NewsApiClient
from app.deps.auth import get_auth_service
from app.deps.news import get_news_service
from app.domain.auth import ACCESS_TOKEN_TYPE, create_token
from app.main import create_app
from app.schemas.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT
from app.services.auth import AuthService
from app.services.news import NewsService
from app.settings import Settings
from tests.helpers import (
    FakeStockRepo,
    FakeUserRepo,
    StubSession,
    assert_error_envelope,
    make_stock,
    make_user,
)

SECRET = "api-tier-jwt-secret"
ALGORITHM = "HS256"
API_KEY = "newsapi-api-tier-key"

NEWS_URL = "/v1/news"
TOP_URL = f"{NEWS_URL}/top"
BY_SYMBOL_URL = f"{NEWS_URL}/by-symbol"

TOP_ENDPOINT = f"https://newsapi.org{TOP_HEADLINES_PATH}"
EVERYTHING_ENDPOINT = f"https://newsapi.org{EVERYTHING_PATH}"

PUBLISHED = "2026-03-02T11:00:00Z"


def article(
    *,
    title: str,
    url: str,
    outlet: str = "Reuters",
    published: str = PUBLISHED,
    description: str | None = None,
    image: str | None = None,
) -> dict[str, Any]:
    """One article in NewsAPI's documented per-item shape, hand-built."""
    return {
        "source": {"id": None, "name": outlet},
        "author": f"{outlet} staff",
        "title": title,
        "description": description,
        "url": url,
        "urlToImage": image,
        "publishedAt": published,
        "content": "Truncated teaser… [+2541 chars]",
    }


def feed(*articles: dict[str, Any], total: int = 36) -> dict[str, Any]:
    return {"status": "ok", "totalResults": total, "articles": list(articles)}


#: One wire story, carried by two outlets with each one's masthead glued onto the headline —
#: which is what makes it a duplicate the raw strings cannot detect.
SYNDICATED = [
    article(
        title="Fed holds rates steady - Reuters",
        url="https://www.reuters.com/markets/fed-holds-2026-03-02/?utm_source=twitter",
        outlet="Reuters",
        description="No change to the target range.",
        image="https://reuters.com/lead.jpg",
    ),
    article(
        title="Fed holds rates steady | CNBC",
        url="https://www.cnbc.com/2026/03/02/fed-holds.html",
        outlet="CNBC",
    ),
]

DISTINCT = article(
    title="Chip demand keeps climbing - Financial Times",
    url="https://www.ft.com/content/chips-2026",
    outlet="Financial Times",
    published="2026-03-02T09:00:00Z",
)


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """Pin the JWT configuration and a vendor key, per ``CLAUDE.md`` §6's idiom."""
    return settings.model_copy(
        update={
            "jwt_secret_key": SecretStr(SECRET),
            "jwt_algorithm": ALGORITHM,
            "jwt_access_token_expire_minutes": 30,
            "jwt_refresh_token_expire_minutes": 60 * 24 * 7,
            "newsapi_api_key": SecretStr(API_KEY),
        }
    )


@pytest.fixture
def account() -> Any:
    return make_user(username="stephen1", email="stephen@example.com")


@pytest.fixture
def apple() -> Any:
    return make_stock(ticker_symbol="AAPL", company="Apple Inc.")


@pytest.fixture
def stocks(apple: Any) -> FakeStockRepo:
    return FakeStockRepo(apple)


def install(app: FastAPI, settings: Settings, stocks: FakeStockRepo, account: Any) -> FastAPI:
    """Point the two service seams at real services with no database behind them."""
    session = StubSession()
    app.dependency_overrides[get_news_service] = lambda: NewsService(
        session,  # type: ignore[arg-type]
        settings,
        client=NewsApiClient(settings),
        stocks=stocks,  # type: ignore[arg-type]
    )
    app.dependency_overrides[get_auth_service] = lambda: AuthService(
        session=session,  # type: ignore[arg-type]
        settings=settings,
        users=FakeUserRepo(account),  # type: ignore[arg-type]
    )
    return app


@pytest.fixture
def app(app: FastAPI, settings: Settings, stocks: FakeStockRepo, account: Any) -> FastAPI:
    return install(app, settings, stocks, account)


@pytest.fixture
def keyless_app(settings: Settings, stocks: FakeStockRepo, account: Any) -> FastAPI:
    """The application a fresh clone gets: ``NEWSAPI_API_KEY`` blank.

    A **second** application rather than the shared ``app`` fixture reconfigured, so a test
    can hold both at once and compare their answers. Overrides are per-application, and
    installing twice on one instance would simply mean the last call won.
    """
    return install(
        create_app(settings.model_copy(update={"newsapi_api_key": SecretStr("")})),
        settings.model_copy(update={"newsapi_api_key": SecretStr("")}),
        stocks,
        account,
    )


@pytest.fixture
def auth(account: Any) -> dict[str, str]:
    token = create_token(
        subject=account.user_id,
        token_type=ACCESS_TOKEN_TYPE,  # type: ignore[arg-type]
        now=dt.datetime.now(dt.UTC),
        lifetime=dt.timedelta(minutes=30),
        secret=SECRET,
        algorithm=ALGORITHM,
    )
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------------------
# authentication
# ---------------------------------------------------------------------------------------


class TestAuthenticationIsRequired:
    """The router this replaces required a token to serve a hardcoded constant. These routes
    make a metered third-party call, which is a much better reason to keep the requirement.
    """

    @pytest.fixture
    def anonymous_urls(self) -> list[str]:
        return [TOP_URL, f"{BY_SYMBOL_URL}/AAPL"]

    async def test_both_routes_refuse_an_anonymous_caller(
        self, client: AsyncClient, anonymous_urls: list[str]
    ) -> None:
        for url in anonymous_urls:
            response = await client.get(url)

            assert_error_envelope(response, status=401, code="unauthorized")
            assert response.headers.get("www-authenticate") == "Bearer", url

    async def test_both_routes_refuse_a_garbage_token(
        self, client: AsyncClient, anonymous_urls: list[str]
    ) -> None:
        for url in anonymous_urls:
            response = await client.get(url, headers={"Authorization": "Bearer nonsense"})

            assert_error_envelope(response, status=401, code="invalid_token")

    async def test_a_refresh_token_is_not_an_access_token(
        self, client: AsyncClient, account: Any
    ) -> None:
        refresh = create_token(
            subject=account.user_id,
            token_type="refresh",  # type: ignore[arg-type]
            now=dt.datetime.now(dt.UTC),
            lifetime=dt.timedelta(days=7),
            secret=SECRET,
            algorithm=ALGORITHM,
        )

        response = await client.get(TOP_URL, headers={"Authorization": f"Bearer {refresh}"})

        assert_error_envelope(response, status=401, code="wrong_token_type")

    async def test_an_anonymous_call_never_reaches_the_vendor(
        self, client: AsyncClient, mock_http: respx.MockRouter
    ) -> None:
        """The guard runs before the service, so an unauthenticated request costs no quota."""
        route = mock_http.get(TOP_ENDPOINT).respond(200, json=feed(DISTINCT))

        await client.get(TOP_URL)

        assert route.call_count == 0

    async def test_neither_route_is_public_by_accident(self, app: FastAPI) -> None:
        paths = app.openapi()["paths"]

        for path in ("/v1/news/top", "/v1/news/by-symbol/{ticker}"):
            assert "security" in paths[path]["get"], path


# ---------------------------------------------------------------------------------------
# GET /v1/news/top
# ---------------------------------------------------------------------------------------


class TestTopHeadlines:
    async def test_it_returns_the_standard_page_envelope(
        self, client: AsyncClient, auth: dict[str, str], mock_http: respx.MockRouter
    ) -> None:
        mock_http.get(TOP_ENDPOINT).respond(200, json=feed(*SYNDICATED, DISTINCT))

        response = await client.get(TOP_URL, headers=auth)

        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"items", "total", "limit", "offset", "has_more"}
        assert body["limit"] == DEFAULT_PAGE_LIMIT
        assert body["offset"] == 0

    async def test_the_syndicated_copy_of_one_story_is_merged(
        self, client: AsyncClient, auth: dict[str, str], mock_http: respx.MockRouter
    ) -> None:
        """The whole point of ``app/domain/news.py``, asserted through the API: two outlets,
        two links, one story — and the richer copy is the one that survives."""
        mock_http.get(TOP_ENDPOINT).respond(200, json=feed(*SYNDICATED, DISTINCT, total=3))

        body = (await client.get(TOP_URL, headers=auth)).json()

        assert body["total"] == 2
        assert len(body["items"]) == 2
        assert body["items"][0]["source_name"] == "Reuters"
        assert body["items"][0]["url_to_image"] == "https://reuters.com/lead.jpg"

    async def test_an_item_carries_the_documented_fields(
        self, client: AsyncClient, auth: dict[str, str], mock_http: respx.MockRouter
    ) -> None:
        mock_http.get(TOP_ENDPOINT).respond(200, json=feed(DISTINCT))

        item = (await client.get(TOP_URL, headers=auth)).json()["items"][0]

        assert set(item) == {
            "title",
            "url",
            "published_at",
            "source_name",
            "author",
            "description",
            "url_to_image",
        }
        assert item["title"] == "Chip demand keeps climbing - Financial Times"
        assert item["source_name"] == "Financial Times"

    async def test_the_truncated_vendor_content_is_never_returned(
        self, client: AsyncClient, auth: dict[str, str], mock_http: respx.MockRouter
    ) -> None:
        """It is unreadable, and re-serving a publisher's body text is a licensing question
        this endpoint has no reason to raise."""
        mock_http.get(TOP_ENDPOINT).respond(200, json=feed(DISTINCT))

        response = await client.get(TOP_URL, headers=auth)

        assert "2541 chars" not in response.text
        assert "content" not in response.json()["items"][0]

    async def test_a_category_reaches_the_vendor(
        self, client: AsyncClient, auth: dict[str, str], mock_http: respx.MockRouter
    ) -> None:
        route = mock_http.get(TOP_ENDPOINT).respond(200, json=feed(DISTINCT))

        await client.get(TOP_URL, params={"category": "business"}, headers=auth)

        assert dict(route.calls.last.request.url.params)["category"] == "business"

    async def test_an_empty_feed_is_a_200_with_an_empty_page(
        self, client: AsyncClient, auth: dict[str, str], mock_http: respx.MockRouter
    ) -> None:
        mock_http.get(TOP_ENDPOINT).respond(200, json=feed(total=0))

        response = await client.get(TOP_URL, headers=auth)

        assert response.status_code == 200
        assert response.json() == {
            "items": [],
            "total": 0,
            "limit": DEFAULT_PAGE_LIMIT,
            "offset": 0,
            "has_more": False,
        }

    async def test_the_window_is_echoed_back(
        self, client: AsyncClient, auth: dict[str, str], mock_http: respx.MockRouter
    ) -> None:
        mock_http.get(TOP_ENDPOINT).respond(200, json=feed(*SYNDICATED, DISTINCT))

        body = (await client.get(TOP_URL, params={"limit": 1, "offset": 1}, headers=auth)).json()

        assert body["limit"] == 1
        assert body["offset"] == 1
        assert len(body["items"]) == 1
        assert body["has_more"] is False

    @pytest.mark.parametrize(
        "params",
        [{"limit": 0}, {"limit": MAX_PAGE_LIMIT + 1}, {"offset": -1}, {"limit": "lots"}],
    )
    async def test_a_nonsense_window_is_a_422_at_the_edge(
        self, client: AsyncClient, auth: dict[str, str], params: dict[str, Any]
    ) -> None:
        """``CLAUDE.md`` §4: an HTTP client is never quietly handed a window it did not ask
        for. The service-side clamp is for the callers with no request to reject."""
        response = await client.get(TOP_URL, params=params, headers=auth)

        assert_error_envelope(response, status=422, code="validation_error")

    async def test_a_bad_window_never_reaches_the_vendor(
        self, client: AsyncClient, auth: dict[str, str], mock_http: respx.MockRouter
    ) -> None:
        route = mock_http.get(TOP_ENDPOINT).respond(200, json=feed(DISTINCT))

        await client.get(TOP_URL, params={"limit": 0}, headers=auth)

        assert route.call_count == 0


# ---------------------------------------------------------------------------------------
# GET /v1/news/by-symbol/{ticker}
# ---------------------------------------------------------------------------------------


class TestNewsBySymbol:
    async def test_a_known_ticker_returns_a_page(
        self, client: AsyncClient, auth: dict[str, str], mock_http: respx.MockRouter
    ) -> None:
        mock_http.get(EVERYTHING_ENDPOINT).respond(200, json=feed(DISTINCT))

        response = await client.get(f"{BY_SYMBOL_URL}/AAPL", headers=auth)

        assert response.status_code == 200
        assert response.json()["total"] == 1

    async def test_the_vendor_is_asked_about_the_company_not_the_three_letter_word(
        self, client: AsyncClient, auth: dict[str, str], mock_http: respx.MockRouter
    ) -> None:
        """The real payoff of resolving the ticker first, visible only end to end."""
        route = mock_http.get(EVERYTHING_ENDPOINT).respond(200, json=feed(DISTINCT))

        await client.get(f"{BY_SYMBOL_URL}/AAPL", headers=auth)

        assert dict(route.calls.last.request.url.params)["q"] == '"AAPL" OR "Apple Inc."'

    async def test_a_lower_cased_ticker_resolves_anyway(
        self, client: AsyncClient, auth: dict[str, str], mock_http: respx.MockRouter
    ) -> None:
        """Normalisation happens in the service, not at the edge — and the path parameter is
        a plain unconstrained string, asserted below."""
        mock_http.get(EVERYTHING_ENDPOINT).respond(200, json=feed(DISTINCT))

        response = await client.get(f"{BY_SYMBOL_URL}/aapl", headers=auth)

        assert response.status_code == 200

    async def test_the_path_parameter_is_a_plain_string_in_the_document(self, app: FastAPI) -> None:
        """Nothing is happening at the edge: if the OpenAPI document declared a constrained
        type, the normalisation would be in the layer a Celery task never goes through."""
        parameters = app.openapi()["paths"]["/v1/news/by-symbol/{ticker}"]["get"]["parameters"]
        ticker = next(p for p in parameters if p["name"] == "ticker")

        assert ticker["schema"]["type"] == "string"
        assert set(ticker["schema"]) <= {"type", "title", "description"}

    async def test_an_unknown_ticker_is_a_404_not_an_empty_feed(
        self, client: AsyncClient, auth: dict[str, str], mock_http: respx.MockRouter
    ) -> None:
        """The decision this ticket had to make. NewsAPI answers a nonsense symbol with
        ``{"status": "ok", "totalResults": 0}`` — byte-identical to a real company nobody
        wrote about — so only the stocks table can tell a typo from a quiet week.
        """
        mock_http.get(EVERYTHING_ENDPOINT).respond(200, json=feed(total=0))

        response = await client.get(f"{BY_SYMBOL_URL}/ZZZZ", headers=auth)

        error = assert_error_envelope(response, status=404, code="not_found")
        assert error["details"] == {"resource": "stock", "identifier": "ZZZZ"}

    async def test_an_unknown_ticker_costs_no_vendor_quota(
        self, client: AsyncClient, auth: dict[str, str], mock_http: respx.MockRouter
    ) -> None:
        route = mock_http.get(EVERYTHING_ENDPOINT).respond(200, json=feed(total=0))

        await client.get(f"{BY_SYMBOL_URL}/ZZZZ", headers=auth)

        assert route.call_count == 0

    async def test_the_404_reports_the_canonical_spelling(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        response = await client.get(f"{BY_SYMBOL_URL}/zzzz", headers=auth)

        error = assert_error_envelope(response, status=404, code="not_found")
        assert error["details"]["identifier"] == "ZZZZ"
        assert "'ZZZZ'" in error["message"]

    async def test_a_real_security_with_no_coverage_is_a_200(
        self, client: AsyncClient, auth: dict[str, str], mock_http: respx.MockRouter
    ) -> None:
        """The other half of the rule: a missing parent is a 404, an empty child is a 200."""
        mock_http.get(EVERYTHING_ENDPOINT).respond(200, json=feed(total=0))

        response = await client.get(f"{BY_SYMBOL_URL}/AAPL", headers=auth)

        assert response.status_code == 200
        assert response.json()["items"] == []
        assert response.json()["total"] == 0


# ---------------------------------------------------------------------------------------
# no key configured — the state of every fresh clone
# ---------------------------------------------------------------------------------------


class TestWithNoApiKeyConfigured:
    """``NEWSAPI_API_KEY`` is blank in ``.env.example``. A 502 saying nothing would be
    honest and useless; an empty page would be dishonest. This is the third option: a 502
    that names the problem and the setting, in the body, on the first call.
    """

    @pytest.fixture
    async def unconfigured(self, keyless_app: FastAPI) -> AsyncIterator[AsyncClient]:
        """``conftest``'s ``client``, pointed at the second application.

        Built here rather than by overriding the shared ``client`` fixture, because two of
        the tests below need both applications answering in one test.
        """
        transport = ASGITransport(app=keyless_app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://testserver") as http:
            yield http

    async def test_top_headlines_is_a_502_that_says_what_is_wrong(
        self, unconfigured: AsyncClient, auth: dict[str, str]
    ) -> None:
        response = await unconfigured.get(TOP_URL, headers=auth)

        error = assert_error_envelope(response, status=502, code="external_service_error")
        assert error["details"] == {
            "service": "newsapi",
            "reason": "not_configured",
            "setting": "NEWSAPI_API_KEY",
        }
        assert "not configured" in error["message"]

    async def test_by_symbol_answers_the_same_way(
        self, unconfigured: AsyncClient, auth: dict[str, str]
    ) -> None:
        response = await unconfigured.get(f"{BY_SYMBOL_URL}/AAPL", headers=auth)

        error = assert_error_envelope(response, status=502, code="external_service_error")
        assert error["details"]["reason"] == "not_configured"

    async def test_it_is_distinguishable_from_a_vendor_outage(
        self,
        unconfigured: AsyncClient,
        client: AsyncClient,
        auth: dict[str, str],
        mock_http: respx.MockRouter,
    ) -> None:
        """Both are 502 ``external_service_error``; ``details.reason`` is what an operator
        branches on, and it must not read ``client_error`` for our own missing config."""
        mock_http.get(TOP_ENDPOINT).respond(500, json={"status": "error", "code": "x"})
        outage = await client.get(TOP_URL, headers=auth)
        unconfigured_response = await unconfigured.get(TOP_URL, headers=auth)

        assert outage.json()["error"]["details"]["reason"] == "server_error"
        assert unconfigured_response.json()["error"]["details"]["reason"] == "not_configured"

    async def test_it_never_reaches_the_network(
        self, unconfigured: AsyncClient, auth: dict[str, str], mock_http: respx.MockRouter
    ) -> None:
        route = mock_http.get(TOP_ENDPOINT).respond(200, json=feed(DISTINCT))

        await unconfigured.get(TOP_URL, headers=auth)

        assert route.call_count == 0

    async def test_a_missing_ticker_is_still_a_404_first(
        self, unconfigured: AsyncClient, auth: dict[str, str]
    ) -> None:
        """The resolution happens before the vendor is touched, so the more specific answer
        wins — a caller is told the symbol is wrong rather than that news is unavailable."""
        response = await unconfigured.get(f"{BY_SYMBOL_URL}/ZZZZ", headers=auth)

        assert_error_envelope(response, status=404, code="not_found")


# ---------------------------------------------------------------------------------------
# upstream failures
# ---------------------------------------------------------------------------------------


class TestAnUpstreamFailure:
    @pytest.mark.parametrize(
        ("status", "payload", "reason"),
        [
            (200, {"status": "error", "code": "rateLimited"}, "rate_limited"),
            (200, {"status": "error", "code": "apiKeyInvalid"}, "client_error"),
            (200, {"status": "error", "code": "maximumResultsReached"}, "client_error"),
            (200, {"nothing": "useful"}, "malformed_response"),
            (401, {"status": "error", "code": "apiKeyInvalid"}, "client_error"),
        ],
    )
    async def test_it_becomes_a_502_naming_the_reason(
        self,
        client: AsyncClient,
        auth: dict[str, str],
        mock_http: respx.MockRouter,
        status: int,
        payload: dict[str, Any],
        reason: str,
    ) -> None:
        mock_http.get(TOP_ENDPOINT).respond(status, json=payload)

        response = await client.get(TOP_URL, headers=auth)

        error = assert_error_envelope(response, status=502, code="external_service_error")
        assert error["details"]["reason"] == reason
        assert error["details"]["service"] == "newsapi"

    async def test_the_vendors_body_is_never_forwarded(
        self, client: AsyncClient, auth: dict[str, str], mock_http: respx.MockRouter
    ) -> None:
        """``CLAUDE.md`` §4 makes the error body a public contract; forwarding upstream
        output through it turns an internal detail into an API."""
        mock_http.get(TOP_ENDPOINT).respond(
            200,
            json={
                "status": "error",
                "code": "apiKeyInvalid",
                "message": "Your API key abcdef123456 is invalid.",
            },
        )

        response = await client.get(TOP_URL, headers=auth)

        assert "abcdef123456" not in response.text

    async def test_the_api_key_is_not_in_the_response(
        self, client: AsyncClient, auth: dict[str, str], mock_http: respx.MockRouter
    ) -> None:
        mock_http.get(TOP_ENDPOINT).respond(200, json={"status": "error", "code": "rateLimited"})

        response = await client.get(TOP_URL, headers=auth)

        assert API_KEY not in response.text

    async def test_the_failure_still_carries_a_request_id(
        self, client: AsyncClient, auth: dict[str, str], mock_http: respx.MockRouter
    ) -> None:
        """So a reported 502 maps to one log line — ``CLAUDE.md`` §4."""
        mock_http.get(TOP_ENDPOINT).respond(200, json={"status": "error", "code": "rateLimited"})

        response = await client.get(TOP_URL, headers=auth)

        error = response.json()["error"]
        assert error["request_id"] == response.headers["X-Request-ID"]


# ---------------------------------------------------------------------------------------
# the router's shape
# ---------------------------------------------------------------------------------------


class TestTheRouterShape:
    def test_it_is_mounted_under_the_version_prefix_with_its_own_tag(self, app: FastAPI) -> None:
        document = app.openapi()

        assert "/v1/news/top" in document["paths"]
        assert document["paths"]["/v1/news/top"]["get"]["tags"] == ["news"]

    def test_there_are_exactly_two_routes_and_both_are_reads(self, app: FastAPI) -> None:
        """Read-only on purpose: nothing is stored, so there is nothing to write."""
        news_paths = {
            path: set(operations)
            for path, operations in app.openapi()["paths"].items()
            if path.startswith("/v1/news")
        }

        assert news_paths == {"/v1/news/top": {"get"}, "/v1/news/by-symbol/{ticker}": {"get"}}

    def test_the_502_is_documented_on_both_routes(self, app: FastAPI) -> None:
        """A client has to know ``not_configured`` is a possibility, or it will report a
        deployment mistake to its users as an outage."""
        paths = app.openapi()["paths"]

        for path in ("/v1/news/top", "/v1/news/by-symbol/{ticker}"):
            described = paths[path]["get"]["responses"]["502"]["description"]
            assert "not_configured" in described, path
            assert "NEWSAPI_API_KEY" in described, path

    def test_the_404_is_documented_only_where_it_can_happen(self, app: FastAPI) -> None:
        paths = app.openapi()["paths"]

        assert "404" in paths["/v1/news/by-symbol/{ticker}"]["get"]["responses"]
        assert "404" not in paths["/v1/news/top"]["get"]["responses"]

    def test_no_path_decorator_spells_out_the_version(self) -> None:
        """``CLAUDE.md`` §4: the prefix lives in ``app/api/v1/__init__.py`` and nowhere else."""
        from app.api.v1 import news as news_router

        assert news_router.router.prefix == "/news"
        assert all("/v1" not in route.path for route in news_router.router.routes)  # type: ignore[attr-defined]
