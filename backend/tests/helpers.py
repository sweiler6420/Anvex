"""Assertions and stubs shared across the test tiers.

Chiefly the error-envelope contract. ``CLAUDE.md`` §4 makes the four-key body a public API
contract, and every ticket from ANV-11 onward asserts it, so the keys are spelled out in
exactly one place: if the envelope ever changes, one constant changes and every test that
depends on it fails loudly rather than drifting.

Also home to :class:`StubSession`, because "override ``get_session`` with something that
does not touch Postgres" is what an ``tests/api/`` test does whenever the route it is
contract-testing happens to take a session.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI
from httpx import Response

from app.deps.session import get_session

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


__all__ = [
    "ERROR_BODY_KEYS",
    "StubSession",
    "assert_error_envelope",
    "override_session",
]
