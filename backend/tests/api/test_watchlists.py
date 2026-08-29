"""Route contract tests for ``/v1/watchlists`` — and the ownership isolation ANV-15 is for.

The API tier (``CLAUDE.md`` §6): real routers, real middleware, real error envelope, **no
database**. ``get_watchlist_service`` and ``get_auth_service`` are redirected at real
services sitting on in-memory repos, so the route, the guard, the service's own branches and
the envelope are all genuinely under test with Docker stopped. Overriding the *factories* and
nothing else is the pattern from ``tests/api/test_stock_data.py``.

**There are two accounts in ``FakeUserRepo``, and that is the point of this module.** Both
hold real bearer tokens minted against the app's own JWT settings, so "Mallory cannot read
Stephen's watchlist" is asserted the way an attacker would find out — over HTTP, with a
valid token of her own. The endpoint being replaced would have let her: its reorder handler
took ``current_user`` as a dependency and never read it, so any authenticated caller could
rearrange (and, through ``add_watchlist_stock``, add to) anybody's list. Registration is
self-service, so "any authenticated caller" is "anybody who spent thirty seconds signing up".

For each of the five routes that name a ``watchlist_id``, three things are asserted:

* Mallory gets a **404, not a 403** — a 403 confirms the id is real, which is the half of
  the information worth protecting (``CLAUDE.md`` §4);
* the response body is **identical**, key for key and message for message, to the body for
  an id that was never created — only the echoed identifier differs;
* Stephen's watchlist is **unchanged** afterwards, so a refusal that leaked a write would
  not pass merely by returning the right status.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import SecretStr

from app.deps.auth import get_auth_service
from app.deps.watchlist import get_watchlist_service
from app.domain.auth import ACCESS_TOKEN_TYPE, create_token
from app.models.watchlist import DEFAULT_TITLE
from app.schemas.pagination import MAX_PAGE_LIMIT
from app.services.auth import AuthService
from app.services.watchlist import WatchlistService
from app.settings import Settings
from tests.helpers import (
    FakeStockRepo,
    FakeUserRepo,
    FakeWatchlistRepo,
    StubSession,
    assert_error_envelope,
    make_entry,
    make_stock,
    make_user,
    make_watchlist,
)

SECRET = "api-tier-jwt-secret"
ALGORITHM = "HS256"

WATCHLISTS_URL = "/v1/watchlists"

#: An id no fixture creates — the "was never created" half of every 404 comparison.
MISSING = uuid.UUID(int=404)


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
def stephen() -> Any:
    return make_user(username="stephen1", email="stephen@example.com")


@pytest.fixture
def mallory() -> Any:
    """A second, entirely ordinary account. Nothing about her is special — which is exactly
    what makes the isolation tests meaningful."""
    return make_user(username="mallory1", email="mallory@example.com")


@pytest.fixture
def catalogue() -> list[Any]:
    return [
        make_stock(ticker_symbol="AAPL", company="Apple Inc."),
        make_stock(ticker_symbol="NVDA", company="NVIDIA Corp."),
        make_stock(ticker_symbol="TSLA", company="Tesla Inc."),
        make_stock(ticker_symbol="QUIET", company="Quiet Holdings Inc."),
    ]


@pytest.fixture
def watchlist(stephen: Any) -> Any:
    return make_watchlist(user_id=stephen.user_id, title="Semis")


@pytest.fixture
def empty_watchlist(stephen: Any) -> Any:
    """Stephen has started a list and put nothing on it — the 200-with-`[]` case."""
    return make_watchlist(user_id=stephen.user_id, title="Later")


@pytest.fixture
def hers(mallory: Any) -> Any:
    """Mallory's own watchlist, so no attack below fails merely for want of one."""
    return make_watchlist(user_id=mallory.user_id, title="Theirs")


@pytest.fixture
def watchlists(
    watchlist: Any, empty_watchlist: Any, hers: Any, catalogue: list[Any]
) -> FakeWatchlistRepo:
    entries = [
        make_entry(watchlist_id=watchlist.watchlist_id, stock=stock, position=n)
        for n, stock in enumerate(catalogue[:3])
    ]
    return FakeWatchlistRepo(watchlist, empty_watchlist, hers, entries=entries, catalogue=catalogue)


@pytest.fixture
def app(
    app: FastAPI,
    settings: Settings,
    watchlists: FakeWatchlistRepo,
    catalogue: list[Any],
    stephen: Any,
    mallory: Any,
) -> FastAPI:
    """The application with the watchlist service on in-memory repos, and auth likewise.

    Auth is overridden as well because every route sits behind ``CurrentUser``: without it
    the guard would reach for Postgres and every test in this module would be a database
    test. Both accounts live in one :class:`~tests.helpers.FakeUserRepo`, which is what lets
    two real tokens resolve against the same application.
    """
    session = StubSession()
    service = WatchlistService(
        session,  # type: ignore[arg-type]
        settings,
        watchlists=watchlists,  # type: ignore[arg-type]
        stocks=FakeStockRepo(*catalogue),  # type: ignore[arg-type]
    )
    auth_service = AuthService(
        session,  # type: ignore[arg-type]
        settings,
        users=FakeUserRepo(stephen, mallory),  # type: ignore[arg-type]
    )
    app.dependency_overrides[get_watchlist_service] = lambda: service
    app.dependency_overrides[get_auth_service] = lambda: auth_service
    return app


def bearer(account: Any) -> dict[str, str]:
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
def auth(stephen: Any) -> dict[str, str]:
    """Stephen's credentials — the owner, unless a test says otherwise."""
    return bearer(stephen)


@pytest.fixture
def intruder_auth(mallory: Any) -> dict[str, str]:
    return bearer(mallory)


@pytest.fixture
def detail_url(watchlist: Any) -> str:
    return f"{WATCHLISTS_URL}/{watchlist.watchlist_id}"


def tickers(payload: dict[str, Any]) -> list[str]:
    return [entry["stock"]["ticker_symbol"] for entry in payload["entries"]]


# ---------------------------------------------------------------------------------------
# authentication
# ---------------------------------------------------------------------------------------


class TestAuthenticationIsRequired:
    @staticmethod
    def every_route(watchlist_id: uuid.UUID, stock_id: uuid.UUID) -> list[tuple[str, str]]:
        base = f"{WATCHLISTS_URL}/{watchlist_id}"
        return [
            ("POST", WATCHLISTS_URL),
            ("GET", WATCHLISTS_URL),
            ("GET", base),
            ("DELETE", base),
            ("POST", f"{base}/stocks"),
            ("DELETE", f"{base}/stocks/{stock_id}"),
            ("PATCH", f"{base}/stocks/{stock_id}"),
        ]

    async def test_every_route_refuses_an_anonymous_caller(
        self, client: AsyncClient, watchlist: Any, catalogue: list[Any]
    ) -> None:
        for method, url in self.every_route(watchlist.watchlist_id, catalogue[0].stock_id):
            response = await client.request(method, url, json={})

            assert_error_envelope(response, status=401, code="unauthorized")
            assert response.headers.get("www-authenticate") == "Bearer", url

    async def test_every_route_refuses_a_garbage_token(
        self, client: AsyncClient, watchlist: Any, catalogue: list[Any]
    ) -> None:
        for method, url in self.every_route(watchlist.watchlist_id, catalogue[0].stock_id):
            response = await client.request(
                method, url, json={}, headers={"Authorization": "Bearer nonsense"}
            )

            assert_error_envelope(response, status=401, code="invalid_token")

    async def test_a_refresh_token_is_not_an_access_token(
        self, client: AsyncClient, stephen: Any, detail_url: str
    ) -> None:
        refresh = create_token(
            subject=stephen.user_id,
            token_type="refresh",  # type: ignore[arg-type]
            now=dt.datetime.now(dt.UTC),
            lifetime=dt.timedelta(days=7),
            secret=SECRET,
            algorithm=ALGORITHM,
        )

        response = await client.get(detail_url, headers={"Authorization": f"Bearer {refresh}"})

        assert_error_envelope(response, status=401, code="wrong_token_type")

    async def test_no_route_is_public_by_accident(self, app: FastAPI) -> None:
        paths = app.openapi()["paths"]
        watchlist_paths = {
            path: spec for path, spec in paths.items() if path.startswith(WATCHLISTS_URL)
        }

        assert sorted(watchlist_paths) == [
            WATCHLISTS_URL,
            f"{WATCHLISTS_URL}/{{watchlist_id}}",
            f"{WATCHLISTS_URL}/{{watchlist_id}}/stocks",
            f"{WATCHLISTS_URL}/{{watchlist_id}}/stocks/{{stock_id}}",
        ]
        for path, spec in watchlist_paths.items():
            for method, operation in spec.items():
                assert "security" in operation, f"{method.upper()} {path}"


# ---------------------------------------------------------------------------------------
# ownership isolation — the point of the ticket
# ---------------------------------------------------------------------------------------


class TestOwnershipIsolation:
    """Mallory, holding a perfectly valid token of her own, against Stephen's watchlist."""

    @staticmethod
    def requests(watchlist_id: uuid.UUID, catalogue: list[Any]) -> dict[str, tuple[str, str, Any]]:
        """``name -> (method, url, json body or None)`` for every route taking an id.

        Two securities are needed, not one: the add route wants a stock that is **not** yet
        on the list and the remove/reorder routes want one that **is**, or the owner-side
        control below would fail for a reason that has nothing to do with ownership.
        """
        base = f"{WATCHLISTS_URL}/{watchlist_id}"
        member, newcomer = catalogue[0].stock_id, catalogue[3].stock_id
        return {
            "read": ("GET", base, None),
            "delete": ("DELETE", base, None),
            "add stock": ("POST", f"{base}/stocks", {"stock_id": str(newcomer)}),
            "remove stock": ("DELETE", f"{base}/stocks/{member}", None),
            "reorder": ("PATCH", f"{base}/stocks/{member}", {"position": 0}),
        }

    NAMES = ("read", "delete", "add stock", "remove stock", "reorder")

    @pytest.mark.parametrize("name", NAMES)
    async def test_another_account_gets_a_404_not_a_403(
        self,
        client: AsyncClient,
        intruder_auth: dict[str, str],
        watchlist: Any,
        catalogue: list[Any],
        name: str,
    ) -> None:
        method, url, body = self.requests(watchlist.watchlist_id, catalogue)[name]

        response = await client.request(method, url, json=body, headers=intruder_auth)

        assert response.status_code != 403, "a 403 confirms the watchlist id is real"
        assert_error_envelope(response, status=404, code="not_found")

    @pytest.mark.parametrize("name", NAMES)
    async def test_the_body_is_identical_to_one_for_an_id_that_never_existed(
        self,
        client: AsyncClient,
        intruder_auth: dict[str, str],
        watchlist: Any,
        catalogue: list[Any],
        name: str,
    ) -> None:
        """Key for key and word for word, bar the identifier the caller itself supplied.

        This is the assertion that makes the refusal an actual non-answer: a differently
        phrased message, an extra ``details`` key or a different ``code`` would each be
        enough to distinguish "somebody else's" from "nobody's".
        """
        method, trespass_url, body = self.requests(watchlist.watchlist_id, catalogue)[name]
        _, absent_url, _ = self.requests(MISSING, catalogue)[name]

        trespass = await client.request(method, trespass_url, json=body, headers=intruder_auth)
        absent = await client.request(method, absent_url, json=body, headers=intruder_auth)

        assert trespass.status_code == absent.status_code == 404
        theirs = trespass.json()["error"]
        nobodys = absent.json()["error"]
        assert theirs["code"] == nobodys["code"]
        assert theirs["details"]["resource"] == nobodys["details"]["resource"]
        assert set(theirs["details"]) == set(nobodys["details"])
        # The message differs only where the caller's own id is echoed back into it.
        assert theirs["message"].replace(str(watchlist.watchlist_id), "") == nobodys[
            "message"
        ].replace(str(MISSING), "")

    @pytest.mark.parametrize("name", NAMES)
    async def test_the_refusal_changes_nothing(
        self,
        client: AsyncClient,
        auth: dict[str, str],
        intruder_auth: dict[str, str],
        watchlist: Any,
        catalogue: list[Any],
        detail_url: str,
        name: str,
    ) -> None:
        before = (await client.get(detail_url, headers=auth)).json()

        method, url, body = self.requests(watchlist.watchlist_id, catalogue)[name]
        await client.request(method, url, json=body, headers=intruder_auth)

        assert (await client.get(detail_url, headers=auth)).json() == before

    @pytest.mark.parametrize("name", NAMES)
    async def test_the_owner_is_not_refused(
        self,
        client: AsyncClient,
        auth: dict[str, str],
        watchlist: Any,
        catalogue: list[Any],
        name: str,
    ) -> None:
        """The control: without it every assertion above would pass on an API that refused
        everybody, which is not the fix anybody wanted."""
        method, url, body = self.requests(watchlist.watchlist_id, catalogue)[name]

        response = await client.request(method, url, json=body, headers=auth)

        assert response.status_code < 400, response.text

    async def test_the_listing_shows_only_the_callers_own_watchlists(
        self, client: AsyncClient, intruder_auth: dict[str, str], hers: Any
    ) -> None:
        response = await client.get(WATCHLISTS_URL, headers=intruder_auth)

        assert response.status_code == 200
        payload = response.json()
        assert [row["watchlist_id"] for row in payload["items"]] == [str(hers.watchlist_id)]
        assert payload["total"] == 1


# ---------------------------------------------------------------------------------------
# create and list
# ---------------------------------------------------------------------------------------


class TestCreate:
    async def test_it_answers_201_with_the_new_watchlist(
        self, client: AsyncClient, auth: dict[str, str], stephen: Any
    ) -> None:
        response = await client.post(WATCHLISTS_URL, json={"title": "Semis"}, headers=auth)

        assert response.status_code == 201
        payload = response.json()
        assert payload["title"] == "Semis"
        assert payload["user_id"] == str(stephen.user_id)

    async def test_an_empty_body_takes_the_default_title(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        response = await client.post(WATCHLISTS_URL, json={}, headers=auth)

        assert response.json()["title"] == DEFAULT_TITLE

    async def test_a_supplied_user_id_is_ignored_not_honoured(
        self, client: AsyncClient, auth: dict[str, str], stephen: Any, mallory: Any
    ) -> None:
        """``WatchlistCreate`` has no ``user_id`` field, so pydantic drops the key rather
        than creating a watchlist on somebody else's account."""
        response = await client.post(
            WATCHLISTS_URL,
            json={"title": "Nice try", "user_id": str(mallory.user_id)},
            headers=auth,
        )

        assert response.json()["user_id"] == str(stephen.user_id)

    async def test_a_blank_title_is_a_422(self, client: AsyncClient, auth: dict[str, str]) -> None:
        response = await client.post(WATCHLISTS_URL, json={"title": ""}, headers=auth)

        assert_error_envelope(response, status=422, code="validation_error")


class TestList:
    async def test_it_returns_the_page_envelope(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        response = await client.get(WATCHLISTS_URL, headers=auth)

        payload = response.json()
        assert set(payload) == {"items", "total", "limit", "offset", "has_more"}
        assert payload["total"] == 2

    async def test_the_rows_carry_no_entries(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        """A list of watchlists must not drag every membership row and every stock behind
        it — that is what the detail route is for."""
        response = await client.get(WATCHLISTS_URL, headers=auth)

        for row in response.json()["items"]:
            assert set(row) == {"watchlist_id", "user_id", "title"}

    async def test_an_over_large_limit_is_refused_at_the_edge(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        """422 rather than a silent clamp, so an HTTP client is never quietly handed a
        shorter page than it asked for (``CLAUDE.md`` §4)."""
        response = await client.get(
            WATCHLISTS_URL, params={"limit": MAX_PAGE_LIMIT + 1}, headers=auth
        )

        assert_error_envelope(response, status=422, code="validation_error")

    async def test_a_negative_offset_is_refused_at_the_edge(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        response = await client.get(WATCHLISTS_URL, params={"offset": -1}, headers=auth)

        assert_error_envelope(response, status=422, code="validation_error")


# ---------------------------------------------------------------------------------------
# read and delete
# ---------------------------------------------------------------------------------------


class TestRead:
    async def test_the_stocks_come_back_in_position_order(
        self, client: AsyncClient, auth: dict[str, str], detail_url: str
    ) -> None:
        response = await client.get(detail_url, headers=auth)

        assert response.status_code == 200
        payload = response.json()
        assert tickers(payload) == ["AAPL", "NVDA", "TSLA"]
        assert [entry["position"] for entry in payload["entries"]] == [0, 1, 2]

    async def test_each_entry_carries_both_halves_of_its_composite_key(
        self, client: AsyncClient, auth: dict[str, str], detail_url: str, watchlist: Any
    ) -> None:
        """``WatchlistData`` has no surrogate id; the pair *is* the entry's identity."""
        entry = (await client.get(detail_url, headers=auth)).json()["entries"][0]

        assert entry["watchlist_id"] == str(watchlist.watchlist_id)
        assert uuid.UUID(entry["stock_id"]) == uuid.UUID(entry["stock"]["stock_id"])
        assert "id" not in entry

    async def test_an_empty_watchlist_is_200_with_an_empty_list(
        self, client: AsyncClient, auth: dict[str, str], empty_watchlist: Any
    ) -> None:
        """The invalid response this replaces: the old handler raised ``204 No Content``
        **with** a ``detail`` body, so an empty list was indistinguishable from an error and
        the response itself was malformed — 204 forbids a body."""
        response = await client.get(
            f"{WATCHLISTS_URL}/{empty_watchlist.watchlist_id}", headers=auth
        )

        assert response.status_code == 200
        assert response.json()["entries"] == []
        assert response.headers["content-type"].startswith("application/json")

    async def test_a_malformed_id_is_a_422_not_a_404(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        """Input shape is judged before existence, so a typo never reveals whether an id is
        real."""
        response = await client.get(f"{WATCHLISTS_URL}/not-a-uuid", headers=auth)

        assert_error_envelope(response, status=422, code="validation_error")


class TestDelete:
    async def test_it_answers_204_with_no_body(
        self, client: AsyncClient, auth: dict[str, str], detail_url: str
    ) -> None:
        response = await client.delete(detail_url, headers=auth)

        assert response.status_code == 204
        assert response.content == b""

    async def test_the_watchlist_is_gone_afterwards(
        self, client: AsyncClient, auth: dict[str, str], detail_url: str
    ) -> None:
        await client.delete(detail_url, headers=auth)

        assert_error_envelope(
            await client.get(detail_url, headers=auth), status=404, code="not_found"
        )

    async def test_deleting_an_unknown_id_is_a_404(
        self, client: AsyncClient, auth: dict[str, str]
    ) -> None:
        response = await client.delete(f"{WATCHLISTS_URL}/{MISSING}", headers=auth)

        error = assert_error_envelope(response, status=404, code="not_found")
        assert error["details"]["resource"] == "watchlist"


# ---------------------------------------------------------------------------------------
# entries
# ---------------------------------------------------------------------------------------


class TestAddStock:
    async def test_it_answers_201_with_the_membership_row(
        self, client: AsyncClient, auth: dict[str, str], detail_url: str, catalogue: list[Any]
    ) -> None:
        response = await client.post(
            f"{detail_url}/stocks",
            json={"stock_id": str(catalogue[3].stock_id)},
            headers=auth,
        )

        assert response.status_code == 201
        assert response.json()["position"] == 3

    async def test_an_omitted_position_appends(
        self, client: AsyncClient, auth: dict[str, str], detail_url: str, catalogue: list[Any]
    ) -> None:
        """A change from the old endpoint, which unconditionally prepended and pushed the
        user's arrangement down by one every time they watched anything new."""
        await client.post(
            f"{detail_url}/stocks",
            json={"stock_id": str(catalogue[3].stock_id)},
            headers=auth,
        )

        payload = (await client.get(detail_url, headers=auth)).json()
        assert tickers(payload) == ["AAPL", "NVDA", "TSLA", "QUIET"]

    async def test_an_explicit_position_inserts_and_shifts_the_rest_down(
        self, client: AsyncClient, auth: dict[str, str], detail_url: str, catalogue: list[Any]
    ) -> None:
        await client.post(
            f"{detail_url}/stocks",
            json={"stock_id": str(catalogue[3].stock_id), "position": 1},
            headers=auth,
        )

        payload = (await client.get(detail_url, headers=auth)).json()
        assert tickers(payload) == ["AAPL", "QUIET", "NVDA", "TSLA"]
        assert [entry["position"] for entry in payload["entries"]] == [0, 1, 2, 3]

    async def test_a_duplicate_is_a_409(
        self, client: AsyncClient, auth: dict[str, str], detail_url: str, catalogue: list[Any]
    ) -> None:
        response = await client.post(
            f"{detail_url}/stocks",
            json={"stock_id": str(catalogue[0].stock_id)},
            headers=auth,
        )

        error = assert_error_envelope(response, status=409, code="conflict")
        assert error["details"]["field"] == "stock_id"

    async def test_an_unknown_security_is_a_404_naming_the_stock(
        self, client: AsyncClient, auth: dict[str, str], detail_url: str
    ) -> None:
        response = await client.post(
            f"{detail_url}/stocks", json={"stock_id": str(MISSING)}, headers=auth
        )

        error = assert_error_envelope(response, status=404, code="not_found")
        assert error["details"]["resource"] == "stock"

    async def test_a_position_past_the_end_is_a_422(
        self, client: AsyncClient, auth: dict[str, str], detail_url: str, catalogue: list[Any]
    ) -> None:
        response = await client.post(
            f"{detail_url}/stocks",
            json={"stock_id": str(catalogue[3].stock_id), "position": 9},
            headers=auth,
        )

        error = assert_error_envelope(response, status=422, code="validation_error")
        assert error["details"]["field"] == "position"

    async def test_a_negative_position_is_refused_at_the_edge(
        self, client: AsyncClient, auth: dict[str, str], detail_url: str, catalogue: list[Any]
    ) -> None:
        """``Position`` carries ``ge=0``, so pydantic refuses before the service is reached
        — and the service refuses it too, for callers that never touch HTTP."""
        response = await client.post(
            f"{detail_url}/stocks",
            json={"stock_id": str(catalogue[3].stock_id), "position": -1},
            headers=auth,
        )

        assert_error_envelope(response, status=422, code="validation_error")


class TestRemoveStock:
    async def test_it_answers_204_and_closes_the_gap(
        self, client: AsyncClient, auth: dict[str, str], detail_url: str, catalogue: list[Any]
    ) -> None:
        response = await client.delete(f"{detail_url}/stocks/{catalogue[1].stock_id}", headers=auth)

        assert (response.status_code, response.content) == (204, b"")
        payload = (await client.get(detail_url, headers=auth)).json()
        assert tickers(payload) == ["AAPL", "TSLA"]
        assert [entry["position"] for entry in payload["entries"]] == [0, 1]

    async def test_a_stock_not_on_the_list_is_a_404_naming_the_entry(
        self, client: AsyncClient, auth: dict[str, str], detail_url: str, catalogue: list[Any]
    ) -> None:
        response = await client.delete(f"{detail_url}/stocks/{catalogue[3].stock_id}", headers=auth)

        error = assert_error_envelope(response, status=404, code="not_found")
        assert error["details"]["resource"] == "watchlist entry"


class TestMoveStock:
    async def test_it_returns_the_whole_list_in_its_new_order(
        self, client: AsyncClient, auth: dict[str, str], detail_url: str, catalogue: list[Any]
    ) -> None:
        response = await client.patch(
            f"{detail_url}/stocks/{catalogue[2].stock_id}",
            json={"position": 0},
            headers=auth,
        )

        assert response.status_code == 200
        assert tickers(response.json()) == ["TSLA", "AAPL", "NVDA"]

    async def test_the_move_persists(
        self, client: AsyncClient, auth: dict[str, str], detail_url: str, catalogue: list[Any]
    ) -> None:
        await client.patch(
            f"{detail_url}/stocks/{catalogue[0].stock_id}",
            json={"position": 2},
            headers=auth,
        )

        assert tickers((await client.get(detail_url, headers=auth)).json()) == [
            "NVDA",
            "TSLA",
            "AAPL",
        ]

    async def test_the_stock_that_moves_is_the_one_in_the_url(
        self, client: AsyncClient, auth: dict[str, str], detail_url: str, catalogue: list[Any]
    ) -> None:
        """The defect this ticket exists to fix, at the HTTP boundary. The old ``PUT
        /v1/watchlist/stock`` took ``stock_id``, ``current_index`` **and**
        ``destination_index`` in the query string and then moved whichever row sat at
        ``current_index`` — so a client one drag behind reordered a stock the user never
        touched. There is no index-of-origin parameter here to be stale.
        """
        response = await client.patch(
            f"{detail_url}/stocks/{catalogue[1].stock_id}",
            json={"position": 0},
            headers=auth,
        )

        assert tickers(response.json())[0] == "NVDA"

    async def test_the_body_carries_only_a_position(self) -> None:
        from app.schemas.watchlist import WatchlistEntryUpdate

        assert set(WatchlistEntryUpdate.model_fields) == {"position"}

    async def test_a_position_past_the_end_is_a_422(
        self, client: AsyncClient, auth: dict[str, str], detail_url: str, catalogue: list[Any]
    ) -> None:
        response = await client.patch(
            f"{detail_url}/stocks/{catalogue[0].stock_id}",
            json={"position": 3},
            headers=auth,
        )

        error = assert_error_envelope(response, status=422, code="validation_error")
        assert error["details"]["field"] == "position"

    async def test_a_negative_position_is_a_422_not_a_wrap_around(
        self, client: AsyncClient, auth: dict[str, str], detail_url: str, catalogue: list[Any]
    ) -> None:
        """The quieter half of the old bug: Python subscripts from the end, so ``-1`` used
        to move the stock to the back of the list and answer 201."""
        response = await client.patch(
            f"{detail_url}/stocks/{catalogue[0].stock_id}",
            json={"position": -1},
            headers=auth,
        )

        assert_error_envelope(response, status=422, code="validation_error")
        assert tickers((await client.get(detail_url, headers=auth)).json())[0] == "AAPL"

    async def test_a_stock_not_on_the_list_is_a_404(
        self, client: AsyncClient, auth: dict[str, str], detail_url: str, catalogue: list[Any]
    ) -> None:
        response = await client.patch(
            f"{detail_url}/stocks/{catalogue[3].stock_id}",
            json={"position": 0},
            headers=auth,
        )

        error = assert_error_envelope(response, status=404, code="not_found")
        assert error["details"]["resource"] == "watchlist entry"

    async def test_moving_within_an_empty_watchlist_is_a_404(
        self, client: AsyncClient, auth: dict[str, str], empty_watchlist: Any, catalogue: list[Any]
    ) -> None:
        response = await client.patch(
            f"{WATCHLISTS_URL}/{empty_watchlist.watchlist_id}/stocks/{catalogue[0].stock_id}",
            json={"position": 0},
            headers=auth,
        )

        assert_error_envelope(response, status=404, code="not_found")


# ---------------------------------------------------------------------------------------
# a whole session over HTTP
# ---------------------------------------------------------------------------------------


async def test_create_fill_rearrange_and_delete(
    client: AsyncClient, auth: dict[str, str], catalogue: list[Any]
) -> None:
    """One user's realistic sequence, entirely over the API and with no database.

    Worth having as a single test because each step depends on the previous one's response:
    a contract that is right individually and wrong in composition is still wrong.
    """
    created = await client.post(WATCHLISTS_URL, json={"title": "New"}, headers=auth)
    url = f"{WATCHLISTS_URL}/{created.json()['watchlist_id']}"

    assert (await client.get(url, headers=auth)).json()["entries"] == []

    for stock in catalogue[:3]:
        added = await client.post(
            f"{url}/stocks", json={"stock_id": str(stock.stock_id)}, headers=auth
        )
        assert added.status_code == 201

    assert tickers((await client.get(url, headers=auth)).json()) == [
        "AAPL",
        "NVDA",
        "TSLA",
    ]

    moved = await client.patch(
        f"{url}/stocks/{catalogue[2].stock_id}", json={"position": 0}, headers=auth
    )
    assert tickers(moved.json()) == ["TSLA", "AAPL", "NVDA"]

    removed = await client.delete(f"{url}/stocks/{catalogue[1].stock_id}", headers=auth)
    assert removed.status_code == 204
    final = (await client.get(url, headers=auth)).json()
    assert tickers(final) == ["TSLA", "AAPL"]
    assert [entry["position"] for entry in final["entries"]] == [0, 1]

    assert (await client.delete(url, headers=auth)).status_code == 204
    assert_error_envelope(await client.get(url, headers=auth), status=404)
