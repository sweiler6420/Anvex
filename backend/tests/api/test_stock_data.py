"""Route contract tests for the candle series under ``/v1/stocks/.../data``.

The API tier (``CLAUDE.md`` §6): real routers, real middleware, real error envelope, **no
database**. ``get_stock_data_service`` and ``get_auth_service`` are redirected at real
services sitting on in-memory repos, so the route, the guard, the service's own branches and
the envelope are all genuinely under test with Docker stopped. Overriding the *factories* and
nothing else is the pattern from ``tests/api/test_stocks.py``.

**This module is the frontend's contract, and that is why it exists.** The charting widgets
were written against the old endpoint's response, and two properties of it are easy to break
by accident and impossible to notice from the backend:

* every point carries a single **`datetime`**, recombined from the stored `date` and `time`
  columns, and it is **naive** — no `Z`, no `+00:00`. ``stock_data.time`` is the exchange's
  local wall clock, so an offset would move a 09:30 New York open by hours. It looks like a
  bug and it is not; ``TestTheDatetimeShape`` says so in an assertion rather than a comment.
* prices are **quoted JSON strings** (``"1234.5678"``), because they are ``Decimal`` and a
  JSON number would be a float and lose the fourth decimal place. The assertions here are on
  the **raw text of the response**, not on a parsed float, because a parsed float is exactly
  the thing that would hide the regression.

The rest is the ordinary read contract: paging, the inclusive date range, 404 for an unknown
security, an empty page for a real security with nothing in range, and auth on both routes.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import SecretStr

from app.deps.auth import get_auth_service
from app.deps.stock_data import get_stock_data_service
from app.domain.auth import ACCESS_TOKEN_TYPE, create_token
from app.schemas.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT
from app.services.auth import AuthService
from app.services.stock_data import StockDataService
from app.settings import Settings
from tests.helpers import (
    FakeStockDataRepo,
    FakeStockRepo,
    FakeUserRepo,
    StubSession,
    assert_error_envelope,
    make_candle,
    make_stock,
)

SECRET = "api-tier-jwt-secret"
ALGORITHM = "HS256"

STOCKS_URL = "/v1/stocks"
BY_TICKER_URL = f"{STOCKS_URL}/by-ticker"

MONDAY = dt.date(2026, 1, 5)
FRIDAY = dt.date(2026, 1, 9)
WEEK = tuple(MONDAY + dt.timedelta(days=offset) for offset in range(5))

OPEN_BELL = dt.time(9, 30)
CLOSING_AUCTION = dt.time(15, 55)

#: Four decimal places and a trailing zero, which is what a float would quietly destroy.
PINNED_CLOSE = "1234.5678"


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
    from tests.helpers import make_user

    return make_user(username="stephen1", email="stephen@example.com")


@pytest.fixture
def apple() -> Any:
    return make_stock(ticker_symbol="AAPL", company="Apple Inc.")


@pytest.fixture
def quiet() -> Any:
    """A real security that has never had a candle ingested — the empty-page case."""
    return make_stock(ticker_symbol="QUIET", company="Quiet Holdings Inc.")


@pytest.fixture
def stocks(apple: Any, quiet: Any) -> FakeStockRepo:
    return FakeStockRepo(apple, quiet)


@pytest.fixture
def candles(apple: Any) -> FakeStockDataRepo:
    """One candle at the opening bell on each of five consecutive trading days, plus a
    second candle later on the Monday so the ``time`` column has something to say."""
    series = [
        make_candle(stock_id=apple.stock_id, date=day, time=OPEN_BELL, close=f"{100 + n}.2500")
        for n, day in enumerate(WEEK)
    ]
    series.append(
        make_candle(
            stock_id=apple.stock_id,
            date=MONDAY,
            time=CLOSING_AUCTION,
            close=PINNED_CLOSE,
            volume=2_048,
        )
    )
    return FakeStockDataRepo(*series)


@pytest.fixture
def app(
    app: FastAPI,
    settings: Settings,
    stocks: FakeStockRepo,
    candles: FakeStockDataRepo,
    account: Any,
) -> FastAPI:
    """The application with the candle service on in-memory repos, and auth likewise.

    Auth is overridden as well because both routes sit behind ``CurrentUser``: without it the
    guard would reach for Postgres and every test in this module would be a database test.
    """
    session = StubSession()
    service = StockDataService(
        session=session,  # type: ignore[arg-type]
        settings=settings,
        stocks=stocks,  # type: ignore[arg-type]
        candles=candles,  # type: ignore[arg-type]
    )
    auth_service = AuthService(session=session, settings=settings, users=FakeUserRepo(account))  # type: ignore[arg-type]
    app.dependency_overrides[get_stock_data_service] = lambda: service
    app.dependency_overrides[get_auth_service] = lambda: auth_service
    return app


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


@pytest.fixture
def series_url(apple: Any) -> str:
    return f"{STOCKS_URL}/{apple.stock_id}/data"


# ---------------------------------------------------------------------------------------
# authentication
# ---------------------------------------------------------------------------------------


class TestAuthenticationIsRequired:
    @pytest.fixture
    def anonymous_urls(self, series_url: str) -> list[str]:
        return [series_url, f"{BY_TICKER_URL}/AAPL/data"]

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
        self, client: AsyncClient, account: Any, series_url: str
    ) -> None:
        refresh = create_token(
            subject=account.user_id,
            token_type="refresh",  # type: ignore[arg-type]
            now=dt.datetime.now(dt.UTC),
            lifetime=dt.timedelta(days=7),
            secret=SECRET,
            algorithm=ALGORITHM,
        )

        response = await client.get(
            series_url, headers={"Authorization": f"Bearer {refresh}"}
        )

        assert_error_envelope(response, status=401, code="wrong_token_type")

    async def test_the_routes_are_not_public_by_accident(self, app: FastAPI) -> None:
        paths = app.openapi()["paths"]

        for path in (
            f"{STOCKS_URL}/{{stock_id}}/data",
            f"{BY_TICKER_URL}/{{ticker}}/data",
        ):
            assert "security" in paths[path]["get"], path


# ---------------------------------------------------------------------------------------
# the shape the charts consume
# ---------------------------------------------------------------------------------------


class TestTheDatetimeShape:
    """The single most breakable thing in this ticket. Asserted on the raw JSON."""

    @pytest.fixture
    async def monday(
        self, client: AsyncClient, auth: dict[str, str], series_url: str
    ) -> dict[str, Any]:
        """The Monday, whose two candles pin both the date and the time halves."""
        response = await client.get(
            series_url, params={"start": "2026-01-05", "end": "2026-01-05"}, headers=auth
        )
        assert response.status_code == 200, response.text
        return response.json()

    async def test_a_point_carries_one_combined_datetime(
        self, monday: dict[str, Any]
    ) -> None:
        """``(date + time)`` from the old SQL, now built by ``StockDataPoint.from_row``."""
        first, second = monday["items"]

        assert first["datetime"] == "2026-01-05T09:30:00"
        assert second["datetime"] == "2026-01-05T15:55:00"

    async def test_the_separate_date_and_time_columns_are_not_exposed(
        self, monday: dict[str, Any]
    ) -> None:
        """One timestamp, not three fields the client has to reassemble."""
        point = monday["items"][0]

        assert "date" not in point
        assert "time" not in point

    async def test_the_datetime_carries_no_offset(self, monday: dict[str, Any]) -> None:
        """**Naive on purpose. Do not "fix" this.** ``stock_data.time`` is the exchange's
        local trading clock and carries no zone, so serialising 09:30 as ``09:30:00Z`` would
        claim the New York open happens at 04:30 Eastern. It is the only datetime in this API
        without an offset, and attaching a real one needs an exchange-to-timezone map that
        does not exist yet."""
        stamp = monday["items"][0]["datetime"]

        assert not stamp.endswith("Z")
        assert "+" not in stamp
        assert dt.datetime.fromisoformat(stamp).tzinfo is None

    async def test_the_point_carries_exactly_the_charting_fields(
        self, monday: dict[str, Any]
    ) -> None:
        assert set(monday["items"][0]) == {
            "stock_id",
            "datetime",
            "open_price",
            "high_price",
            "low_price",
            "close_price",
            "volume",
        }

    async def test_the_openapi_document_declares_the_naive_timestamp(
        self, app: FastAPI
    ) -> None:
        """A generated client is told the shape too, not just the live response."""
        point = app.openapi()["components"]["schemas"]["StockDataPoint"]

        assert point["properties"]["datetime"]["format"] == "date-time"
        assert point["properties"]["datetime"]["examples"] == ["2026-01-05T09:30:00"]


class TestPricesAreQuotedStrings:
    @pytest.fixture
    async def response_text(
        self, client: AsyncClient, auth: dict[str, str], series_url: str
    ) -> str:
        response = await client.get(
            series_url, params={"start": "2026-01-05", "end": "2026-01-05"}, headers=auth
        )
        assert response.status_code == 200, response.text
        return response.text

    async def test_a_price_is_a_json_string_not_a_number(self, response_text: str) -> None:
        """Asserted on the raw body: parsing first is what would hide the regression."""
        assert f'"close_price":"{PINNED_CLOSE}"' in response_text.replace(" ", "")

    async def test_all_four_decimal_places_survive(self, response_text: str) -> None:
        point = json.loads(response_text)["items"][1]

        assert point["close_price"] == PINNED_CLOSE
        assert Decimal(point["close_price"]) == Decimal(PINNED_CLOSE)
        assert point["open_price"] == "1234.0678"
        assert point["high_price"] == "1235.5678"
        assert point["low_price"] == "1233.5678"

    async def test_volume_stays_a_json_number(self, response_text: str) -> None:
        """It is a count, not money — an integer loses nothing and a chart wants a number."""
        point = json.loads(response_text)["items"][1]

        assert point["volume"] == 2_048
        assert isinstance(point["volume"], int)


# ---------------------------------------------------------------------------------------
# the envelope and paging
# ---------------------------------------------------------------------------------------


class TestTheEnvelope:
    async def test_a_series_comes_back_in_the_page_envelope(
        self, client: AsyncClient, auth: dict[str, str], series_url: str
    ) -> None:
        response = await client.get(series_url, headers=auth)

        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"items", "total", "limit", "offset", "has_more"}
        assert body["total"] == 6
        assert body["limit"] == DEFAULT_PAGE_LIMIT
        assert body["offset"] == 0
        assert body["has_more"] is False

    async def test_candles_are_oldest_first(
        self, client: AsyncClient, auth: dict[str, str], series_url: str
    ) -> None:
        response = await client.get(series_url, headers=auth)

        stamps = [point["datetime"] for point in response.json()["items"]]
        assert stamps == sorted(stamps)

    async def test_a_window_reports_the_full_total(
        self, client: AsyncClient, auth: dict[str, str], series_url: str
    ) -> None:
        response = await client.get(series_url, params={"limit": 2}, headers=auth)

        body = response.json()
        assert len(body["items"]) == 2
        assert body["total"] == 6
        assert body["has_more"] is True

    async def test_an_offset_past_the_end_is_empty_with_a_truthful_total(
        self, client: AsyncClient, auth: dict[str, str], series_url: str
    ) -> None:
        response = await client.get(series_url, params={"offset": 100}, headers=auth)

        body = response.json()
        assert body["items"] == []
        assert body["total"] == 6
        assert body["has_more"] is False

    async def test_paging_walks_the_series_without_repeating_a_candle(
        self, client: AsyncClient, auth: dict[str, str], series_url: str
    ) -> None:
        seen: list[str] = []
        for offset in (0, 2, 4):
            response = await client.get(
                series_url, params={"limit": 2, "offset": offset}, headers=auth
            )
            seen.extend(point["datetime"] for point in response.json()["items"])

        assert len(seen) == len(set(seen)) == 6

    async def test_an_over_large_limit_is_refused_rather_than_clamped(
        self, client: AsyncClient, auth: dict[str, str], series_url: str
    ) -> None:
        """``CLAUDE.md`` §4: an HTTP client is never quietly handed a shorter page."""
        response = await client.get(
            series_url, params={"limit": MAX_PAGE_LIMIT + 1}, headers=auth
        )

        assert_error_envelope(response, status=422, code="validation_error")

    @pytest.mark.parametrize("params", [{"limit": 0}, {"limit": -1}, {"offset": -1}])
    async def test_a_nonsense_window_is_a_422(
        self,
        client: AsyncClient,
        auth: dict[str, str],
        series_url: str,
        params: dict[str, int],
    ) -> None:
        response = await client.get(series_url, params=params, headers=auth)

        assert_error_envelope(response, status=422, code="validation_error")


# ---------------------------------------------------------------------------------------
# the date range
# ---------------------------------------------------------------------------------------


class TestTheDateRange:
    async def test_both_bounds_are_inclusive(
        self, client: AsyncClient, auth: dict[str, str], series_url: str
    ) -> None:
        response = await client.get(
            series_url,
            params={"start": "2026-01-06", "end": "2026-01-08"},
            headers=auth,
        )

        body = response.json()
        assert body["total"] == 3
        assert [point["datetime"][:10] for point in body["items"]] == [
            "2026-01-06",
            "2026-01-07",
            "2026-01-08",
        ]

    async def test_a_single_day_returns_that_day(
        self, client: AsyncClient, auth: dict[str, str], series_url: str
    ) -> None:
        response = await client.get(
            series_url,
            params={"start": "2026-01-07", "end": "2026-01-07"},
            headers=auth,
        )

        assert response.json()["total"] == 1

    async def test_an_open_end_means_everything_since(
        self, client: AsyncClient, auth: dict[str, str], series_url: str
    ) -> None:
        response = await client.get(series_url, params={"start": "2026-01-08"}, headers=auth)

        assert response.json()["total"] == 2

    async def test_an_open_start_means_everything_until(
        self, client: AsyncClient, auth: dict[str, str], series_url: str
    ) -> None:
        response = await client.get(series_url, params={"end": "2026-01-06"}, headers=auth)

        assert response.json()["total"] == 3

    async def test_a_range_with_no_candles_is_an_empty_page_not_a_404(
        self, client: AsyncClient, auth: dict[str, str], series_url: str
    ) -> None:
        """A real security that simply did not trade in the window is a 200."""
        response = await client.get(
            series_url,
            params={"start": "2030-01-01", "end": "2030-01-31"},
            headers=auth,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["items"] == []
        assert body["total"] == 0

    async def test_an_inverted_range_is_a_422_not_an_empty_page(
        self, client: AsyncClient, auth: dict[str, str], series_url: str
    ) -> None:
        """The caller has a bug and an empty 200 would hide it."""
        response = await client.get(
            series_url,
            params={"start": "2026-01-09", "end": "2026-01-05"},
            headers=auth,
        )

        error = assert_error_envelope(response, status=422, code="validation_error")
        assert error["details"] == {
            "field": "start",
            "start": "2026-01-09",
            "end": "2026-01-05",
        }

    async def test_an_unparseable_date_is_a_422_at_the_edge(
        self, client: AsyncClient, auth: dict[str, str], series_url: str
    ) -> None:
        """FastAPI's own validation, in the same envelope as the service's."""
        response = await client.get(series_url, params={"start": "yesterday"}, headers=auth)

        assert_error_envelope(response, status=422, code="validation_error")


# ---------------------------------------------------------------------------------------
# resolving the security
# ---------------------------------------------------------------------------------------


class TestByStockId:
    async def test_an_unknown_stock_is_a_404(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        """A sub-collection of a parent that does not exist is not an empty collection."""
        missing = uuid.uuid4()

        response = await client.get(f"{STOCKS_URL}/{missing}/data", headers=auth)

        error = assert_error_envelope(response, status=404, code="not_found")
        assert error["details"] == {"resource": "stock", "identifier": str(missing)}

    async def test_a_known_stock_with_no_candles_is_an_empty_page(
        self, client: AsyncClient, auth: dict[str, str], quiet: Any
    ) -> None:
        response = await client.get(f"{STOCKS_URL}/{quiet.stock_id}/data", headers=auth)

        assert response.status_code == 200
        assert response.json() == {
            "items": [],
            "total": 0,
            "limit": DEFAULT_PAGE_LIMIT,
            "offset": 0,
            "has_more": False,
        }

    async def test_a_malformed_stock_id_is_a_422(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        response = await client.get(f"{STOCKS_URL}/not-a-uuid/data", headers=auth)

        assert_error_envelope(response, status=422, code="validation_error")


class TestByTicker:
    async def test_it_resolves_a_ticker(
        self, client: AsyncClient, auth: dict[str, str], apple: Any
    ) -> None:
        response = await client.get(f"{BY_TICKER_URL}/AAPL/data", headers=auth)

        body = response.json()
        assert response.status_code == 200
        assert body["total"] == 6
        assert {point["stock_id"] for point in body["items"]} == {str(apple.stock_id)}

    @pytest.mark.parametrize("segment", ["aapl", "AaPl", "%20aapl%20"])
    async def test_normalisation_is_the_services_not_the_edges(
        self, client: AsyncClient, auth: dict[str, str], segment: str
    ) -> None:
        """The path parameter is a plain ``str`` (asserted below), so a lower-cased or padded
        URL can only resolve because the service upper-cased it — which is the layer a Celery
        task also goes through."""
        response = await client.get(f"{BY_TICKER_URL}/{segment}/data", headers=auth)

        assert response.status_code == 200, response.text
        assert response.json()["total"] == 6

    async def test_the_document_declares_an_unconstrained_string(
        self, app: FastAPI
    ) -> None:
        """Nothing is happening at the edge: no pattern, no ``BeforeValidator``."""
        operation = app.openapi()["paths"][f"{BY_TICKER_URL}/{{ticker}}/data"]["get"]
        ticker = next(
            parameter for parameter in operation["parameters"] if parameter["name"] == "ticker"
        )

        assert ticker["in"] == "path"
        assert ticker["schema"]["type"] == "string"
        assert "pattern" not in ticker["schema"]
        assert "maxLength" not in ticker["schema"]

    async def test_an_unknown_ticker_is_a_404_naming_the_canonical_spelling(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        response = await client.get(f"{BY_TICKER_URL}/nope/data", headers=auth)

        error = assert_error_envelope(response, status=404, code="not_found")
        assert error["details"] == {"resource": "stock", "identifier": "NOPE"}

    async def test_the_range_and_window_apply_here_too(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        response = await client.get(
            f"{BY_TICKER_URL}/aapl/data",
            params={"start": "2026-01-06", "end": "2026-01-08", "limit": 2},
            headers=auth,
        )

        body = response.json()
        assert body["total"] == 3
        assert body["limit"] == 2
        assert body["has_more"] is True


# ---------------------------------------------------------------------------------------
# routing
# ---------------------------------------------------------------------------------------


class TestRouteOrdering:
    """Two routers share the ``/v1/stocks`` prefix, so the ordering is worth proving."""

    def test_the_literal_route_is_declared_before_the_parameterised_one(
        self, app: FastAPI
    ) -> None:
        paths = list(app.openapi()["paths"])

        assert paths.index(f"{BY_TICKER_URL}/{{ticker}}/data") < paths.index(
            f"{STOCKS_URL}/{{stock_id}}/data"
        )

    async def test_neither_candle_route_can_shadow_the_other(
        self, client: AsyncClient, auth: dict[str, str], apple: Any
    ) -> None:
        """They differ in segment count, so ``{stock_id}`` — a single-segment pattern —
        cannot swallow ``by-ticker/AAPL``. Both answer, and they answer the same series."""
        by_id = await client.get(f"{STOCKS_URL}/{apple.stock_id}/data", headers=auth)
        by_ticker = await client.get(f"{BY_TICKER_URL}/AAPL/data", headers=auth)

        assert by_id.status_code == by_ticker.status_code == 200
        assert by_id.json() == by_ticker.json()

    def test_ANV_13s_routes_are_declared_before_this_router(self, app: FastAPI) -> None:
        """Mounting a second router on the same prefix did not displace the securities
        routes, and they still come first — which is what decides the one URL both routers
        could claim, ``/v1/stocks/by-ticker/data`` (a security whose ticker is ``DATA``)."""
        paths = list(app.openapi()["paths"])

        assert paths.index(f"{BY_TICKER_URL}/{{ticker}}") < paths.index(
            f"{BY_TICKER_URL}/{{ticker}}/data"
        )
        assert paths.index(f"{STOCKS_URL}/{{stock_id}}") < paths.index(
            f"{STOCKS_URL}/{{stock_id}}/data"
        )


class TestRouterWiring:
    def test_both_routes_are_mounted_under_the_version_prefix(self, app: FastAPI) -> None:
        paths = app.openapi()["paths"]

        assert set(paths[f"{STOCKS_URL}/{{stock_id}}/data"]) == {"get"}
        assert set(paths[f"{BY_TICKER_URL}/{{ticker}}/data"]) == {"get"}

    def test_the_resource_is_read_only(self, app: FastAPI) -> None:
        """ANV-14's scope boundary: writing a candle is ANV-22's ingest, through the
        service, not a hand-written POST."""
        paths = app.openapi()["paths"]
        candle_paths = {
            path: set(operations)
            for path, operations in paths.items()
            if path.endswith("/data")
        }

        assert len(candle_paths) == 2
        for path, operations in candle_paths.items():
            assert operations == {"get"}, path

    def test_the_routes_document_their_query_parameters(self, app: FastAPI) -> None:
        for path, parent in (
            (f"{STOCKS_URL}/{{stock_id}}/data", "stock_id"),
            (f"{BY_TICKER_URL}/{{ticker}}/data", "ticker"),
        ):
            operation = app.openapi()["paths"][path]["get"]
            names = {parameter["name"] for parameter in operation["parameters"]}

            assert names == {parent, "start", "end", "limit", "offset"}

    def test_the_routes_return_a_page_of_points(self, app: FastAPI) -> None:
        document = app.openapi()
        content = document["paths"][f"{STOCKS_URL}/{{stock_id}}/data"]["get"]["responses"][
            "200"
        ]["content"]
        ref = content["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]

        assert set(document["components"]["schemas"][ref]["properties"]) == {
            "items",
            "total",
            "limit",
            "offset",
            "has_more",
        }
        assert "StockDataPoint" in ref

    def test_the_candle_routes_have_their_own_tag(self, app: FastAPI) -> None:
        """A separate service and router, even though it shares the ``/stocks`` prefix."""
        operation = app.openapi()["paths"][f"{STOCKS_URL}/{{stock_id}}/data"]["get"]

        assert operation["tags"] == ["stock data"]
