"""Route contract tests for ``/v1/auth``.

The API tier (``CLAUDE.md`` §6): real routers, real middleware, real error envelope, **no
database**. ``get_auth_service`` is overridden with a real
:class:`~app.services.auth.AuthService` sitting on :class:`tests.helpers.FakeUserRepo`, so
what is under test is the whole stack a client actually meets — form parsing, the JSON
body, status codes, the four-key error body — while Postgres is never involved and the
suite still runs with Docker stopped.

Overriding the *factory* rather than monkeypatching anything is the pattern every later
resource copies: ``app/deps/`` exposes one ``get_x_service`` per resource precisely so a
route test has a single seam.

**The test this epic exists for** is
:meth:`TestRefreshRotation.test_an_access_token_presented_to_the_refresh_endpoint_is_rejected`.
In the old API that exact request returned 200 and a fresh long-lived pair, so any leaked
access token could be renewed forever. It must now be a 401 with code ``wrong_token_type``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import SecretStr

from app.deps.auth import CurrentUser, get_auth_service
from app.domain.auth import ACCESS_TOKEN_TYPE, REFRESH_TOKEN_TYPE, create_token
from app.schemas.auth import RECOVERY_MESSAGE
from app.services.auth import AuthService
from app.settings import Settings
from app.utils.security import hash_password
from tests.helpers import FakeUserRepo, StubSession, assert_error_envelope, make_user

SECRET = "api-tier-jwt-secret"
ALGORITHM = "HS256"
PASSWORD = "correct-horse-battery"
WRONG_PASSWORD = "incorrect-horse-battery"
PASSWORD_HASH = hash_password(PASSWORD)

USERNAME = "stephen1"
EMAIL = "stephen@example.com"

LOGIN_URL = "/v1/auth/login"
REFRESH_URL = "/v1/auth/refresh"
RECOVERY_URL = "/v1/auth/recovery"
#: A protected route installed by the ``app`` fixture below. There is no protected resource
#: in the API yet — ANV-12 brings the first — so the guard is exercised against a probe.
PROBE_URL = "/probe/me"


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
    return make_user(username=USERNAME, email=EMAIL, password_hash=PASSWORD_HASH)


@pytest.fixture
def users(account: Any) -> FakeUserRepo:
    return FakeUserRepo(account)


@pytest.fixture
def app(app: FastAPI, settings: Settings, users: FakeUserRepo) -> FastAPI:
    """The application with its auth service backed by the in-memory repo.

    Also mounts a probe route behind ``CurrentUser``, which is how the guard gets tested
    before any real protected resource exists.
    """
    service = AuthService(session=None, settings=settings, users=users)  # type: ignore[arg-type]
    app.dependency_overrides[get_auth_service] = lambda: service

    @app.get(PROBE_URL)
    async def probe(user: CurrentUser) -> dict[str, str]:
        return {"user_id": str(user.user_id)}

    return app


def mint(
    subject: uuid.UUID,
    *,
    token_type: str = REFRESH_TOKEN_TYPE,
    now: datetime | None = None,
    lifetime: timedelta = timedelta(days=7),
) -> str:
    return create_token(
        subject=subject,
        token_type=token_type,  # type: ignore[arg-type]
        now=now or datetime.now(UTC),
        lifetime=lifetime,
        secret=SECRET,
        algorithm=ALGORITHM,
    )


def expired(subject: uuid.UUID, *, token_type: str) -> str:
    """A token that was valid a month ago and is not now."""
    return mint(
        subject,
        token_type=token_type,
        now=datetime.now(UTC) - timedelta(days=30),
        lifetime=timedelta(minutes=1),
    )


# ---------------------------------------------------------------------------------------
# POST /v1/auth/login
# ---------------------------------------------------------------------------------------


class TestLogin:
    async def test_valid_credentials_return_the_pair_the_frontend_parses(
        self, client: AsyncClient
    ) -> None:
        response = await client.post(
            LOGIN_URL, data={"username": USERNAME, "password": PASSWORD}
        )

        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"access_token", "refresh_token", "token_type"}
        assert body["token_type"] == "bearer"
        assert body["access_token"] and body["refresh_token"]

    async def test_an_email_address_works_in_the_username_field(
        self, client: AsyncClient
    ) -> None:
        """Preserved from the old API, which resolved either with a single `OR`."""
        response = await client.post(LOGIN_URL, data={"username": EMAIL, "password": PASSWORD})

        assert response.status_code == 200

    async def test_the_access_token_opens_a_protected_route(
        self, client: AsyncClient, account: Any
    ) -> None:
        login = await client.post(LOGIN_URL, data={"username": USERNAME, "password": PASSWORD})
        token = login.json()["access_token"]

        response = await client.get(PROBE_URL, headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200
        assert response.json() == {"user_id": str(account.user_id)}

    async def test_an_unknown_identifier_is_a_401(self, client: AsyncClient) -> None:
        response = await client.post(
            LOGIN_URL, data={"username": "nobody", "password": PASSWORD}
        )

        assert_error_envelope(response, status=401, code="unauthorized")

    async def test_a_wrong_password_is_a_401(self, client: AsyncClient) -> None:
        response = await client.post(
            LOGIN_URL, data={"username": USERNAME, "password": WRONG_PASSWORD}
        )

        assert_error_envelope(response, status=401, code="unauthorized")

    async def test_the_two_failures_are_indistinguishable_on_the_wire(
        self, client: AsyncClient
    ) -> None:
        """No status, code, message or detail may tell an attacker the account exists."""
        unknown = await client.post(LOGIN_URL, data={"username": "ghost", "password": PASSWORD})
        wrong = await client.post(
            LOGIN_URL, data={"username": USERNAME, "password": WRONG_PASSWORD}
        )

        assert unknown.status_code == wrong.status_code == 401
        unknown_error = assert_error_envelope(unknown, status=401)
        wrong_error = assert_error_envelope(wrong, status=401)
        # `request_id` is per-request by design and is the only key allowed to differ.
        unknown_error.pop("request_id")
        wrong_error.pop("request_id")
        assert unknown_error == wrong_error

    async def test_login_is_form_encoded_not_json(self, client: AsyncClient) -> None:
        """`CLAUDE.md` §4 fixes the OAuth2 password flow; a JSON body is a 422."""
        response = await client.post(LOGIN_URL, json={"username": USERNAME, "password": PASSWORD})

        assert_error_envelope(response, status=422, code="validation_error")

    async def test_a_missing_password_is_a_422(self, client: AsyncClient) -> None:
        response = await client.post(LOGIN_URL, data={"username": USERNAME})

        assert_error_envelope(response, status=422, code="validation_error")


# ---------------------------------------------------------------------------------------
# POST /v1/auth/refresh
# ---------------------------------------------------------------------------------------


class TestRefreshRotation:
    async def test_a_refresh_token_is_exchanged_for_a_brand_new_pair(
        self, client: AsyncClient, account: Any
    ) -> None:
        """Rotation: the refresh token comes back changed, not echoed.

        Minted a minute in the past on purpose — HS256 is deterministic, so a token issued
        for the same subject within the same second would be byte-identical and "rotated"
        would be unfalsifiable.
        """
        old = mint(account.user_id, now=datetime.now(UTC) - timedelta(minutes=1))

        response = await client.post(REFRESH_URL, json={"refresh_token": old})

        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"access_token", "refresh_token", "token_type"}
        assert body["refresh_token"] != old

        # And the new access token really works.
        probe = await client.get(
            PROBE_URL, headers={"Authorization": f"Bearer {body['access_token']}"}
        )
        assert probe.status_code == 200

    async def test_an_access_token_presented_to_the_refresh_endpoint_is_rejected(
        self, client: AsyncClient, account: Any
    ) -> None:
        """**The regression this epic exists for.**

        The old ``POST /v1/refresh`` ran ``verify_access_token`` over whatever it was given
        and returned a fresh pair, so an *access* token — the short-lived half, the one
        that ends up in logs, crash reports and browser memory — could be traded for a
        long-lived one, over and over, forever. Compromise of a 30-minute token became
        permanent.

        It must now be a 401 with code ``wrong_token_type``, and it must hand back no
        tokens of any kind.
        """
        access_token = mint(account.user_id, token_type=ACCESS_TOKEN_TYPE)

        response = await client.post(REFRESH_URL, json={"refresh_token": access_token})

        error = assert_error_envelope(response, status=401, code="wrong_token_type")
        assert error["details"] == {"expected_type": "refresh", "actual_type": "access"}
        assert "access_token" not in response.text
        assert "refresh_token" not in response.json()

    async def test_an_expired_refresh_token_is_a_401(
        self, client: AsyncClient, account: Any
    ) -> None:
        response = await client.post(
            REFRESH_URL,
            json={"refresh_token": expired(account.user_id, token_type=REFRESH_TOKEN_TYPE)},
        )

        assert_error_envelope(response, status=401, code="token_expired")

    async def test_a_forged_token_is_a_401(self, client: AsyncClient) -> None:
        response = await client.post(REFRESH_URL, json={"refresh_token": "not.a.jwt"})

        assert_error_envelope(response, status=401, code="invalid_token")

    async def test_a_token_for_a_deleted_account_is_a_401(
        self, client: AsyncClient, account: Any, users: FakeUserRepo
    ) -> None:
        """A token stays cryptographically valid for a week after the row disappears."""
        token = mint(account.user_id)
        users.remove(account)

        response = await client.post(REFRESH_URL, json={"refresh_token": token})

        assert_error_envelope(response, status=401, code="unauthorized")

    async def test_the_token_travels_in_the_body_not_the_query_string(
        self, client: AsyncClient, account: Any
    ) -> None:
        """The old endpoint took it as a query parameter, i.e. into every proxy log."""
        token = mint(account.user_id)

        response = await client.post(f"{REFRESH_URL}?refresh_token={token}")

        assert_error_envelope(response, status=422, code="validation_error")

    async def test_an_empty_token_is_a_422(self, client: AsyncClient) -> None:
        response = await client.post(REFRESH_URL, json={"refresh_token": ""})

        assert_error_envelope(response, status=422, code="validation_error")


# ---------------------------------------------------------------------------------------
# Protected routes
# ---------------------------------------------------------------------------------------


class TestGuard:
    async def test_an_anonymous_caller_is_refused(self, client: AsyncClient) -> None:
        response = await client.get(PROBE_URL)

        assert_error_envelope(response, status=401, code="unauthorized")

    async def test_the_refusal_carries_the_bearer_challenge(self, client: AsyncClient) -> None:
        """`WWW-Authenticate` survives the envelope re-shaping in the error middleware."""
        response = await client.get(PROBE_URL)

        assert response.headers.get("www-authenticate") == "Bearer"

    async def test_an_expired_access_token_is_a_401(
        self, client: AsyncClient, account: Any
    ) -> None:
        token = expired(account.user_id, token_type=ACCESS_TOKEN_TYPE)

        response = await client.get(PROBE_URL, headers={"Authorization": f"Bearer {token}"})

        assert_error_envelope(response, status=401, code="token_expired")

    async def test_a_refresh_token_does_not_open_a_protected_route(
        self, client: AsyncClient, account: Any
    ) -> None:
        """The mirror of the refresh hole — the long-lived half must not be a key."""
        token = mint(account.user_id, token_type=REFRESH_TOKEN_TYPE)

        response = await client.get(PROBE_URL, headers={"Authorization": f"Bearer {token}"})

        error = assert_error_envelope(response, status=401, code="wrong_token_type")
        assert error["details"] == {"expected_type": "access", "actual_type": "refresh"}

    async def test_a_garbage_token_is_a_401(self, client: AsyncClient) -> None:
        response = await client.get(PROBE_URL, headers={"Authorization": "Bearer nonsense"})

        assert_error_envelope(response, status=401, code="invalid_token")

    async def test_a_token_for_a_deleted_account_is_a_401(
        self, client: AsyncClient, account: Any, users: FakeUserRepo
    ) -> None:
        token = mint(account.user_id, token_type=ACCESS_TOKEN_TYPE)
        users.remove(account)

        response = await client.get(PROBE_URL, headers={"Authorization": f"Bearer {token}"})

        assert_error_envelope(response, status=401, code="unauthorized")


# ---------------------------------------------------------------------------------------
# POST /v1/auth/recovery
# ---------------------------------------------------------------------------------------


class TestRecovery:
    async def test_an_existing_account_is_accepted(self, client: AsyncClient) -> None:
        response = await client.post(RECOVERY_URL, json={"username": USERNAME})

        assert response.status_code == 202
        assert response.json() == {"status": "accepted", "message": RECOVERY_MESSAGE}

    async def test_an_unknown_account_gets_the_identical_answer(
        self, client: AsyncClient
    ) -> None:
        """The old endpoint answered 404 `"User not found with username: <x>"`.

        That made password recovery a free username-enumeration API. Status **and** body
        must now be the same for both, so a caller learns nothing.
        """
        existing = await client.post(RECOVERY_URL, json={"username": USERNAME})
        missing = await client.post(RECOVERY_URL, json={"username": "no-such-person"})

        assert existing.status_code == missing.status_code == 202
        assert existing.json() == missing.json()

    async def test_the_response_never_echoes_the_username(self, client: AsyncClient) -> None:
        response = await client.post(RECOVERY_URL, json={"username": "no-such-person"})

        assert "no-such-person" not in response.text

    async def test_recovery_needs_no_credentials(self, client: AsyncClient) -> None:
        """"I have forgotten my password" cannot require being signed in."""
        response = await client.post(RECOVERY_URL, json={"username": USERNAME})

        assert response.status_code == 202

    async def test_a_missing_username_is_a_422(self, client: AsyncClient) -> None:
        response = await client.post(RECOVERY_URL, json={})

        assert_error_envelope(response, status=422, code="validation_error")


# ---------------------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------------------


class TestRouterWiring:
    def test_the_three_routes_are_mounted_under_the_version_prefix(self, app: FastAPI) -> None:
        paths = app.openapi()["paths"]

        assert set(paths) >= {LOGIN_URL, REFRESH_URL, RECOVERY_URL}
        for url in (LOGIN_URL, REFRESH_URL, RECOVERY_URL):
            assert set(paths[url]) == {"post"}

    def test_the_swagger_authorize_button_points_at_the_real_login_route(
        self, app: FastAPI
    ) -> None:
        """A `tokenUrl` naming a route that does not exist is a silently broken /docs."""
        from app.deps.auth import TOKEN_URL

        assert f"/{TOKEN_URL}" == LOGIN_URL

    def test_the_service_factory_wires_the_request_session_and_the_shared_repo(
        self, settings: Settings
    ) -> None:
        """The real ``get_auth_service`` — the one every test above overrides away.

        Repos are stateless singletons, so the service takes the module-level ``user_repo``
        rather than constructing one per request; the *session* is what varies.
        """
        from app.deps.auth import get_auth_service
        from app.repos.user import user_repo

        session = StubSession()

        service = get_auth_service(session, settings)  # type: ignore[arg-type]

        assert isinstance(service, AuthService)
        assert service.session is session
        assert service.settings is settings
        assert service.users is user_repo
