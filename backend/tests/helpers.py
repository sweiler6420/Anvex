"""Assertions and stubs shared across the test tiers.

Chiefly the error-envelope contract. ``CLAUDE.md`` §4 makes the four-key body a public API
contract, and every ticket from ANV-11 onward asserts it, so the keys are spelled out in
exactly one place: if the envelope ever changes, one constant changes and every test that
depends on it fails loudly rather than drifting.

Also home to the fakes that let a layer be tested without the layer below it:
:class:`StubSession`, because "override ``get_session`` with something that does not touch
Postgres" is what a ``tests/api/`` test does whenever the route it is contract-testing
happens to take a session; and :class:`FakeUserRepo` plus :func:`make_user`, because a
service's own logic is worth testing at unit speed against an in-memory repo rather than
only through a database.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from httpx import Response

from app.deps.session import get_session
from app.models import User

#: Every key ``app.schemas.errors.ErrorResponse`` promises, always present.
ERROR_BODY_KEYS = frozenset({"code", "message", "details", "request_id"})


def assert_error_envelope(
    response: Response,
    *,
    status: int | None = None,
    code: str | None = None,
) -> dict[str, Any]:
    """Assert ``response`` is a well-formed Anvex error and return its ``error`` object.

    Checks the whole contract, not just the keys: ``details`` is a dict (never ``null``,
    so a client indexes it unconditionally) and ``code``/``message`` are non-empty strings.
    Pass ``status`` and/or ``code`` to pin the specific failure as well.

    Returns the inner ``error`` object so a caller can go on to assert on ``details``::

        error = assert_error_envelope(response, status=404, code="not_found")
        assert error["details"]["resource"] == "stock"
    """
    if status is not None:
        assert response.status_code == status, response.text
    assert response.status_code >= 400, f"expected an error response, got {response.status_code}"

    payload = response.json()
    assert set(payload) == {"error"}, payload
    error = payload["error"]
    assert set(error) == set(ERROR_BODY_KEYS), error

    assert isinstance(error["code"], str) and error["code"]
    assert isinstance(error["message"], str) and error["message"]
    assert isinstance(error["details"], dict)
    assert isinstance(error["request_id"], str) and error["request_id"]

    if code is not None:
        assert error["code"] == code, error

    return error


class StubSession:
    """Minimal stand-in for ``AsyncSession``: records ``execute`` calls, or refuses them.

    A stub, not a mock — it answers and records, which is all a route contract test needs.
    Anything that cares what the SQL *did* belongs in ``tests/integration/`` against
    ``db_session`` and a real database.

    Pass ``error`` to make every ``execute`` raise, which is how the failure branch of a
    handler is tested without breaking an actual database::

        session = override_session(app, StubSession(error=OSError("connection refused")))
    """

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        #: Every statement passed to :meth:`execute`, in order.
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> Any:
        self.statements.append(statement)
        if self.error is not None:
            raise self.error
        return None


def override_session(app: FastAPI, session: StubSession | None = None) -> StubSession:
    """Point ``app``'s ``get_session`` dependency at ``session`` and return it.

    Safe because the ``app`` fixture builds a fresh application per test, so the override
    map starts empty every time and cannot leak into the next test.
    """
    session = session if session is not None else StubSession()
    stub = session

    async def _get_session() -> AsyncIterator[StubSession]:
        yield stub

    app.dependency_overrides[get_session] = _get_session
    return stub


class FakeUserRepo:
    """An in-memory stand-in for :class:`app.repos.user.UserRepo`.

    Lets a service be exercised for real — its branches, its error semantics, the tokens it
    actually mints — with no Postgres anywhere, which is what keeps the service tests in the
    fast tier and keeps them running with Docker stopped.

    It implements only the lookups a service calls, and it deliberately re-implements the
    *behaviour* of the real queries rather than pretending to be them: the login lookup
    matches email **or** username, exactly as ``get_by_email_or_username``'s single ``OR``
    statement does. Anything asserting what the SQL *did* belongs in ``tests/integration/``
    against a real database.

    ``calls`` records ``(method, argument)`` in order, so a test can assert that (say)
    refresh really re-read the account rather than trusting the token's claims.
    """

    def __init__(self, *users: User) -> None:
        self.users: list[User] = list(users)
        self.calls: list[tuple[str, Any]] = []

    def add(self, user: User) -> User:
        self.users.append(user)
        return user

    def remove(self, user: User) -> None:
        """Delete an account, for the "the token outlived its user" tests."""
        self.users = [candidate for candidate in self.users if candidate is not user]

    async def get_by_id(self, session: Any, user_id: uuid.UUID) -> User | None:
        self.calls.append(("get_by_id", user_id))
        return self._first(lambda user: user.user_id == user_id)

    async def get_by_username(self, session: Any, username: str) -> User | None:
        self.calls.append(("get_by_username", username))
        return self._first(lambda user: user.username == username)

    async def get_by_email(self, session: Any, email: str) -> User | None:
        self.calls.append(("get_by_email", email))
        return self._first(lambda user: user.email == email)

    async def get_by_email_or_username(self, session: Any, identifier: str) -> User | None:
        self.calls.append(("get_by_email_or_username", identifier))
        return self._first(
            lambda user: identifier in (user.email, user.username),
        )

    def _first(self, predicate: Callable[[User], bool]) -> User | None:
        return next((user for user in self.users if predicate(user)), None)


def make_user(
    *,
    username: str = "testuser",
    email: str = "test@example.com",
    password_hash: str = "",
    user_id: uuid.UUID | None = None,
) -> User:
    """Build a detached :class:`~app.models.User` for a fake repo.

    Not :class:`tests.factories.UserFactory`: that one flushes to a session, which is the
    whole thing these tests are avoiding. ``user_id`` and ``created_at`` are normally server
    defaults, so they are filled in here — a detached instance never sees Postgres.
    """
    return User(
        user_id=user_id or uuid.uuid4(),
        username=username,
        email=email,
        password=password_hash,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


__all__ = [
    "ERROR_BODY_KEYS",
    "FakeUserRepo",
    "StubSession",
    "assert_error_envelope",
    "make_user",
    "override_session",
]
