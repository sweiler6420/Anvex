"""Contract tests for the liveness and readiness probes.

No database is required: the readiness path is exercised through a dependency override
that yields a stub session, so both the healthy and the unreachable case are deterministic
and run in milliseconds.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.deps import get_session


class _StubSession:
    """Minimal stand-in for ``AsyncSession``: records or refuses ``execute``."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> Any:
        self.statements.append(statement)
        if self.error is not None:
            raise self.error
        return None


def _override_session(app: FastAPI, session: _StubSession) -> None:
    async def _get_session() -> AsyncIterator[_StubSession]:
        yield session

    app.dependency_overrides[get_session] = _get_session


class TestLiveness:
    async def test_health_returns_ok(self, client: AsyncClient) -> None:
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    async def test_health_never_touches_the_database(
        self, app: FastAPI, client: AsyncClient
    ) -> None:
        """A liveness probe that depends on Postgres gets containers restart-looped."""
        exploding = _StubSession(error=AssertionError("liveness must not query the database"))
        _override_session(app, exploding)

        response = await client.get("/health")

        assert response.status_code == 200
        assert exploding.statements == []


class TestReadiness:
    async def test_ready_runs_select_1_and_returns_ok(
        self, app: FastAPI, client: AsyncClient
    ) -> None:
        session = _StubSession()
        _override_session(app, session)

        response = await client.get("/health/ready")

        assert response.status_code == 200
        assert response.json() == {"status": "ok", "database": "ok"}
        assert len(session.statements) == 1
        assert "SELECT 1" in str(session.statements[0])

    @pytest.mark.parametrize(
        "failure",
        [OSError("connection refused"), TimeoutError("pool timed out")],
        ids=["connection-refused", "timeout"],
    )
    async def test_ready_returns_503_when_the_database_is_unreachable(
        self, app: FastAPI, client: AsyncClient, failure: Exception
    ) -> None:
        _override_session(app, _StubSession(error=failure))

        response = await client.get("/health/ready")

        assert response.status_code == 503
        error = response.json()["error"]
        assert error["code"] == "service_unavailable"
        assert error["message"] == "The database is unavailable."

    async def test_the_503_body_leaks_no_driver_detail(
        self, app: FastAPI, client: AsyncClient
    ) -> None:
        _override_session(app, _StubSession(error=OSError("password authentication failed")))

        response = await client.get("/health/ready")

        assert "password" not in response.text
        assert response.json()["error"]["details"] == {}


class TestDocs:
    """`uv run uvicorn app.main:app` must serve the docs, not just the endpoints."""

    async def test_openapi_and_docs_are_served(self, client: AsyncClient) -> None:
        assert (await client.get("/docs")).status_code == 200

        schema = await client.get("/openapi.json")
        assert schema.status_code == 200
        paths = schema.json()["paths"]
        assert "/health" in paths
        assert "/health/ready" in paths
