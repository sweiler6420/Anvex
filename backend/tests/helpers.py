"""Assertions and stubs shared across the test tiers.

Chiefly the error-envelope contract. ``CLAUDE.md`` §4 makes the four-key body a public API
contract, and every ticket from ANV-11 onward asserts it, so the keys are spelled out in
exactly one place: if the envelope ever changes, one constant changes and every test that
depends on it fails loudly rather than drifting.

Also home to the fakes that let a layer be tested without the layer below it:
:class:`StubSession`, because "override ``get_session`` with something that does not touch
Postgres" is what a ``tests/api/`` test does whenever the route it is contract-testing
happens to take a session; and the ``FakeXRepo`` / ``make_x`` pairs
(:class:`FakeUserRepo`, :class:`FakeStockRepo`, :class:`FakeStockDataRepo`), because a
service's own logic is worth testing at unit speed against an in-memory repo rather than
only through a database.

**The fakes live here, together.** Each new resource adds its pair beside the existing ones
rather than starting a module-local set, so an API test can build one object graph shared
across two services — which is what made register → login → ``/me`` testable with no
database at all.
"""

from __future__ import annotations

import itertools
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

from fastapi import FastAPI
from httpx import Response

from app.deps.session import get_session
from app.models import Stock, StockData, User

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


class FakeStockRepo:
    """An in-memory stand-in for :class:`app.repos.stock.StockRepo`.

    Same idea as :class:`FakeUserRepo`, and the same discipline: it re-implements the
    *behaviour* of the real queries rather than pretending to be them, so a service test
    driven against it is genuinely testing the service.

    Two behaviours are load-bearing and deliberately faithful:

    * :meth:`get_by_ticker` is **exact and case-sensitive**, because the real one is
      (``ticker_symbol`` is unique and its index serves the lookup directly). A fake that
      folded case would silently pass a service that had forgotten to normalise, which is
      one of the things ``tests/unit/test_services_stock.py`` exists to catch.
    * :meth:`list_stocks` counts ``total`` **before** applying the window, exactly as
      ``BaseRepo._page`` does, so an ``offset`` past the end is ``([], total)`` and not
      ``([], 0)``.

    What it does not reproduce is SQL: ``ilike`` escaping of ``%``/``_``, and the stability
    of the ticker ordering, are database facts proved in
    ``tests/integration/test_repos_stock.py``. ``calls`` records ``(method, argument)`` in
    order, so a test can assert *what the service asked the repo for* — which is how ticker
    normalisation gets pinned at the boundary rather than only at the result.
    """

    def __init__(self, *stocks: Stock) -> None:
        self.stocks: list[Stock] = list(stocks)
        self.calls: list[tuple[str, Any]] = []

    def add(self, stock: Stock) -> Stock:
        self.stocks.append(stock)
        return stock

    async def get_by_id(self, session: Any, stock_id: uuid.UUID) -> Stock | None:
        self.calls.append(("get_by_id", stock_id))
        return next((stock for stock in self.stocks if stock.stock_id == stock_id), None)

    async def get_by_ticker(self, session: Any, ticker_symbol: str) -> Stock | None:
        """Exact match, casing included — see the class docstring."""
        self.calls.append(("get_by_ticker", ticker_symbol))
        return next(
            (stock for stock in self.stocks if stock.ticker_symbol == ticker_symbol), None
        )

    async def list_stocks(
        self,
        session: Any,
        *,
        search: str | None = None,
        limit: int,
        offset: int = 0,
    ) -> tuple[list[Stock], int]:
        self.calls.append(
            ("list_stocks", {"search": search, "limit": limit, "offset": offset})
        )
        term = (search or "").strip().lower()
        matched = [
            stock
            for stock in self.stocks
            if not term
            or term in stock.ticker_symbol.lower()
            or term in stock.company.lower()
        ]
        matched.sort(key=lambda stock: stock.ticker_symbol)
        # `total` counts every match and is taken *before* the window is applied.
        return matched[offset : offset + limit], len(matched)


class FakeStockDataRepo:
    """An in-memory stand-in for :class:`app.repos.stock_data.StockDataRepo`.

    Same idea and the same discipline as :class:`FakeStockRepo`: it re-implements the
    *behaviour* of the real query rather than pretending to be it, and it implements only
    the method the service actually calls.

    Three behaviours are load-bearing and deliberately faithful:

    * ``total`` is counted **before** the window, exactly as ``BaseRepo._page`` does, so an
      ``offset`` past the end is ``([], total)`` and not ``([], 0)``.
    * the date bounds are **inclusive** on both ends, matching the repo's ``date >= start``
      / ``date <= end``, so a service that turned a single-day request into an empty one
      would fail here rather than pass.
    * rows come back **chronologically** by ``(date, time, id)``, which is the order the
      real query declares — a chart plots left to right, and paging over an unstable order
      would repeat or skip candles.

    It filters on ``stock_id`` and nothing else, which is the point of the fake: an unknown
    id yields ``([], 0)``, indistinguishable from a stock that simply has no candles.
    Turning one of those into a 404 and the other into an empty page is the *service's*
    judgement, and this is what makes a service that skipped the parent lookup fail.

    ``calls`` records ``(method, kwargs)`` in order, so a test can assert *what the service
    asked the repo for* — that the resolved limit and the inclusive bounds really reached
    the boundary, not merely that the result looked right.
    """

    def __init__(self, *candles: StockData) -> None:
        self.candles: list[StockData] = list(candles)
        self.calls: list[tuple[str, Any]] = []

    def add(self, candle: StockData) -> StockData:
        self.candles.append(candle)
        return candle

    async def list_for_stock(
        self,
        session: Any,
        stock_id: uuid.UUID,
        *,
        start: date | None = None,
        end: date | None = None,
        limit: int,
        offset: int = 0,
    ) -> tuple[list[StockData], int]:
        self.calls.append(
            (
                "list_for_stock",
                {
                    "stock_id": stock_id,
                    "start": start,
                    "end": end,
                    "limit": limit,
                    "offset": offset,
                },
            )
        )
        matched = [
            candle
            for candle in self.candles
            if candle.stock_id == stock_id
            and (start is None or candle.date >= start)
            and (end is None or candle.date <= end)
        ]
        matched.sort(key=lambda candle: (candle.date, candle.time, candle.id))
        # `total` counts every match in range and is taken *before* the window is applied.
        return matched[offset : offset + limit], len(matched)


def make_stock(
    *,
    ticker_symbol: str = "AAPL",
    company: str = "Apple Inc.",
    market: str = "NASDAQ",
    isin: str | None = "US0378331005",
    stock_id: uuid.UUID | None = None,
) -> Stock:
    """Build a detached :class:`~app.models.Stock` for :class:`FakeStockRepo`.

    Not :class:`tests.factories.StockFactory`, which flushes to a session — the whole point
    of these tests is that there is no session. ``stock_id`` is a server default in
    production, so it is filled in here.
    """
    return Stock(
        stock_id=stock_id or uuid.uuid4(),
        ticker_symbol=ticker_symbol,
        company=company,
        market=market,
        isin=isin,
    )


#: Ids for detached candles, so :func:`make_candle` never needs a database to be sortable.
#: ``stock_data.id`` is a ``BIGSERIAL`` in production and is the third ordering key.
_candle_ids = itertools.count(1)


def make_candle(
    *,
    stock_id: uuid.UUID,
    date: date,
    time: time = time(9, 30),
    close: str = "101.2500",
    volume: int = 1_000,
    candle_id: int | None = None,
) -> StockData:
    """Build a detached :class:`~app.models.StockData` for :class:`FakeStockDataRepo`.

    Not :class:`tests.factories.StockDataFactory`, which flushes to a session — the whole
    point of the unit tier is that there is no session. The parent's id is required for the
    same reason the factory requires a parent: a candle with an invented ``stock_id`` is a
    candle no test can find again.

    ``close`` is a **string**, deliberately: ``Decimal("101.2500")`` keeps the trailing
    zero and the four decimal places the ``NUMERIC(12, 4)`` column has, which is exactly
    what the API tier asserts survives serialisation. ``Decimal(101.25)`` from a float
    would not.
    """
    price = Decimal(close)
    return StockData(
        id=candle_id if candle_id is not None else next(_candle_ids),
        stock_id=stock_id,
        date=date,
        time=time,
        open_price=price - Decimal("0.5000"),
        high_price=price + Decimal("1.0000"),
        low_price=price - Decimal("1.0000"),
        close_price=price,
        volume=volume,
    )


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
    "FakeStockDataRepo",
    "FakeStockRepo",
    "FakeUserRepo",
    "StubSession",
    "assert_error_envelope",
    "make_candle",
    "make_stock",
    "make_user",
    "override_session",
]
