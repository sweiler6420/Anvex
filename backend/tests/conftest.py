"""Shared fixtures.

Deliberately thin: **ANV-6 owns the real test harness** (database fixtures, factories,
transaction rollback). What is here is only what ANV-4's API tests cannot run without —
an app instance built from explicit settings, and an ``AsyncClient`` bound to it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.settings import Settings

#: Fixed origin used by the CORS assertions, so they never depend on the developer's
#: real `.env`.
ALLOWED_ORIGIN = "http://localhost:5173"


@pytest.fixture
def settings() -> Settings:
    """Settings with the values the API tests assert against pinned explicitly.

    Keyword arguments win over both the environment and the `.env` file, which is what
    makes these tests independent of the machine they run on.
    """
    return Settings(api_cors_origins=f"{ALLOWED_ORIGIN},http://127.0.0.1:5173", log_level="WARNING")


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    """A fresh application per test, so dependency overrides cannot leak between tests."""
    return create_app(settings)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """An ``AsyncClient`` speaking ASGI directly to ``app`` — no socket, no server.

    ``raise_app_exceptions=False`` matters: Starlette's ``ServerErrorMiddleware`` sends the
    500 response and then **re-raises** so the ASGI server can log the crash. Without this
    flag the transport would propagate that re-raise into the test and we could never
    assert on the 500 body a real client receives.
    """
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client
