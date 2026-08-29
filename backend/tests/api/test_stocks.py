"""Route contract tests for ``/v1/stocks``.

The API tier (``CLAUDE.md`` §6): real routers, real middleware, real error envelope, **no
database**. ``get_stock_service`` and ``get_auth_service`` are redirected at real services
sitting on in-memory repos (``tests.helpers.FakeStockRepo``, ``FakeUserRepo``), so the
route, the guard, the service's own branches and the error envelope are all genuinely under
test with Docker stopped. Overriding the *factories* and nothing else is the pattern from
``tests/api/test_users.py``; ``app/deps/`` exposes one seam per resource for exactly this.

Three things this module exists to pin.

**All three routes are guarded.** The router this replaces required a token on its single
route, and reference data is no reason to drop the requirement — so there is an anonymous
401 case for each.

**Ticker normalisation is not happening at the edge.** The path parameter is declared as a
plain ``str``: the OpenAPI document is asserted to say so, and ``/by-ticker/aapl`` is
asserted to resolve anyway. Together those two mean the upper-casing can only be the
service's, which is where a Celery task can also reach it. (ANV-8's annotated ``Ticker``
*would* apply its ``BeforeValidator`` to a path parameter — that was checked, not assumed —
and is deliberately not used here.)

**The route ordering, and what it is actually worth.** ``/by-ticker/{ticker}`` is declared
before ``/{stock_id}`` per ``CLAUDE.md`` §4, and :class:`TestRouteOrdering` proves both
halves of the truth: a *two-segment* literal route cannot be shadowed by a single
parameterised segment whichever order they are declared in, while a *one-segment* literal
route absolutely can — which is the ``/users/me`` trap ANV-12 hit, reproduced here against
a control application so the convention is justified rather than merely obeyed.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.deps.auth import get_auth_service
from app.deps.stock import get_stock_service
from app.domain.auth import ACCESS_TOKEN_TYPE, create_token
from app.schemas.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT
from app.services.auth import AuthService
from app.services.stock import StockService
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

STOCKS_URL = "/v1/stocks"
BY_TICKER_URL = f"{STOCKS_URL}/by-ticker"

CATALOGUE = (
    ("AAPL", "Apple Inc.", "NASDAQ"),
    ("MSFT", "Microsoft Corporation", "NASDAQ"),
    ("NVDA", "NVIDIA Corporation", "NASDAQ"),
    ("TSLA", "Tesla, Inc.", "NASDAQ"),
    ("XOM", "Exxon Mobil Corporation", "NYSE"),
)


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """Pin the JWT configuration so the tokens minted below verify against the app."""
    return settings.model_copy(
        update={
            "jwt_secret_key": SecretStr(SECRET),
            "jwt_algorithm": ALGORITHM,
            "jwt_access_token_expire_minutes": 30,
            "jwt_refresh_token_expire_minutes": 60 * 24 * 7,
        }
    )


@pytest.fixture
def account() -> Any:
    return make_user(username="stephen1", email="stephen@example.com")


@pytest.fixture
def stocks() -> FakeStockRepo:
    return FakeStockRepo(
        *(
            make_stock(ticker_symbol=ticker, company=company, market=market)
            for ticker, company, market in CATALOGUE
        )
    )


@pytest.fixture
def apple(stocks: FakeStockRepo) -> Any:
    return stocks.stocks[0]


@pytest.fixture
def app(
    app: FastAPI, settings: Settings, stocks: FakeStockRepo, account: Any
) -> FastAPI:
    """The application with the stock service on an in-memory repo and auth likewise.

    Auth is overridden as well because every route here sits behind ``CurrentUser``:
    without it the guard would reach for Postgres and every test in this module would be a
    database test.
    """
    session = StubSession()
    stock_service = StockService(session=session, settings=settings, stocks=stocks)  # type: ignore[arg-type]
    auth_service = AuthService(session=session, settings=settings, users=FakeUserRepo(account))  # type: ignore[arg-type]
    app.dependency_overrides[get_stock_service] = lambda: stock_service
    app.dependency_overrides[get_auth_service] = lambda: auth_service
    return app


@pytest.fixture
def auth(account: Any) -> dict[str, str]:
    token = create_token(
        subject=account.user_id,
        token_type=ACCESS_TOKEN_TYPE,  # type: ignore[arg-type]
        now=datetime.now(UTC),
        lifetime=timedelta(minutes=30),
        secret=SECRET,
        algorithm=ALGORITHM,
    )
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------------------
# Authentication — every route, no exceptions
# ---------------------------------------------------------------------------------------


class TestAuthenticationIsRequired:
    @pytest.fixture
    def anonymous_urls(self, apple: Any) -> list[str]:
        return [STOCKS_URL, f"{STOCKS_URL}/{apple.stock_id}", f"{BY_TICKER_URL}/AAPL"]

    async def test_every_route_refuses_an_anonymous_caller(
        self, client: AsyncClient, anonymous_urls: list[str]
    ) -> None:
        for url in anonymous_urls:
            response = await client.get(url)

            assert_error_envelope(response, status=401, code="unauthorized")
            assert response.headers.get("www-authenticate") == "Bearer", url

    async def test_every_route_refuses_a_garbage_token(
        self, client: AsyncClient, anonymous_urls: list[str]
    ) -> None:
        headers = {"Authorization": "Bearer nonsense"}

        for url in anonymous_urls:
            response = await client.get(url, headers=headers)

            assert_error_envelope(response, status=401, code="invalid_token")

    async def test_a_refresh_token_is_not_an_access_token(
        self, client: AsyncClient, account: Any
    ) -> None:
        refresh = create_token(
            subject=account.user_id,
            token_type="refresh",  # type: ignore[arg-type]
            now=datetime.now(UTC),
            lifetime=timedelta(days=7),
            secret=SECRET,
            algorithm=ALGORITHM,
        )

        response = await client.get(
            STOCKS_URL, headers={"Authorization": f"Bearer {refresh}"}
        )

        assert_error_envelope(response, status=401, code="wrong_token_type")

    async def test_the_routes_are_not_public_by_accident(self, app: FastAPI) -> None:
        """Declared in the document too, so a generated client knows to send a token."""
        paths = app.openapi()["paths"]

        for path in (
            STOCKS_URL,
            f"{STOCKS_URL}/{{stock_id}}",
            f"{BY_TICKER_URL}/{{ticker}}",
        ):
            assert "security" in paths[path]["get"], path


# ---------------------------------------------------------------------------------------
# GET /v1/stocks
# ---------------------------------------------------------------------------------------


class TestListStocks:
    async def test_a_list_is_a_page_envelope_not_a_bare_array(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        response = await client.get(STOCKS_URL, headers=auth)

        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"items", "total", "limit", "offset", "has_more"}
        assert isinstance(body["items"], list)

    async def test_the_default_window_is_reported_back(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        response = await client.get(STOCKS_URL, headers=auth)

        body = response.json()
        assert (body["limit"], body["offset"]) == (DEFAULT_PAGE_LIMIT, 0)
        assert body["total"] == len(CATALOGUE)
        assert body["has_more"] is False

    async def test_items_are_the_public_stock_shape_ordered_by_ticker(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        response = await client.get(STOCKS_URL, headers=auth)

        items = response.json()["items"]
        assert [item["ticker_symbol"] for item in items] == sorted(
            ticker for ticker, _, _ in CATALOGUE
        )
        assert set(items[0]) == {"stock_id", "ticker_symbol", "company", "market", "isin"}

    async def test_limit_and_offset_move_the_window(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        response = await client.get(
            STOCKS_URL, params={"limit": 2, "offset": 1}, headers=auth
        )

        body = response.json()
        assert [item["ticker_symbol"] for item in body["items"]] == ["MSFT", "NVDA"]
        assert (body["limit"], body["offset"], body["total"]) == (2, 1, len(CATALOGUE))
        assert body["has_more"] is True

    async def test_an_offset_past_the_end_keeps_the_total_truthful(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        response = await client.get(STOCKS_URL, params={"offset": 500}, headers=auth)

        body = response.json()
        assert body["items"] == []
        assert body["total"] == len(CATALOGUE)
        assert body["has_more"] is False

    async def test_search_filters_on_the_ticker(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        response = await client.get(STOCKS_URL, params={"search": "nvda"}, headers=auth)

        body = response.json()
        assert [item["ticker_symbol"] for item in body["items"]] == ["NVDA"]
        assert body["total"] == 1

    async def test_search_filters_on_the_company_name_too(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        response = await client.get(STOCKS_URL, params={"search": "Exxon"}, headers=auth)

        assert [item["ticker_symbol"] for item in response.json()["items"]] == ["XOM"]

    async def test_a_search_matching_nothing_is_an_empty_page_not_a_404(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        response = await client.get(STOCKS_URL, params={"search": "zzzz"}, headers=auth)

        assert response.status_code == 200
        assert response.json() == {
            "items": [],
            "total": 0,
            "limit": DEFAULT_PAGE_LIMIT,
            "offset": 0,
            "has_more": False,
        }

    async def test_an_empty_search_means_no_filter(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        """Not "contains the empty string" — the defect in the old ``/v1/stock_data``."""
        response = await client.get(STOCKS_URL, params={"search": ""}, headers=auth)

        assert response.json()["total"] == len(CATALOGUE)

    @pytest.mark.parametrize(
        "params",
        [
            {"limit": MAX_PAGE_LIMIT + 1},
            {"limit": 0},
            {"limit": -1},
            {"offset": -1},
            {"limit": "lots"},
        ],
    )
    async def test_an_out_of_range_window_is_a_422(
        self, client: AsyncClient, auth: dict[str, str], params: dict[str, Any]
    ) -> None:
        """Refused at the edge rather than quietly clamped, so a caller never believes it
        received a complete list (``app/schemas/pagination.py``)."""
        response = await client.get(STOCKS_URL, params=params, headers=auth)

        assert_error_envelope(response, status=422, code="validation_error")

    async def test_the_ceiling_itself_is_allowed(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        response = await client.get(
            STOCKS_URL, params={"limit": MAX_PAGE_LIMIT}, headers=auth
        )

        assert response.status_code == 200
        assert response.json()["limit"] == MAX_PAGE_LIMIT


# ---------------------------------------------------------------------------------------
# GET /v1/stocks/{stock_id}
# ---------------------------------------------------------------------------------------


class TestReadStockById:
    async def test_a_known_id_is_a_200(
        self, client: AsyncClient, auth: dict[str, str], apple: Any
    ) -> None:
        response = await client.get(f"{STOCKS_URL}/{apple.stock_id}", headers=auth)

        assert response.status_code == 200
        body = response.json()
        assert body["stock_id"] == str(apple.stock_id)
        assert body["ticker_symbol"] == "AAPL"

    async def test_an_unknown_id_is_a_404_in_the_standard_envelope(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        missing = uuid.uuid4()

        response = await client.get(f"{STOCKS_URL}/{missing}", headers=auth)

        error = assert_error_envelope(response, status=404, code="not_found")
        assert error["details"] == {"resource": "stock", "identifier": str(missing)}

    async def test_a_non_uuid_id_is_a_422(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        response = await client.get(f"{STOCKS_URL}/not-a-uuid", headers=auth)

        assert_error_envelope(response, status=422, code="validation_error")

    async def test_the_route_matches_at_all(
        self, client: AsyncClient, auth: dict[str, str], apple: Any
    ) -> None:
        """The router this replaces declared ``@router.get('{id}')`` — no leading slash —
        so it mounted as ``/v1/stocks{id}`` and no request ever reached it."""
        response = await client.get(f"{STOCKS_URL}/{apple.stock_id}", headers=auth)

        assert response.status_code == 200

    def test_the_path_parameter_is_named_stock_id(self, app: FastAPI) -> None:
        operation = app.openapi()["paths"][f"{STOCKS_URL}/{{stock_id}}"]["get"]
        names = [parameter["name"] for parameter in operation["parameters"]]

        assert names == ["stock_id"]


# ---------------------------------------------------------------------------------------
# GET /v1/stocks/by-ticker/{ticker}
# ---------------------------------------------------------------------------------------


class TestReadStockByTicker:
    async def test_the_canonical_ticker_is_a_200(
        self, client: AsyncClient, auth: dict[str, str], apple: Any
    ) -> None:
        response = await client.get(f"{BY_TICKER_URL}/AAPL", headers=auth)

        assert response.status_code == 200
        assert response.json()["stock_id"] == str(apple.stock_id)

    @pytest.mark.parametrize("given", ["aapl", "AaPl", "aApL"])
    async def test_any_casing_resolves_to_the_same_security(
        self, client: AsyncClient, auth: dict[str, str], apple: Any, given: str
    ) -> None:
        response = await client.get(f"{BY_TICKER_URL}/{given}", headers=auth)

        assert response.status_code == 200
        assert response.json()["stock_id"] == str(apple.stock_id)

    async def test_a_padded_ticker_resolves_too(
        self, client: AsyncClient, auth: dict[str, str], apple: Any
    ) -> None:
        """``%20aapl%20`` — whitespace survives URL decoding into the path parameter."""
        response = await client.get(f"{BY_TICKER_URL}/%20aapl%20", headers=auth)

        assert response.status_code == 200
        assert response.json()["stock_id"] == str(apple.stock_id)

    async def test_an_unknown_ticker_is_a_404_naming_the_canonical_spelling(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        response = await client.get(f"{BY_TICKER_URL}/zzzz", headers=auth)

        error = assert_error_envelope(response, status=404, code="not_found")
        assert error["details"] == {"resource": "stock", "identifier": "ZZZZ"}

    def test_the_path_parameter_is_a_plain_string_at_the_edge(self, app: FastAPI) -> None:
        """The other half of the normalisation proof.

        The document declares an unconstrained string, so nothing at the edge is folding
        case — and ``aapl`` still resolves above. The upper-casing can therefore only be
        ``StockService``'s, which is what a Celery task holding a vendor symbol also goes
        through. ANV-8's annotated ``Ticker`` type *would* apply its ``BeforeValidator``
        here if it were used; it is not, on purpose.
        """
        operation = app.openapi()["paths"][f"{BY_TICKER_URL}/{{ticker}}"]["get"]
        parameter = next(p for p in operation["parameters"] if p["name"] == "ticker")

        assert parameter["in"] == "path"
        assert parameter["schema"]["type"] == "string"
        assert "maxLength" not in parameter["schema"]
        assert "minLength" not in parameter["schema"]


# ---------------------------------------------------------------------------------------
# Route ordering
# ---------------------------------------------------------------------------------------


class TestRouteOrdering:
    """``CLAUDE.md`` §4: a literal segment is declared before a parameterised one.

    ANV-12 hit the real version of this — ``/users/me`` below ``/users/{user_id}`` turns
    every ``/me`` request into a failed attempt to parse ``"me"`` as a UUID. The tests here
    pin the ordering *and* establish honestly how much it is worth in this router, because
    a comment claiming a fix that is not load-bearing is worse than no comment.
    """

    def test_the_literal_route_is_declared_before_the_parameterised_one(
        self, app: FastAPI
    ) -> None:
        """Read off the OpenAPI document, whose ``paths`` are emitted by walking the route
        table in declaration order — a public surface, unlike the private structure
        ``app.routes`` uses to hold an included router."""
        paths = list(app.openapi()["paths"])

        assert paths.index(f"{BY_TICKER_URL}/{{ticker}}") < paths.index(
            f"{STOCKS_URL}/{{stock_id}}"
        )

    def test_the_document_order_really_is_declaration_order(self, app: FastAPI) -> None:
        """Guards the test above from being vacuous: ANV-12's ``/users/me`` — the ordering
        that genuinely has to hold — reads the same way out of the same document."""
        paths = list(app.openapi()["paths"])

        assert paths.index("/v1/users/me") < paths.index("/v1/users/{user_id}")

    async def test_by_ticker_is_not_parsed_as_a_stock_id(
        self, client: AsyncClient, auth: dict[str, str], apple: Any
    ) -> None:
        """The behaviour the ordering is there to protect: the literal route answers."""
        response = await client.get(f"{BY_TICKER_URL}/AAPL", headers=auth)

        assert response.status_code == 200
        assert response.json()["ticker_symbol"] == "AAPL"

    async def test_the_bare_prefix_is_a_stock_id_lookup_and_says_so(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        """Where the shadow actually lands: ``/v1/stocks/by-ticker`` is one segment, so it
        matches ``/{stock_id}`` and fails UUID parsing. A 422 rather than a 404 is the
        honest answer — the request named no ticker at all."""
        response = await client.get(BY_TICKER_URL, headers=auth)

        assert_error_envelope(response, status=422, code="validation_error")

    async def test_a_two_segment_literal_route_cannot_be_shadowed(self) -> None:
        """The half of the story a bare "order matters" comment would get wrong.

        Starlette's default path converter never matches a ``/``, so ``/{stock_id}``
        compiles to a single-segment pattern and *cannot* swallow ``/by-ticker/AAPL``
        whichever way round the two are declared. Proven against a control application
        with the declarations deliberately reversed.
        """
        reversed_order = APIRouter()

        @reversed_order.get("/stocks/{stock_id}")
        async def by_id(stock_id: uuid.UUID) -> dict[str, str]:
            return {"route": "by_id"}

        @reversed_order.get("/stocks/by-ticker/{ticker}")
        async def by_ticker(ticker: str) -> dict[str, str]:
            return {"route": "by_ticker"}

        control = FastAPI()
        control.include_router(reversed_order)

        transport = ASGITransport(app=control, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://testserver") as probe:
            response = await probe.get("/stocks/by-ticker/AAPL")

        assert response.status_code == 200
        assert response.json() == {"route": "by_ticker"}

    async def test_a_one_segment_literal_route_is_shadowed_when_declared_second(
        self,
    ) -> None:
        """And this is why the convention is kept anyway.

        The same control application with a *single-segment* literal — the ``/users/me``
        shape — is broken by the reversed order and correct when the literal comes first.
        Declaring the literal first costs nothing and removes the need for whoever adds
        ``/v1/stocks/popular`` next to rediscover the difference.
        """

        def build(literal_first: bool) -> FastAPI:
            router = APIRouter()

            async def popular() -> dict[str, str]:
                return {"route": "popular"}

            async def by_id(stock_id: uuid.UUID) -> dict[str, str]:
                return {"route": "by_id"}

            if literal_first:
                router.get("/stocks/popular")(popular)
            router.get("/stocks/{stock_id}")(by_id)
            if not literal_first:
                router.get("/stocks/popular")(popular)

            control = FastAPI()
            control.include_router(router)
            return control

        async def fetch(control: FastAPI) -> Any:
            transport = ASGITransport(app=control, raise_app_exceptions=False)
            async with AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as probe:
                return await probe.get("/stocks/popular")

        correct = await fetch(build(literal_first=True))
        broken = await fetch(build(literal_first=False))

        assert correct.status_code == 200
        assert correct.json() == {"route": "popular"}
        assert broken.status_code == 422


# ---------------------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------------------


class TestRouterWiring:
    def test_the_three_routes_are_mounted_under_the_version_prefix(
        self, app: FastAPI
    ) -> None:
        paths = app.openapi()["paths"]

        assert set(paths[STOCKS_URL]) == {"get"}
        assert set(paths[f"{STOCKS_URL}/{{stock_id}}"]) == {"get"}
        assert set(paths[f"{BY_TICKER_URL}/{{ticker}}"]) == {"get"}

    def test_the_resource_is_read_only(self, app: FastAPI) -> None:
        """ANV-13's scope boundary, asserted rather than remembered: creating a security is
        ANV-22's ingest, through the service, not a hand-written POST."""
        paths = app.openapi()["paths"]
        stock_paths = {
            path: set(operations)
            for path, operations in paths.items()
            if path.startswith(STOCKS_URL)
        }

        assert stock_paths
        for path, operations in stock_paths.items():
            assert operations == {"get"}, path

    def test_the_list_route_documents_its_query_parameters(self, app: FastAPI) -> None:
        operation = app.openapi()["paths"][STOCKS_URL]["get"]
        names = {parameter["name"] for parameter in operation["parameters"]}

        assert names == {"search", "limit", "offset"}

    def test_the_list_route_returns_a_page_of_stocks(self, app: FastAPI) -> None:
        document = app.openapi()
        content = document["paths"][STOCKS_URL]["get"]["responses"]["200"]["content"]
        ref = content["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]

        assert set(document["components"]["schemas"][ref]["properties"]) == {
            "items",
            "total",
            "limit",
            "offset",
            "has_more",
        }

    def test_the_service_factory_wires_the_request_session_and_the_shared_repo(
        self, settings: Settings
    ) -> None:
        """The real ``get_stock_service`` — the one every test above overrides away."""
        from app.deps.stock import get_stock_service as factory
        from app.repos.stock import stock_repo

        stub = StubSession()

        service = factory(stub, settings)  # type: ignore[arg-type]

        assert isinstance(service, StockService)
        assert service.session is stub
        assert service.settings is settings
        assert service.stocks is stock_repo
