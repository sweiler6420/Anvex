"""Route contract tests for ``/v1/users``.

The API tier (``CLAUDE.md`` §6): real routers, real middleware, real error envelope, **no
database**. ``get_user_service`` and ``get_auth_service`` are both redirected at real
services sitting on one shared :class:`tests.helpers.FakeUserRepo`, so a registration made
through the API is genuinely there for the login that follows — which is what lets the
whole sign-up → sign-in → ``/me`` loop be tested at unit speed with Docker stopped.

Overriding the *factories* rather than monkeypatching anything is the pattern from
``tests/api/test_auth.py``; ``app/deps/`` exposes one ``get_x_service`` per resource
precisely so a route test has a single seam per resource.

**These are the first protected routes in the API**, so this module is also where
``securitySchemes`` in the OpenAPI document gets asserted for the first time — ANV-11's
probe route was mounted by a fixture and never appeared in the schema.

Three defects in the router this replaces are pinned here:
``@router.get('{id}')`` with no leading slash (a route that could never match), a handler
reading ``user_id`` while its decorator declared ``id``, and the absence of ``/me``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import SecretStr

from app.deps.auth import get_auth_service
from app.deps.user import get_user_service
from app.domain.auth import ACCESS_TOKEN_TYPE, create_token
from app.domain.password import FAILED_RULES_DETAIL
from app.services.auth import AuthService
from app.services.user import EMAIL_TAKEN_MESSAGE, USERNAME_TAKEN_MESSAGE, UserService
from app.settings import Settings
from app.utils.security import hash_password
from tests.helpers import FakeUserRepo, StubSession, assert_error_envelope, make_user

SECRET = "api-tier-jwt-secret"
ALGORITHM = "HS256"

#: Satisfies ANV-43's strength policy — a capital, a digit and a symbol — because
#: registration now applies it. ``tests/api/test_auth.py`` deliberately keeps the weak
#: original: nothing re-checks strength at login.
PASSWORD = "Correct-horse-battery1"
PASSWORD_HASH = hash_password(PASSWORD)

#: An account created before ANV-43, whose password the policy would refuse today. It has to
#: keep working: see :class:`TestTheOldPasswordsStillWork`.
LEGACY_PASSWORD = "aaaaaaa"
LEGACY_PASSWORD_HASH = hash_password(LEGACY_PASSWORD)

USERNAME = "stephen1"
EMAIL = "stephen@example.com"

#: 27 characters (inside ANV-8's cap) and 75 bytes (outside bcrypt's). Reaches the service —
#: and satisfies the strength policy, so it is refused for its *length*, which is the point.
MULTIBYTE_PASSWORD = "漢" * 24 + "A1!"

USERS_URL = "/v1/users"
ME_URL = "/v1/users/me"
LOGIN_URL = "/v1/auth/login"


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
def session() -> StubSession:
    return StubSession()


@pytest.fixture
def app(app: FastAPI, settings: Settings, users: FakeUserRepo, session: StubSession) -> FastAPI:
    """The application with both services backed by one in-memory repo.

    Auth is overridden as well as users because ``/me`` and ``/{user_id}`` sit behind
    ``CurrentUser``: without it the guard would try to reach Postgres and every protected
    test would be a database test.
    """
    user_service = UserService(session=session, settings=settings, users=users)  # type: ignore[arg-type]
    auth_service = AuthService(session=session, settings=settings, users=users)  # type: ignore[arg-type]
    app.dependency_overrides[get_user_service] = lambda: user_service
    app.dependency_overrides[get_auth_service] = lambda: auth_service
    return app


def mint(subject: uuid.UUID, *, token_type: str = ACCESS_TOKEN_TYPE) -> str:
    return create_token(
        subject=subject,
        token_type=token_type,  # type: ignore[arg-type]
        now=datetime.now(UTC),
        lifetime=timedelta(minutes=30),
        secret=SECRET,
        algorithm=ALGORITHM,
    )


def bearer(subject: uuid.UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint(subject)}"}


def new_registration(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "username": "newperson",
        "email": "new.person@example.com",
        "password": PASSWORD,
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------------------
# POST /v1/users
# ---------------------------------------------------------------------------------------


class TestRegister:
    async def test_a_valid_registration_is_a_201(self, client: AsyncClient) -> None:
        response = await client.post(USERS_URL, json=new_registration())

        assert response.status_code == 201
        body = response.json()
        assert set(body) == {"user_id", "username", "email", "created_at"}
        assert body["username"] == "newperson"
        assert body["email"] == "new.person@example.com"
        uuid.UUID(body["user_id"])

    async def test_the_response_carries_no_password_of_any_kind(self, client: AsyncClient) -> None:
        """Neither the field, nor the plaintext, nor the digest that replaced it."""
        response = await client.post(USERS_URL, json=new_registration())

        assert "password" not in response.json()
        assert PASSWORD not in response.text
        assert "$2b$" not in response.text

    async def test_registration_needs_no_credentials(self, client: AsyncClient) -> None:
        """It is how the first token becomes obtainable at all."""
        response = await client.post(USERS_URL, json=new_registration())

        assert response.status_code == 201

    async def test_a_duplicate_email_is_a_409(self, client: AsyncClient) -> None:
        response = await client.post(USERS_URL, json=new_registration(email=EMAIL))

        error = assert_error_envelope(response, status=409, code="conflict")
        assert error["message"] == EMAIL_TAKEN_MESSAGE
        assert error["details"] == {"resource": "user", "field": "email"}

    async def test_a_duplicate_username_is_a_409(self, client: AsyncClient) -> None:
        response = await client.post(USERS_URL, json=new_registration(username=USERNAME))

        error = assert_error_envelope(response, status=409, code="conflict")
        assert error["message"] == USERNAME_TAKEN_MESSAGE
        assert error["details"] == {"resource": "user", "field": "username"}

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("username", "short"),
            ("email", "not-an-email"),
            ("password", "abc"),
            ("password", "x" * 73),
        ],
    )
    async def test_invalid_input_is_a_422(
        self, client: AsyncClient, field: str, value: str
    ) -> None:
        response = await client.post(USERS_URL, json=new_registration(**{field: value}))

        assert_error_envelope(response, status=422, code="validation_error")

    async def test_a_missing_field_is_a_422(self, client: AsyncClient) -> None:
        response = await client.post(USERS_URL, json={"username": "newperson"})

        assert_error_envelope(response, status=422, code="validation_error")

    async def test_a_multibyte_password_past_bcrypts_limit_is_a_422_not_a_500(
        self, client: AsyncClient
    ) -> None:
        """25 characters, 75 bytes: past the schema and into the service.

        This is the whole reason ``UserService`` catches ``PasswordTooLongError`` — without
        the translation it escapes as a bare ``ValueError`` and the caller gets a 500 for
        input the API should simply have refused.
        """
        response = await client.post(USERS_URL, json=new_registration(password=MULTIBYTE_PASSWORD))

        error = assert_error_envelope(response, status=422, code="validation_error")
        assert error["details"]["field"] == "password"


# ---------------------------------------------------------------------------------------
# POST /v1/users — the strength policy (ANV-43)
# ---------------------------------------------------------------------------------------


class TestRegisterRefusesAWeakPassword:
    """The gap this ticket closed: the API used to enforce length and nothing else.

    ANV-30's four rules were the *only* place the policy lived, so anything that was not our
    browser form — ``curl``, the ``/docs`` page, a future mobile client — could register
    ``aaaaaaa``. These tests are the ones that fail if the policy is ever taken back out of
    the service, which is why they go through the real router and the real middleware rather
    than calling the domain function.
    """

    async def test_seven_lowercase_letters_are_a_422(self, client: AsyncClient) -> None:
        response = await client.post(USERS_URL, json=new_registration(password=LEGACY_PASSWORD))

        assert_error_envelope(response, status=422, code="validation_error")

    async def test_the_response_names_the_rules_that_failed(self, client: AsyncClient) -> None:
        """``details`` is the machine-readable half — a client renders per-rule messages."""
        response = await client.post(USERS_URL, json=new_registration(password=LEGACY_PASSWORD))

        error = assert_error_envelope(response, status=422, code="validation_error")
        assert error["details"] == {
            "field": "password",
            FAILED_RULES_DETAIL: ["uppercase", "number", "symbol"],
        }

    async def test_the_message_names_them_too(self, client: AsyncClient) -> None:
        """For ``curl`` and the Swagger page, which have no rule list to light up."""
        response = await client.post(USERS_URL, json=new_registration(password=LEGACY_PASSWORD))

        error = assert_error_envelope(response, status=422, code="validation_error")
        assert error["message"] == "Password needs an uppercase letter, a number and a symbol."

    @pytest.mark.parametrize(
        ("password", "expected"),
        [
            ("password1!", ["uppercase"]),
            ("Password!!", ["number"]),
            ("Password11", ["symbol"]),
            ("password11", ["uppercase", "symbol"]),
        ],
    )
    async def test_each_rule_is_reported_on_its_own(
        self, client: AsyncClient, password: str, expected: list[str]
    ) -> None:
        response = await client.post(USERS_URL, json=new_registration(password=password))

        error = assert_error_envelope(response, status=422, code="validation_error")
        assert error["details"][FAILED_RULES_DETAIL] == expected

    async def test_the_refusal_never_echoes_the_password(self, client: AsyncClient) -> None:
        response = await client.post(USERS_URL, json=new_registration(password=LEGACY_PASSWORD))

        assert LEGACY_PASSWORD not in response.text

    async def test_a_non_ascii_capital_and_symbol_are_accepted(self, client: AsyncClient) -> None:
        """``ÄNDERUNG1€`` — the password ``validator`` refused for having neither.

        A server that reintroduced ``[A-Z]`` and a punctuation list would 422 here while the
        sign-up form said the password was fine, which is worse than having no server rule.
        """
        response = await client.post(USERS_URL, json=new_registration(password="ÄNDERUNG1€"))

        assert response.status_code == 201

    async def test_nothing_is_created(self, client: AsyncClient) -> None:
        """The refusal is a refusal, not a warning: the account must not exist afterwards."""
        await client.post(USERS_URL, json=new_registration(password=LEGACY_PASSWORD))

        login = await client.post(
            LOGIN_URL, data={"username": "newperson", "password": LEGACY_PASSWORD}
        )

        assert_error_envelope(login, status=401, code="unauthorized")


class TestTheOldPasswordsStillWork:
    """The policy applies when a password is **chosen**, never when one is verified.

    Every account in the legacy database predates ANV-43, so a login-time strength check
    would lock out exactly the people who did nothing wrong. This is the test that fails if
    somebody adds one — see ``app/domain/password.py``'s module docstring.
    """

    @pytest.fixture
    def legacy(self, users: FakeUserRepo) -> Any:
        return users.add(
            make_user(
                username="oldtimer",
                email="old.timer@example.com",
                password_hash=LEGACY_PASSWORD_HASH,
            )
        )

    async def test_an_account_whose_password_the_policy_would_refuse_can_sign_in(
        self, client: AsyncClient, legacy: Any
    ) -> None:
        login = await client.post(
            LOGIN_URL, data={"username": "oldtimer", "password": LEGACY_PASSWORD}
        )

        assert login.status_code == 200
        assert login.json()["access_token"]

    async def test_and_can_still_read_its_own_account(
        self, client: AsyncClient, legacy: Any
    ) -> None:
        login = await client.post(
            LOGIN_URL, data={"username": "oldtimer", "password": LEGACY_PASSWORD}
        )
        token = login.json()["access_token"]

        me = await client.get(ME_URL, headers={"Authorization": f"Bearer {token}"})

        assert me.status_code == 200
        assert me.json()["username"] == "oldtimer"

    async def test_while_the_same_password_cannot_be_chosen_today(
        self, client: AsyncClient
    ) -> None:
        """Both halves in one place, so the asymmetry is deliberate rather than accidental."""
        response = await client.post(USERS_URL, json=new_registration(password=LEGACY_PASSWORD))

        assert response.status_code == 422


# ---------------------------------------------------------------------------------------
# GET /v1/users/me
# ---------------------------------------------------------------------------------------


class TestCurrentUser:
    async def test_an_anonymous_caller_is_refused(self, client: AsyncClient) -> None:
        response = await client.get(ME_URL)

        assert_error_envelope(response, status=401, code="unauthorized")
        assert response.headers.get("www-authenticate") == "Bearer"

    async def test_a_bearer_token_gets_its_own_account(
        self, client: AsyncClient, account: Any
    ) -> None:
        response = await client.get(ME_URL, headers=bearer(account.user_id))

        assert response.status_code == 200
        body = response.json()
        assert body["user_id"] == str(account.user_id)
        assert body["username"] == USERNAME
        assert body["email"] == EMAIL

    async def test_me_never_returns_a_password(self, client: AsyncClient, account: Any) -> None:
        response = await client.get(ME_URL, headers=bearer(account.user_id))

        assert "password" not in response.json()
        assert "$2b$" not in response.text

    async def test_a_garbage_token_is_a_401(self, client: AsyncClient) -> None:
        response = await client.get(ME_URL, headers={"Authorization": "Bearer nonsense"})

        assert_error_envelope(response, status=401, code="invalid_token")

    async def test_a_token_for_a_deleted_account_is_a_401(
        self, client: AsyncClient, account: Any, users: FakeUserRepo
    ) -> None:
        headers = bearer(account.user_id)
        users.remove(account)

        response = await client.get(ME_URL, headers=headers)

        assert_error_envelope(response, status=401, code="unauthorized")

    async def test_me_is_not_parsed_as_a_user_id(self, client: AsyncClient, account: Any) -> None:
        """Declaration order matters: with `/{user_id}` first, "me" would be a 422."""
        response = await client.get(ME_URL, headers=bearer(account.user_id))

        assert response.status_code == 200


# ---------------------------------------------------------------------------------------
# GET /v1/users/{user_id}
# ---------------------------------------------------------------------------------------


class TestReadUserById:
    async def test_an_anonymous_caller_is_refused(self, client: AsyncClient, account: Any) -> None:
        response = await client.get(f"{USERS_URL}/{account.user_id}")

        assert_error_envelope(response, status=401, code="unauthorized")

    async def test_your_own_id_resolves(self, client: AsyncClient, account: Any) -> None:
        response = await client.get(
            f"{USERS_URL}/{account.user_id}", headers=bearer(account.user_id)
        )

        assert response.status_code == 200
        assert response.json()["user_id"] == str(account.user_id)

    async def test_the_route_matches_at_all(self, client: AsyncClient, account: Any) -> None:
        """The old router declared this as `'{id}'` — no leading slash, so it mounted as
        ``/v1/users{id}`` and no request for ``/v1/users/<uuid>`` ever reached it."""
        response = await client.get(
            f"{USERS_URL}/{account.user_id}", headers=bearer(account.user_id)
        )

        assert response.status_code != 404

    async def test_somebody_elses_account_is_a_404(
        self, client: AsyncClient, account: Any, users: FakeUserRepo
    ) -> None:
        """The old API served any user row — and therefore any email address — to any
        authenticated caller, on an API where anybody can register a token."""
        other = users.add(make_user(username="otherperson", email="other@example.com"))

        response = await client.get(f"{USERS_URL}/{other.user_id}", headers=bearer(account.user_id))

        assert_error_envelope(response, status=404, code="not_found")
        assert "other@example.com" not in response.text

    async def test_a_stranger_and_a_ghost_answer_identically(
        self, client: AsyncClient, account: Any, users: FakeUserRepo
    ) -> None:
        """403 would confirm the account exists; the two must be one answer."""
        other = users.add(make_user(username="otherperson", email="other@example.com"))
        nobody = uuid.uuid4()

        stranger = await client.get(f"{USERS_URL}/{other.user_id}", headers=bearer(account.user_id))
        ghost = await client.get(f"{USERS_URL}/{nobody}", headers=bearer(account.user_id))

        assert stranger.status_code == ghost.status_code == 404
        stranger_error = assert_error_envelope(stranger, status=404)
        ghost_error = assert_error_envelope(ghost, status=404)
        assert stranger_error["code"] == ghost_error["code"]
        assert set(stranger_error["details"]) == set(ghost_error["details"])
        # Only the id the caller themselves put in the URL differs.
        assert stranger_error["message"].replace(str(other.user_id), "?") == ghost_error[
            "message"
        ].replace(str(nobody), "?")

    async def test_a_non_uuid_id_is_a_422(self, client: AsyncClient, account: Any) -> None:
        response = await client.get(f"{USERS_URL}/not-a-uuid", headers=bearer(account.user_id))

        assert_error_envelope(response, status=422, code="validation_error")


# ---------------------------------------------------------------------------------------
# The loop this epic finally closes
# ---------------------------------------------------------------------------------------


class TestRegisterThenSignIn:
    async def test_a_freshly_registered_account_can_log_in_and_read_itself(
        self, client: AsyncClient
    ) -> None:
        """Register → login → ``/me``, through the real routers and the real bcrypt.

        The first time the full loop exists in Anvex. It is a genuine round trip: the
        password is hashed by ``UserService`` and verified by ``AuthService``, so a
        mismatch anywhere in the hashing path fails here rather than in production.
        """
        registered = await client.post(USERS_URL, json=new_registration())
        assert registered.status_code == 201

        login = await client.post(LOGIN_URL, data={"username": "newperson", "password": PASSWORD})
        assert login.status_code == 200
        token = login.json()["access_token"]

        me = await client.get(ME_URL, headers={"Authorization": f"Bearer {token}"})

        assert me.status_code == 200
        assert me.json()["user_id"] == registered.json()["user_id"]
        assert me.json()["email"] == "new.person@example.com"

    async def test_the_email_address_works_as_the_login_identifier_too(
        self, client: AsyncClient
    ) -> None:
        await client.post(USERS_URL, json=new_registration())

        login = await client.post(
            LOGIN_URL, data={"username": "new.person@example.com", "password": PASSWORD}
        )

        assert login.status_code == 200

    async def test_the_old_password_of_a_different_account_does_not_work(
        self, client: AsyncClient
    ) -> None:
        await client.post(USERS_URL, json=new_registration(password="A-different-one1"))

        login = await client.post(LOGIN_URL, data={"username": "newperson", "password": PASSWORD})

        assert_error_envelope(login, status=401, code="unauthorized")


# ---------------------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------------------


class TestRouterWiring:
    def test_the_three_routes_are_mounted_under_the_version_prefix(self, app: FastAPI) -> None:
        paths = app.openapi()["paths"]

        assert set(paths[USERS_URL]) == {"post"}
        assert set(paths[ME_URL]) == {"get"}
        assert set(paths[f"{USERS_URL}/{{user_id}}"]) == {"get"}

    def test_registration_is_documented_as_a_201(self, app: FastAPI) -> None:
        responses = app.openapi()["paths"][USERS_URL]["post"]["responses"]

        assert "201" in responses
        assert "200" not in responses

    def test_the_path_parameter_is_named_user_id(self, app: FastAPI) -> None:
        """The old handler declared `{id}` and read `user_id`, so its 404 interpolated the
        builtin ``id`` function into the message."""
        operation = app.openapi()["paths"][f"{USERS_URL}/{{user_id}}"]["get"]
        names = [parameter["name"] for parameter in operation["parameters"]]

        assert names == ["user_id"]

    def test_the_first_protected_route_puts_security_schemes_in_the_document(
        self, app: FastAPI
    ) -> None:
        """Before ANV-12 the document had no ``securitySchemes`` at all — nothing was
        guarded, so Swagger's *Authorize* button had nothing to offer."""
        document = app.openapi()

        schemes = document["components"]["securitySchemes"]
        assert "OAuth2PasswordBearer" in schemes
        assert schemes["OAuth2PasswordBearer"]["type"] == "oauth2"
        assert schemes["OAuth2PasswordBearer"]["flows"]["password"]["tokenUrl"] == "v1/auth/login"

    def test_the_guarded_routes_declare_security_and_the_public_one_does_not(
        self, app: FastAPI
    ) -> None:
        paths = app.openapi()["paths"]

        assert "security" in paths[ME_URL]["get"]
        assert "security" in paths[f"{USERS_URL}/{{user_id}}"]["get"]
        assert "security" not in paths[USERS_URL]["post"]

    def test_the_service_factory_wires_the_request_session_and_the_shared_repo(
        self, settings: Settings
    ) -> None:
        """The real ``get_user_service`` — the one every test above overrides away."""
        from app.deps.user import get_user_service as factory
        from app.repos.user import user_repo

        stub = StubSession()

        service = factory(stub, settings)  # type: ignore[arg-type]

        assert isinstance(service, UserService)
        assert service.session is stub
        assert service.settings is settings
        assert service.users is user_repo
