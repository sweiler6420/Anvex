"""The Anvex backend test harness.

Every fixture the suite shares lives here — there is deliberately no second conftest and no
parallel set of database fixtures. Supporting plumbing sits beside it:
:mod:`tests.database` (how the harness reaches and migrates ``db-test``),
:mod:`tests.helpers` (shared assertions) and :mod:`tests.factories` (model builders).

The three tiers (``CLAUDE.md`` §6) and what each may touch:

* ``tests/unit/`` — pure domain and utils. **No fixtures, no I/O.** The fast tier.
* ``tests/api/`` — route contracts through ``client``; services stubbed with
  ``app.dependency_overrides``. No database.
* ``tests/integration/`` — repos and services against real Postgres via ``db_session``;
  clients against mocked HTTP via ``mock_http``.

**Skipping is fixture-driven, not directory-driven.** Any test requesting one of the
``db_*`` fixtures is auto-marked ``db`` and skips with a reason when ``db-test`` does not
answer; a ``respx``-only test in the same directory keeps running. That is what makes
``uv run python -m pytest`` green on a machine with Docker stopped, as ``CLAUDE.md`` §6
requires.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from uuid import uuid4

import pytest
import respx
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.base import SCHEMA
from app.deps.session import get_session
from app.main import create_app
from app.settings import Settings
from tests import database
from tests.factories import reset_randomness

#: Fixed origin used by the CORS assertions, so they never depend on the developer's
#: real `.env`.
ALLOWED_ORIGIN = "http://localhost:5173"

#: Requesting any of these means the test needs a live Postgres. `pytest_collection_
#: modifyitems` turns that into a `db` marker, so `-m "not db"` deselects the whole tier
#: without a single test having to remember to mark itself.
DATABASE_FIXTURES = frozenset(
    {
        "database_available",
        "db_engine",
        "db_connection",
        "db_session",
        "db_app",
        "db_client",
        "scratch_table",
        "throwaway_database_url",
    }
)

#: A committed table the harness creates once per session so the rollback tests have
#: something to write to while `app/models/` is still empty. **ANV-7 deletes this** — real
#: models and their factories replace it.
SCRATCH_TABLE = f'"{SCHEMA}"."harness_scratch"'


# ---------------------------------------------------------------------------------------
# Markers and collection
# ---------------------------------------------------------------------------------------


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "db: needs the compose `db-test` Postgres. Applied automatically to any test that "
        "requests a `db_*` fixture; such a test skips when the database is unreachable.",
    )


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark every database-dependent test `db`, based on the fixtures it asked for."""
    for item in items:
        if DATABASE_FIXTURES & set(getattr(item, "fixturenames", ())):
            item.add_marker("db")


# ---------------------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _deterministic_fakes() -> None:
    """Re-seed faker and reset the factory sequences before every test.

    Autouse and cheap. It makes generated data a function of the test rather than of how
    many tests ran before it, so a failure reproduces from `pytest path::test_name` alone.
    """
    reset_randomness()


# ---------------------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------------------


@pytest.fixture
def settings() -> Settings:
    """Settings with the values the API tests assert against pinned explicitly.

    Keyword arguments win over both the environment and the `.env` file, which is what
    makes these tests independent of the machine they run on. A test module needing a
    different value overrides *this* fixture rather than building a second `Settings`::

        @pytest.fixture
        def settings(settings: Settings) -> Settings:
            return settings.model_copy(update={"jwt_access_token_expire_minutes": 1})
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


# ---------------------------------------------------------------------------------------
# Outbound HTTP (client tests)
# ---------------------------------------------------------------------------------------


@pytest.fixture
def mock_http() -> Iterator[respx.MockRouter]:
    """A ``respx`` router intercepting every outbound ``httpx`` call.

    Use it for anything in ``app/clients/``: ``CLAUDE.md`` §6 forbids a test touching a live
    vendor API, and an un-mocked call would otherwise leave the machine::

        mock_http.get("https://www.alphavantage.co/query").respond(200, json={...})
        quote = await AlphaVantageClient().fetch_quote("AAPL")

    ``assert_all_called=False`` because a shared fixture cannot know which of the routes a
    given test registers it actually intends to exercise. A test that wants that check
    should assert on ``route.call_count`` explicitly, which says what it means.

    ``assert_all_mocked`` stays at its default ``True``: an unmatched request raises rather
    than escaping to the network, and that is the guarantee that actually matters.
    """
    with respx.mock(assert_all_called=False) as router:
        yield router


# ---------------------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------------------


@pytest.fixture(scope="session")
def database_available() -> None:
    """Skip the requesting test unless ``db-test`` accepts a connection.

    Probed once per session (:func:`tests.database.unavailable_reason` is cached), so the
    "Docker is stopped" path costs one refused connection for the whole run.
    """
    reason = database.unavailable_reason()
    if reason is not None:
        pytest.skip(reason)


@pytest.fixture(scope="session")
def db_engine(database_available: None) -> Iterator[AsyncEngine]:
    """A session-wide engine against ``db-test``, with the schema already migrated.

    Two deliberate choices:

    * **The schema comes from the real Alembic migrations**, not
      ``Base.metadata.create_all``. ``db-test`` is tmpfs-backed with no volume, so it starts
      with no ``anvex`` schema and no ``alembic_version`` every time and the harness has to
      build it. Running the migrations means the tests exercise the schema production
      actually gets — which is why ``CLAUDE.md`` §4 bans ``create_all`` outside tests.
      ``upgrade head`` is idempotent, so a container left running between runs is fine.
    * **``NullPool``.** The engine is session-scoped but each test runs in its own event
      loop, and an asyncpg connection belongs to the loop that opened it. With no pool there
      is nothing to hand across loops: every test opens and closes its own connection. It
      also lets this stay an ordinary synchronous fixture, so it needs no ``loop_scope``
      gymnastics from pytest-asyncio.
    """
    url = database.database_url()
    database.upgrade_to_head(url)

    engine = create_async_engine(url, poolclass=NullPool, echo=False)
    try:
        yield engine
    finally:
        asyncio.run(engine.dispose())


@pytest.fixture
async def db_connection(db_engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """One connection holding an open, **never-committed** outer transaction.

    The outer half of the isolation mechanism; :func:`db_session` is the inner half. Rolling
    back here undoes everything the test wrote, so tests never see each other's rows and no
    cleanup code is needed anywhere.
    """
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            yield connection
        finally:
            if transaction.is_active:
                await transaction.rollback()


@pytest.fixture
async def db_session(db_connection: AsyncConnection) -> AsyncIterator[AsyncSession]:
    """An ``AsyncSession`` whose writes are discarded when the test ends.

    **How it survives ``session.commit()`` — which every service in this codebase calls.**
    The session is bound to a connection that is *already* in a transaction, and
    ``join_transaction_mode="create_savepoint"`` tells SQLAlchemy to open a ``SAVEPOINT``
    instead of joining that transaction. ``commit()`` then releases the savepoint and starts
    a new one; the outer transaction is untouched, and :func:`db_connection` rolls it back at
    teardown. So a service under test commits for real — defaults fire, constraints trip,
    the row is visible to every later query — and still leaves nothing behind.

    ``expire_on_commit=False`` and ``autoflush=False`` mirror the application's
    ``async_sessionmaker`` (``app/db/session.py``) so a test sees the same ORM behaviour
    production does.
    """
    session = AsyncSession(
        bind=db_connection,
        expire_on_commit=False,
        autoflush=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield session
    finally:
        await session.close()


@pytest.fixture
def db_app(app: FastAPI, db_session: AsyncSession) -> FastAPI:
    """``app`` with ``deps.get_session`` resolved to the rolled-back :func:`db_session`.

    For the rare API test that wants the real database rather than a stubbed service.
    Handler and test share one session, so a row the test creates is visible to the request
    and vice versa — and both disappear at teardown.
    """

    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override
    return app


@pytest.fixture
def db_client(db_app: FastAPI, client: AsyncClient) -> AsyncClient:
    """:func:`client`, bound to the app whose session is the rolled-back one.

    Literally the same client object — ``db_app`` and ``app`` are the same function-scoped
    instance, and requesting this fixture is what installs the override.
    """
    return client


@pytest.fixture(scope="session")
def scratch_table(db_engine: AsyncEngine) -> Iterator[str]:
    """A committed table the rollback tests write to, dropped at session end.

    **Temporary scaffolding.** ``app/models/`` is empty until ANV-7, and the isolation proof
    needs a table that outlives an individual test's transaction — a table created *inside*
    the rollback would vanish with it, and the "the next test sees nothing" half of the
    demonstration would prove nothing. ANV-7 should delete this fixture and rewrite
    ``tests/integration/test_harness.py`` against a real model and its factory.
    """
    create = (
        f"CREATE TABLE IF NOT EXISTS {SCRATCH_TABLE} ("
        "  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),"
        "  label text NOT NULL UNIQUE"
        ")"
    )
    asyncio.run(_execute_committed(db_engine, create))
    try:
        yield SCRATCH_TABLE
    finally:
        asyncio.run(_execute_committed(db_engine, f"DROP TABLE IF EXISTS {SCRATCH_TABLE}"))


@pytest.fixture
def throwaway_database_url(database_available: None) -> Iterator[str]:
    """A brand-new, empty database on the test server, dropped afterwards.

    For the migration test, which runs ``upgrade``/``downgrade``/``upgrade`` and must not
    tear the schema out from under the session-scoped :func:`db_engine`.
    """
    name = f"anvex_throwaway_{uuid4().hex[:12]}"
    database.create_database(name)
    try:
        yield database.database_url(name)
    finally:
        database.drop_database(name)


async def _execute_committed(engine: AsyncEngine, statement: str) -> None:
    """Run one statement in its own committed transaction."""
    async with engine.begin() as connection:
        await connection.execute(text(statement))
