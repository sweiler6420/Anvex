"""Route contract tests for ``/v1/politicians``.

The API tier (``CLAUDE.md`` §6): real routers, real middleware, real error envelope, **no
database**. ``get_politician_service`` and ``get_auth_service`` are redirected at real
services sitting on in-memory repos (``tests.helpers.FakePoliticianRepo``, ``FakeUserRepo``),
so the route, the guard, the service's own branches and the error envelope are all genuinely
under test with Docker stopped. Overriding the *factories* and nothing else is the pattern
from ``tests/api/test_stocks.py``; ``app/deps/`` exposes one seam per resource for exactly
this.

Three things this module exists to pin.

**Both routes are guarded.** Reference data is no reason to drop the token requirement — the
same argument stocks make — so there is an anonymous 401 case for each, and the OpenAPI
document is asserted to declare the requirement so a generated client knows to send one.

**Filter normalisation is not happening at the edge.** The three query parameters are
declared as plain strings — the document is asserted to say so — and ``?state=tx`` is
asserted to find Texans anyway. Together those mean the upper-casing can only be the
service's, which is where the seed script and a future Celery task reach it too.

**The 404 is the plain kind.** There is no owner, so there is no 403-shaped alternative and
no cross-account body to compare against; what matters is that the envelope is the standard
one and ``details.resource`` is the noun a client branches on.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import SecretStr

from app.deps.auth import get_auth_service
from app.deps.politician import get_politician_service
from app.domain.auth import ACCESS_TOKEN_TYPE, create_token
from app.schemas.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT
from app.services.auth import AuthService
from app.services.politician import PoliticianService
from app.settings import Settings
from tests.helpers import (
    FakePoliticianRepo,
    FakeUserRepo,
    StubSession,
    assert_error_envelope,
    make_politician,
    make_user,
)

SECRET = "api-tier-jwt-secret"
ALGORITHM = "HS256"

POLITICIANS_URL = "/v1/politicians"

# (id, last name, state, chamber, party)
ROSTER = (
    ("TX-SEN-R", "Ashgrove", "TX", "Senate", "Republican"),
    ("TX-HOU-R", "Blackwater", "TX", "House", "Republican"),
    ("TX-HOU-D", "Caldermill", "TX", "House", "Democrat"),
    ("CA-SEN-D", "Danforth", "CA", "Senate", "Democrat"),
    ("CA-HOU-D", "Ellsworth", "CA", "House", "Democrat"),
    ("VT-SEN-I", "Gainsborough", "VT", "Senate", "Independent"),
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
def politicians() -> FakePoliticianRepo:
    return FakePoliticianRepo(
        *(
            make_politician(
                politician_id=identifier,
                last_name=last_name,
                state=state,
                chamber=chamber,
                party=party,
            )
            for identifier, last_name, state, chamber, party in ROSTER
        )
    )


@pytest.fixture
def app(app: FastAPI, settings: Settings, politicians: FakePoliticianRepo, account: Any) -> FastAPI:
    """The application with the roster service on an in-memory repo and auth likewise.

    Auth is overridden as well because both routes sit behind ``CurrentUser``: without it the
    guard would reach for Postgres and every test in this module would be a database test.
    """
    session = StubSession()
    politician_service = PoliticianService(  # type: ignore[arg-type]
        session=session, settings=settings, politicians=politicians
    )
    auth_service = AuthService(session=session, settings=settings, users=FakeUserRepo(account))  # type: ignore[arg-type]
    app.dependency_overrides[get_politician_service] = lambda: politician_service
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
# Authentication — both routes, no exceptions
# ---------------------------------------------------------------------------------------


class TestAuthenticationIsRequired:
    @pytest.fixture
    def anonymous_urls(self) -> list[str]:
        return [POLITICIANS_URL, f"{POLITICIANS_URL}/TX-SEN-R"]

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

        response = await client.get(POLITICIANS_URL, headers={"Authorization": f"Bearer {refresh}"})

        assert_error_envelope(response, status=401, code="wrong_token_type")

    async def test_an_expired_token_says_so(self, client: AsyncClient, account: Any) -> None:
        expired = create_token(
            subject=account.user_id,
            token_type=ACCESS_TOKEN_TYPE,  # type: ignore[arg-type]
            now=datetime.now(UTC) - timedelta(hours=2),
            lifetime=timedelta(minutes=30),
            secret=SECRET,
            algorithm=ALGORITHM,
        )

        response = await client.get(POLITICIANS_URL, headers={"Authorization": f"Bearer {expired}"})

        assert_error_envelope(response, status=401, code="token_expired")

    async def test_the_routes_are_not_public_by_accident(self, app: FastAPI) -> None:
        """Declared in the document too, so a generated client knows to send a token."""
        paths = app.openapi()["paths"]

        for path in (POLITICIANS_URL, f"{POLITICIANS_URL}/{{politician_id}}"):
            assert "security" in paths[path]["get"], path


# ---------------------------------------------------------------------------------------
# GET /v1/politicians
# ---------------------------------------------------------------------------------------


class TestListPoliticians:
    async def test_a_list_is_a_page_envelope_not_a_bare_array(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        response = await client.get(POLITICIANS_URL, headers=auth)

        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"items", "total", "limit", "offset", "has_more"}
        assert isinstance(body["items"], list)

    async def test_the_default_window_is_reported_back(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        body = (await client.get(POLITICIANS_URL, headers=auth)).json()

        assert (body["limit"], body["offset"]) == (DEFAULT_PAGE_LIMIT, 0)
        assert body["total"] == len(ROSTER)
        assert body["has_more"] is False

    async def test_items_are_the_public_shape_ordered_by_surname(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        items = (await client.get(POLITICIANS_URL, headers=auth)).json()["items"]

        assert [item["last_name"] for item in items] == sorted(
            last_name for _, last_name, _, _, _ in ROSTER
        )
        assert set(items[0]) == {
            "politician_id",
            "first_name",
            "last_name",
            "party",
            "state",
            "chamber",
            "dob",
            "gender",
        }

    async def test_a_date_of_birth_serialises_as_an_iso_date(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        items = (await client.get(POLITICIANS_URL, headers=auth)).json()["items"]

        assert items[0]["dob"] == "1960-05-04"

    async def test_the_nullable_columns_come_back_as_null_not_absent(
        self, client: AsyncClient, auth: dict[str, str], politicians: FakePoliticianRepo
    ) -> None:
        """A client indexes them unconditionally, exactly as it does ``details``."""
        politicians.add(
            make_politician(
                politician_id="XX-000",
                last_name="Zzyzx",
                state=None,
                chamber=None,
                dob=None,
                gender=None,
            )
        )

        response = await client.get(POLITICIANS_URL, params={"offset": len(ROSTER)}, headers=auth)

        item = response.json()["items"][0]
        assert item["politician_id"] == "XX-000"
        assert (item["state"], item["chamber"], item["dob"], item["gender"]) == (
            None,
            None,
            None,
            None,
        )

    @pytest.mark.parametrize(
        ("params", "expected"),
        [
            ({"state": "TX"}, {"TX-SEN-R", "TX-HOU-R", "TX-HOU-D"}),
            ({"party": "Independent"}, {"VT-SEN-I"}),
            ({"chamber": "House"}, {"TX-HOU-R", "TX-HOU-D", "CA-HOU-D"}),
            ({"state": "TX", "party": "Republican"}, {"TX-SEN-R", "TX-HOU-R"}),
            ({"state": "TX", "party": "Republican", "chamber": "Senate"}, {"TX-SEN-R"}),
        ],
        ids=["state", "party", "chamber", "two", "all three"],
    )
    async def test_the_filters_compose(
        self, client: AsyncClient, auth: dict[str, str], params: dict[str, str], expected: set[str]
    ) -> None:
        body = (await client.get(POLITICIANS_URL, params=params, headers=auth)).json()

        assert {item["politician_id"] for item in body["items"]} == expected
        assert body["total"] == len(expected)

    @pytest.mark.parametrize(
        "params",
        [
            {"state": "tx"},
            {"state": " Tx "},
            {"state": "tx", "chamber": "senate"},
            {"state": "TX", "party": "republican", "chamber": "SENATE"},
        ],
    )
    async def test_a_lower_cased_filter_resolves_anyway(
        self, client: AsyncClient, auth: dict[str, str], params: dict[str, str]
    ) -> None:
        """Which, with the document below, means the normalisation can only be the service's."""
        body = (await client.get(POLITICIANS_URL, params=params, headers=auth)).json()

        assert body["total"] >= 1
        assert all(item["state"] == "TX" for item in body["items"])

    async def test_the_filters_are_plain_strings_in_the_document(self, app: FastAPI) -> None:
        """Nothing is happening at the edge — no annotated type, no ``BeforeValidator``."""
        parameters = app.openapi()["paths"][POLITICIANS_URL]["get"]["parameters"]
        declared = {parameter["name"]: parameter["schema"] for parameter in parameters}

        for name in ("state", "party", "chamber"):
            schema = declared[name]
            types = schema.get("anyOf", [schema])
            assert {"type": "string"} in types, (name, schema)

    async def test_an_unknown_filter_value_is_an_empty_page_not_a_422(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        """The party column is free text; Anvex does not own the vocabulary."""
        response = await client.get(POLITICIANS_URL, params={"party": "Whig"}, headers=auth)

        assert response.status_code == 200
        assert response.json() == {
            "items": [],
            "total": 0,
            "limit": DEFAULT_PAGE_LIMIT,
            "offset": 0,
            "has_more": False,
        }

    async def test_limit_and_offset_move_the_window(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        body = (
            await client.get(POLITICIANS_URL, params={"limit": 2, "offset": 1}, headers=auth)
        ).json()

        assert [item["last_name"] for item in body["items"]] == ["Blackwater", "Caldermill"]
        assert (body["limit"], body["offset"], body["total"]) == (2, 1, len(ROSTER))
        assert body["has_more"] is True

    async def test_an_offset_past_the_end_keeps_the_total_truthful(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        body = (await client.get(POLITICIANS_URL, params={"offset": 500}, headers=auth)).json()

        assert body["items"] == []
        assert body["total"] == len(ROSTER)
        assert body["has_more"] is False

    @pytest.mark.parametrize(
        ("params", "field"),
        [
            ({"limit": 0}, "limit"),
            ({"limit": -1}, "limit"),
            ({"limit": MAX_PAGE_LIMIT + 1}, "limit"),
            ({"offset": -1}, "offset"),
            ({"limit": "many"}, "limit"),
        ],
    )
    async def test_a_window_outside_the_bounds_is_refused_not_clamped(
        self, client: AsyncClient, auth: dict[str, str], params: dict[str, Any], field: str
    ) -> None:
        """An HTTP client is never quietly handed a page it did not ask for (``CLAUDE.md`` §4)."""
        response = await client.get(POLITICIANS_URL, params=params, headers=auth)

        error = assert_error_envelope(response, status=422)
        assert field in str(error["details"])


# ---------------------------------------------------------------------------------------
# GET /v1/politicians/{politician_id}
# ---------------------------------------------------------------------------------------


class TestReadPolitician:
    async def test_a_known_roster_id_resolves(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        response = await client.get(f"{POLITICIANS_URL}/TX-SEN-R", headers=auth)

        assert response.status_code == 200
        assert response.json()["last_name"] == "Ashgrove"

    async def test_an_unknown_roster_id_is_a_404_in_the_standard_envelope(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        response = await client.get(f"{POLITICIANS_URL}/ZZ-000", headers=auth)

        error = assert_error_envelope(response, status=404, code="not_found")
        assert error["details"]["resource"] == "politician"
        assert error["details"]["identifier"] == "ZZ-000"

    async def test_the_id_is_a_string_not_a_uuid(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        """The roster's own external identifier is the primary key — that is the point of it."""
        response = await client.get(f"{POLITICIANS_URL}/not-a-uuid", headers=auth)

        assert_error_envelope(response, status=404, code="not_found")

    async def test_the_document_declares_it_as_a_string(self, app: FastAPI) -> None:
        parameters = app.openapi()["paths"][f"{POLITICIANS_URL}/{{politician_id}}"]["get"][
            "parameters"
        ]
        schema = next(
            parameter["schema"] for parameter in parameters if parameter["name"] == "politician_id"
        )

        assert schema["type"] == "string"
        assert "format" not in schema

    async def test_there_is_no_way_to_write_to_the_roster(self, app: FastAPI) -> None:
        """Reference data is filled by the seed script, never over HTTP."""
        paths = app.openapi()["paths"]

        for path in (POLITICIANS_URL, f"{POLITICIANS_URL}/{{politician_id}}"):
            assert set(paths[path]) == {"get"}, path
