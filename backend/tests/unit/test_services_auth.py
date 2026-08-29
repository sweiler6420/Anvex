"""Unit tests for ``app/services/auth.py`` — the service's own logic, no database.

The fast tier (``CLAUDE.md`` §6). :class:`tests.helpers.FakeUserRepo` stands in for the
repo, so every branch below runs in microseconds-plus-bcrypt and keeps running with Docker
stopped. What is *not* tested here is SQL: that ``get_by_email_or_username`` really matches
both columns is proved in ``tests/integration/test_repos_user.py`` against real Postgres.

Four properties are being pinned, and they are different properties:

1. **Login is not an oracle.** An unknown identifier and a wrong password produce the same
   exception class, the same ``code``, the same ``message`` and the same ``details``, and
   both pay the same bcrypt cost.
2. **Refresh rotates and re-reads.** A new pair every time, and the account is loaded from
   the repo rather than believed from the claims — so a deleted user stops refreshing.
3. **Recovery is indistinguishable.** Byte-identical response whether or not the account
   exists, and *nothing is sent*, because there is no mail client to send it with.
4. **The clock is read exactly once per operation**, asserted by counting the reads.

Bcrypt is genuinely slow (~100 ms a call by design), so the one real hash below is computed
once at import and reused.
"""

from __future__ import annotations

import ast
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from app.domain import auth as domain_auth
from app.domain.auth import (
    ACCESS_TOKEN_TYPE,
    REFRESH_TOKEN_TYPE,
    ExpiredTokenError,
    TokenError,
    WrongTokenTypeError,
    create_token,
    decode_access_token,
    decode_refresh_token,
)
from app.domain.errors import AnvexError, UnauthorizedError
from app.schemas.auth import RECOVERY_MESSAGE, RecoveryAccepted, TokenPair
from app.services import auth as auth_service_module
from app.services.auth import INVALID_CREDENTIALS_MESSAGE, AuthService
from app.settings import Settings
from app.utils.security import hash_password
from tests.helpers import FakeUserRepo, make_user

PASSWORD = "correct-horse-battery"
WRONG_PASSWORD = "incorrect-horse-battery"

#: One real bcrypt digest, computed once. Hashing per test would dominate the runtime of
#: the whole unit tier.
PASSWORD_HASH = hash_password(PASSWORD)

SECRET = "unit-test-jwt-secret"
ALGORITHM = "HS256"
ACCESS_MINUTES = 30
REFRESH_MINUTES = 60 * 24 * 7


def build_settings(**overrides: Any) -> Settings:
    """Settings pinned to this module's values, ignoring the developer's ``.env``."""
    values: dict[str, Any] = {
        "jwt_secret_key": SECRET,
        "jwt_algorithm": ALGORITHM,
        "jwt_access_token_expire_minutes": ACCESS_MINUTES,
        "jwt_refresh_token_expire_minutes": REFRESH_MINUTES,
    }
    values.update(overrides)
    return Settings(**values)


def build_service(*users: Any, **overrides: Any) -> tuple[AuthService, FakeUserRepo]:
    """An :class:`AuthService` over an in-memory repo. ``session`` is never touched."""
    repo = FakeUserRepo(*users)
    service = AuthService(session=None, settings=build_settings(**overrides), users=repo)  # type: ignore[arg-type]
    return service, repo


def registered_user(**overrides: Any) -> Any:
    return make_user(
        username="stephen1",
        email="stephen@example.com",
        password_hash=PASSWORD_HASH,
        **overrides,
    )


def mint(
    subject: uuid.UUID,
    *,
    token_type: str = REFRESH_TOKEN_TYPE,
    now: datetime | None = None,
    lifetime: timedelta = timedelta(minutes=REFRESH_MINUTES),
    secret: str = SECRET,
) -> str:
    """Mint a token directly through the domain, so a test can choose its ``now``."""
    return create_token(
        subject=subject,
        token_type=token_type,  # type: ignore[arg-type]
        now=now or datetime.now(UTC),
        lifetime=lifetime,
        secret=secret,
        algorithm=ALGORITHM,
    )


class CountingClock:
    """A ``datetime`` stand-in that records how many times ``now()`` was called."""

    def __init__(self, moment: datetime) -> None:
        self.moment = moment
        self.reads = 0

    def now(self, tz: Any = None) -> datetime:
        self.reads += 1
        return self.moment


# ---------------------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------------------


class TestLogin:
    async def test_an_email_address_signs_in(self) -> None:
        user = registered_user()
        service, _ = build_service(user)

        pair = await service.login(identifier=user.email, password=PASSWORD)

        assert isinstance(pair, TokenPair)
        assert pair.token_type == "bearer"

    async def test_a_username_signs_in_too(self) -> None:
        """The old API accepted either in `OAuth2PasswordRequestForm.username`."""
        user = registered_user()
        service, _ = build_service(user)

        pair = await service.login(identifier=user.username, password=PASSWORD)

        payload = decode_access_token(
            pair.access_token, now=datetime.now(UTC), secret=SECRET, algorithm=ALGORITHM
        )
        assert payload.sub == user.user_id

    async def test_the_pair_is_one_access_token_and_one_refresh_token(self) -> None:
        user = registered_user()
        service, _ = build_service(user)
        now = datetime.now(UTC)

        pair = await service.login(identifier=user.email, password=PASSWORD)

        access = decode_access_token(pair.access_token, now=now, secret=SECRET, algorithm=ALGORITHM)
        refresh = decode_refresh_token(
            pair.refresh_token, now=now, secret=SECRET, algorithm=ALGORITHM
        )
        assert access.type == ACCESS_TOKEN_TYPE
        assert refresh.type == REFRESH_TOKEN_TYPE
        assert access.sub == refresh.sub == user.user_id

    async def test_the_two_halves_share_one_issued_at(self) -> None:
        """One clock reading per operation, passed down — not two readings."""
        service, _ = build_service(registered_user())

        pair = await service.login(identifier="stephen1", password=PASSWORD)

        now = datetime.now(UTC)
        access = decode_access_token(pair.access_token, now=now, secret=SECRET, algorithm=ALGORITHM)
        refresh = decode_refresh_token(
            pair.refresh_token, now=now, secret=SECRET, algorithm=ALGORITHM
        )
        assert access.iat == refresh.iat

    async def test_lifetimes_come_from_settings(self) -> None:
        service, _ = build_service(
            registered_user(),
            jwt_access_token_expire_minutes=5,
            jwt_refresh_token_expire_minutes=90,
        )

        pair = await service.login(identifier="stephen1", password=PASSWORD)

        now = datetime.now(UTC)
        access = decode_access_token(pair.access_token, now=now, secret=SECRET, algorithm=ALGORITHM)
        refresh = decode_refresh_token(
            pair.refresh_token, now=now, secret=SECRET, algorithm=ALGORITHM
        )
        assert access.exp - access.iat == timedelta(minutes=5)
        assert refresh.exp - refresh.iat == timedelta(minutes=90)

    async def test_an_unknown_identifier_is_refused(self) -> None:
        service, _ = build_service(registered_user())

        with pytest.raises(UnauthorizedError):
            await service.login(identifier="nobody@example.com", password=PASSWORD)

    async def test_a_wrong_password_is_refused(self) -> None:
        user = registered_user()
        service, _ = build_service(user)

        with pytest.raises(UnauthorizedError):
            await service.login(identifier=user.email, password=WRONG_PASSWORD)

    async def test_the_two_failures_are_indistinguishable(self) -> None:
        """The property this whole arrangement exists for.

        Same class, same code, same message, same details — so no caller, and no client
        library, can tell "no such account" from "wrong password".
        """
        service, _ = build_service(registered_user())

        with pytest.raises(UnauthorizedError) as unknown:
            await service.login(identifier="ghost@example.com", password=PASSWORD)
        with pytest.raises(UnauthorizedError) as wrong:
            await service.login(identifier="stephen@example.com", password=WRONG_PASSWORD)

        assert type(unknown.value) is type(wrong.value)
        assert unknown.value.code == wrong.value.code == "unauthorized"
        assert unknown.value.message == wrong.value.message == INVALID_CREDENTIALS_MESSAGE
        assert unknown.value.details == wrong.value.details == {}

    async def test_a_failure_is_never_a_token_error(self) -> None:
        """`token_expired` tells a client to go and refresh; a bad password does not."""
        service, _ = build_service(registered_user())

        with pytest.raises(UnauthorizedError) as caught:
            await service.login(identifier="stephen1", password=WRONG_PASSWORD)

        assert not isinstance(caught.value, TokenError)

    async def test_an_unknown_identifier_still_pays_for_a_hash_comparison(self) -> None:
        """Otherwise response time answers "does this account exist?" for free."""
        service, _ = build_service(registered_user())

        with (
            mock.patch.object(auth_service_module, "verify_password", return_value=False) as verify,
            pytest.raises(UnauthorizedError),
        ):
            await service.login(identifier="ghost@example.com", password=PASSWORD)

        assert verify.call_count == 1, "the miss path skipped bcrypt and leaks timing"

    async def test_the_decoy_hash_can_never_actually_authenticate(self) -> None:
        """A timing equaliser must not become a second way in."""
        service, _ = build_service()

        for candidate in (PASSWORD, "", auth_service_module._ABSENT_USER_PASSWORD_HASH):
            with pytest.raises(UnauthorizedError):
                await service.login(identifier="ghost@example.com", password=candidate)

    async def test_the_lookup_is_the_single_or_statement(self) -> None:
        """One query, not "try email then try username" — two round trips leak timing."""
        service, repo = build_service(registered_user())

        await service.login(identifier="stephen1", password=PASSWORD)

        assert repo.calls == [("get_by_email_or_username", "stephen1")]

    async def test_exactly_one_clock_read(self) -> None:
        clock = CountingClock(datetime.now(UTC))
        service, _ = build_service(registered_user())

        with mock.patch.object(auth_service_module, "datetime", clock):
            await service.login(identifier="stephen1", password=PASSWORD)

        assert clock.reads == 1


# ---------------------------------------------------------------------------------------
# refresh
# ---------------------------------------------------------------------------------------


class TestRefresh:
    async def test_a_refresh_token_yields_a_new_pair(self) -> None:
        user = registered_user()
        service, _ = build_service(user)
        token = mint(user.user_id, now=datetime.now(UTC) - timedelta(minutes=5))

        pair = await service.refresh(refresh_token=token)

        assert isinstance(pair, TokenPair)

    async def test_the_refresh_token_itself_is_rotated(self) -> None:
        """Both halves are re-minted. Rotation is what bounds a stolen refresh token.

        The presented token is deliberately minted a minute in the past so the new one has
        a different ``iat`` — HS256 is deterministic, and two tokens issued in the same
        second for the same subject are byte-identical.
        """
        user = registered_user()
        service, _ = build_service(user)
        old = mint(user.user_id, now=datetime.now(UTC) - timedelta(minutes=1))

        pair = await service.refresh(refresh_token=old)

        assert pair.refresh_token != old
        now = datetime.now(UTC)
        assert (
            decode_refresh_token(
                pair.refresh_token, now=now, secret=SECRET, algorithm=ALGORITHM
            ).sub
            == user.user_id
        )
        assert (
            decode_access_token(pair.access_token, now=now, secret=SECRET, algorithm=ALGORITHM).sub
            == user.user_id
        )

    async def test_an_access_token_is_refused(self) -> None:
        """**The bug this epic exists for.** The old `/v1/refresh` accepted this."""
        user = registered_user()
        service, _ = build_service(user)
        access = mint(user.user_id, token_type=ACCESS_TOKEN_TYPE)

        with pytest.raises(WrongTokenTypeError) as caught:
            await service.refresh(refresh_token=access)

        assert caught.value.code == "wrong_token_type"
        assert caught.value.details == {"expected_type": "refresh", "actual_type": "access"}

    async def test_an_expired_refresh_token_is_refused(self) -> None:
        user = registered_user()
        service, _ = build_service(user)
        stale = mint(
            user.user_id,
            now=datetime.now(UTC) - timedelta(days=30),
            lifetime=timedelta(minutes=1),
        )

        with pytest.raises(ExpiredTokenError):
            await service.refresh(refresh_token=stale)

    async def test_a_token_signed_with_another_key_is_refused(self) -> None:
        user = registered_user()
        service, _ = build_service(user)
        forged = mint(user.user_id, secret="not-the-real-secret")

        with pytest.raises(TokenError):
            await service.refresh(refresh_token=forged)

    async def test_the_account_is_re_read_not_trusted_from_the_claims(self) -> None:
        user = registered_user()
        service, repo = build_service(user)
        token = mint(user.user_id)
        repo.calls.clear()

        await service.refresh(refresh_token=token)

        assert repo.calls == [("get_by_id", user.user_id)]

    async def test_a_deleted_account_cannot_keep_refreshing(self) -> None:
        """A token stays cryptographically valid for a week after the row is gone."""
        user = registered_user()
        service, repo = build_service(user)
        token = mint(user.user_id)
        repo.remove(user)

        with pytest.raises(UnauthorizedError) as caught:
            await service.refresh(refresh_token=token)

        assert not isinstance(caught.value, TokenError)

    async def test_exactly_one_clock_read(self) -> None:
        user = registered_user()
        service, _ = build_service(user)
        token = mint(user.user_id)
        clock = CountingClock(datetime.now(UTC))

        with mock.patch.object(auth_service_module, "datetime", clock):
            await service.refresh(refresh_token=token)

        assert clock.reads == 1


# ---------------------------------------------------------------------------------------
# authenticate (what `get_current_user` runs)
# ---------------------------------------------------------------------------------------


class TestAuthenticate:
    async def test_an_access_token_resolves_to_its_account(self) -> None:
        user = registered_user()
        service, _ = build_service(user)

        resolved = await service.authenticate(mint(user.user_id, token_type=ACCESS_TOKEN_TYPE))

        assert resolved is user

    async def test_a_refresh_token_is_refused(self) -> None:
        """The mirror image of the refresh hole: a long-lived token must not open doors."""
        user = registered_user()
        service, _ = build_service(user)

        with pytest.raises(WrongTokenTypeError) as caught:
            await service.authenticate(mint(user.user_id, token_type=REFRESH_TOKEN_TYPE))

        assert caught.value.details == {"expected_type": "access", "actual_type": "refresh"}

    @pytest.mark.parametrize("token", [None, "", "not-a-jwt", "a.b.c"])
    async def test_a_missing_or_malformed_token_is_refused(self, token: str | None) -> None:
        service, _ = build_service(registered_user())

        with pytest.raises(TokenError):
            await service.authenticate(token)

    async def test_a_deleted_account_stops_authenticating_immediately(self) -> None:
        user = registered_user()
        service, repo = build_service(user)
        token = mint(user.user_id, token_type=ACCESS_TOKEN_TYPE)
        repo.remove(user)

        with pytest.raises(UnauthorizedError):
            await service.authenticate(token)

    async def test_exactly_one_clock_read(self) -> None:
        user = registered_user()
        service, _ = build_service(user)
        token = mint(user.user_id, token_type=ACCESS_TOKEN_TYPE)
        clock = CountingClock(datetime.now(UTC))

        with mock.patch.object(auth_service_module, "datetime", clock):
            await service.authenticate(token)

        assert clock.reads == 1


# ---------------------------------------------------------------------------------------
# recovery
# ---------------------------------------------------------------------------------------


class TestRecovery:
    async def test_an_existing_account_gets_the_fixed_response(self) -> None:
        user = registered_user()
        service, _ = build_service(user)

        result = await service.recovery(username=user.username)

        assert isinstance(result, RecoveryAccepted)
        assert result.status == "accepted"
        assert result.message == RECOVERY_MESSAGE

    async def test_an_unknown_account_does_not_raise(self) -> None:
        """The old endpoint answered 404 with the username echoed back."""
        service, _ = build_service(registered_user())

        result = await service.recovery(username="nobody-at-all")

        assert isinstance(result, RecoveryAccepted)

    async def test_the_two_responses_are_byte_identical(self) -> None:
        """The property: recovery must not be a username-enumeration API."""
        user = registered_user()
        service, _ = build_service(user)

        existing = await service.recovery(username=user.username)
        missing = await service.recovery(username="nobody-at-all")

        assert existing.model_dump_json() == missing.model_dump_json()

    async def test_nothing_is_actually_sent(self) -> None:
        """An honest no-op: there is no mail client in the repo, so nothing is delivered.

        Asserted structurally rather than by mocking a mailer, because a mailer would be
        the thing being asserted into existence. If ``app/clients/`` grows one and this
        service starts calling it, this test fails and should be rewritten to assert the
        send — which is exactly the moment to notice.
        """
        source = Path(auth_service_module.__file__).read_text(encoding="utf-8")
        assert "TODO" in source, "the missing delivery step must stay visible in the code"

        service, repo = build_service(registered_user())
        await service.recovery(username="stephen1")

        assert [name for name, _ in repo.calls] == ["get_by_username"]

    async def test_exactly_one_clock_read(self) -> None:
        clock = CountingClock(datetime.now(UTC))
        service, _ = build_service(registered_user())

        with mock.patch.object(auth_service_module, "datetime", clock):
            await service.recovery(username="stephen1")

        assert clock.reads == 1


# ---------------------------------------------------------------------------------------
# layering
# ---------------------------------------------------------------------------------------


def service_tree() -> ast.Module:
    return ast.parse(Path(auth_service_module.__file__).read_text(encoding="utf-8"))


class TestLayering:
    """`CLAUDE.md` §3, checked rather than trusted — prose conventions get broken."""

    def test_the_service_imports_no_web_framework(self) -> None:
        modules: set[str] = set()
        for node in ast.walk(service_tree()):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                modules.add(node.module)
        roots = {module.split(".")[0] for module in modules}

        assert "fastapi" not in roots
        assert "starlette" not in roots
        assert "app.api" not in modules
        assert "app.deps" not in modules

    def test_the_service_raises_no_http_exception(self) -> None:
        """Parsed, not grepped, so the docstring explaining the rule does not trip it."""
        used = {
            node.id if isinstance(node, ast.Name) else node.attr
            for node in ast.walk(service_tree())
            if isinstance(node, ast.Name | ast.Attribute)
        }
        assert "HTTPException" not in used

    def test_every_error_it_raises_is_a_domain_error(self) -> None:
        raised = {
            node.exc.func.id
            for node in ast.walk(service_tree())
            if isinstance(node, ast.Raise)
            and isinstance(node.exc, ast.Call)
            and isinstance(node.exc.func, ast.Name)
        }
        for name in raised:
            assert issubclass(getattr(auth_service_module, name), AnvexError), name

    def test_no_sqlalchemy_query_is_written_here(self) -> None:
        """`CLAUDE.md` §3: if you typed `select(` outside `app/repos/`, it is the wrong file."""
        source = Path(auth_service_module.__file__).read_text(encoding="utf-8")
        assert "select(" not in source

    def test_the_domain_never_learns_about_settings(self) -> None:
        """The service unwraps `SecretStr`; `app/domain/auth.py` takes a plain `str`."""
        source = Path(domain_auth.__file__).read_text(encoding="utf-8")
        assert "get_secret_value" not in source
