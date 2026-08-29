"""The API tier's use of the harness — the shape every route test from ANV-11 will copy.

A ``tests/api/`` test asserts the **route contract**: status code, response shape, auth
enforcement, validation. It does not touch Postgres. Whatever the handler depends on is
replaced through ``app.dependency_overrides``, which is safe precisely because the ``app``
fixture builds a fresh application per test, so an override cannot leak into the next one.

The database-backed variant (``db_client``) exists but is the exception — see
``tests/integration/test_harness.py``.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.settings import Settings
from tests.conftest import ALLOWED_ORIGIN
from tests.helpers import (
    ERROR_BODY_KEYS,
    StubSession,
    assert_error_envelope,
    override_session,
)


class TestDependencyOverrides:
    """`override_session` + `StubSession` is how a session-taking route is contract-tested."""

    async def test_a_stubbed_session_keeps_the_route_off_postgres(
        self, app: FastAPI, client: AsyncClient
    ) -> None:
        session = override_session(app)

        response = await client.get("/health/ready")

        assert response.status_code == 200
        assert [str(statement) for statement in session.statements] == ["SELECT 1"]

    async def test_the_failure_branch_is_reachable_without_a_broken_database(
        self, app: FastAPI, client: AsyncClient
    ) -> None:
        override_session(app, StubSession(error=OSError("connection refused")))

        error = assert_error_envelope(await client.get("/health/ready"), status=503)

        assert error["code"] == "service_unavailable"

    async def test_overrides_do_not_leak_into_the_next_test(self, app: FastAPI) -> None:
        """`app` is function-scoped, so every test starts from an empty override map.

        If this ever fails, the `app` fixture has been widened to module or session scope
        and every dependency-override test in the suite has become order-dependent.
        """
        assert app.dependency_overrides == {}


class TestSettingsOverride:
    """A module pins configuration by *overriding* the `settings` fixture, not replacing it.

    The override receives the harness's own `settings` and copies it, so the pinned CORS
    origins and quiet log level stay in force and only the one field under test changes.
    """

    @pytest.fixture
    def settings(self, settings: Settings) -> Settings:
        return settings.model_copy(update={"api_cors_origins": "http://pinned.example"})

    async def test_the_app_is_built_from_the_overridden_settings(self, client: AsyncClient) -> None:
        response = await client.get("/health", headers={"Origin": "http://pinned.example"})
        assert response.headers["access-control-allow-origin"] == "http://pinned.example"

    async def test_the_default_origin_is_no_longer_allowed(self, client: AsyncClient) -> None:
        response = await client.get("/health", headers={"Origin": ALLOWED_ORIGIN})
        assert "access-control-allow-origin" not in response.headers


class TestErrorEnvelopeHelper:
    """`assert_error_envelope` is the one place the error contract is spelled out."""

    async def test_it_accepts_a_real_error_response(self, client: AsyncClient) -> None:
        error = assert_error_envelope(
            await client.get("/no-such-route"), status=404, code="not_found"
        )
        assert error["details"] == {}

    async def test_it_returns_the_error_object_for_further_assertions(
        self, client: AsyncClient
    ) -> None:
        error = assert_error_envelope(await client.post("/health"), status=405)
        assert error["code"] == "method_not_allowed"
        assert set(error) == set(ERROR_BODY_KEYS)

    async def test_it_rejects_a_success_response(self, client: AsyncClient) -> None:
        """Guards against a test that "passes" because the endpoint stopped failing."""
        with pytest.raises(AssertionError):
            assert_error_envelope(await client.get("/health"))

    async def test_it_rejects_a_wrong_code(self, client: AsyncClient) -> None:
        with pytest.raises(AssertionError):
            assert_error_envelope(await client.get("/no-such-route"), code="conflict")
