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
    """Minimal stand-in for ``AsyncSession``: records what it was asked to do.

    A stub, not a mock — it answers and records, which is all a route contract test needs.
    Anything that cares what the SQL *did* belongs in ``tests/integration/`` against
    ``db_session`` and a real database.

    Pass ``error`` to make every ``execute`` raise, which is how the failure branch of a
    handler is tested without breaking an actual database::

        session = override_session(app, StubSession(error=OSError("connection refused")))

    ``commit`` and ``rollback`` are counted rather than ignored. ``CLAUDE.md`` §3 puts the
    transaction boundary in the service, so "did this use case actually commit, and did the
    failing branch roll back instead" is a property worth asserting at unit speed::

        assert session.commits == 1 and session.rollbacks == 0
    """

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        #: Every statement passed to :meth:`execute`, in order.
        self.statements: list[Any] = []
        #: How many times the service closed a transaction, and how many times it abandoned
        #: one. Counters rather than booleans so a double commit is visible too.
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, statement: Any) -> Any:
        self.statements.append(statement)
        if self.error is not None:
            raise self.error
        return None

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


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

    **It does not enforce the unique indexes**, and that is on purpose. ``uq_users_email``
    and ``uq_users_username`` are a database guarantee, proved against real Postgres in
    ``tests/integration/test_repos_user.py``; a fake that re-implemented them would only be
    testing itself. What a fake *can* reproduce is the moment the guarantee fires — set
    :attr:`create_error` to an ``IntegrityError`` and the next :meth:`create` raises it,
    which is the "two sign-ups raced and one lost" path no pre-check can close.
    """

    def __init__(self, *users: User) -> None:
        self.users: list[User] = list(users)
        self.calls: list[tuple[str, Any]] = []
        #: Raised (once, then cleared) by the next :meth:`create`. See the class docstring.
        self.create_error: Exception | None = None

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

    async def email_exists(
        self, session: Any, email: str, *, exclude_user_id: uuid.UUID | None = None
    ) -> bool:
        self.calls.append(("email_exists", email))
        return any(
            user.email == email and user.user_id != exclude_user_id for user in self.users
        )

    async def username_exists(
        self, session: Any, username: str, *, exclude_user_id: uuid.UUID | None = None
    ) -> bool:
        self.calls.append(("username_exists", username))
        return any(
            user.username == username and user.user_id != exclude_user_id for user in self.users
        )

    async def create(
        self, session: Any, *, username: str, email: str, password: str
    ) -> User:
        """Insert an account. ``password`` is the **hash**, exactly as the real repo takes it."""
        self.calls.append(("create", username))
        if self.create_error is not None:
            error, self.create_error = self.create_error, None
            raise error
        return self.add(make_user(username=username, email=email, password_hash=password))

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
