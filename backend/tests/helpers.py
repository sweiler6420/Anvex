"""Assertions and stubs shared across the test tiers.

Chiefly the error-envelope contract. ``CLAUDE.md`` §4 makes the four-key body a public API
contract, and every ticket from ANV-11 onward asserts it, so the keys are spelled out in
exactly one place: if the envelope ever changes, one constant changes and every test that
depends on it fails loudly rather than drifting.

Also home to the fakes that let a layer be tested without the layer below it:
:class:`StubSession`, because "override ``get_session`` with something that does not touch
Postgres" is what a ``tests/api/`` test does whenever the route it is contract-testing
happens to take a session; and the ``FakeXRepo`` / ``make_x`` pairs
(:class:`FakeUserRepo`, :class:`FakeStockRepo`, :class:`FakeStockDataRepo`,
:class:`FakeWatchlistRepo`, :class:`FakePoliticianRepo`), because a service's own logic is
worth testing at unit speed against an in-memory repo rather than only through a database.

**The fakes live here, together.** Each new resource adds its pair beside the existing ones
rather than starting a module-local set, so an API test can build one object graph shared
across two services — which is what made register → login → ``/me`` testable with no
database at all.
"""

from __future__ import annotations

import itertools
import uuid
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

from fastapi import FastAPI
from httpx import Response

from app.deps.session import get_session
from app.models import Politician, Stock, StockData, User, Watchlist, WatchlistData
from app.models.watchlist import DEFAULT_TITLE

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
        return any(user.email == email and user.user_id != exclude_user_id for user in self.users)

    async def username_exists(
        self, session: Any, username: str, *, exclude_user_id: uuid.UUID | None = None
    ) -> bool:
        self.calls.append(("username_exists", username))
        return any(
            user.username == username and user.user_id != exclude_user_id for user in self.users
        )

    async def create(self, session: Any, *, username: str, email: str, password: str) -> User:
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
        return next((stock for stock in self.stocks if stock.ticker_symbol == ticker_symbol), None)

    async def list_stocks(
        self,
        session: Any,
        *,
        search: str | None = None,
        limit: int,
        offset: int = 0,
    ) -> tuple[list[Stock], int]:
        self.calls.append(("list_stocks", {"search": search, "limit": limit, "offset": offset}))
        term = (search or "").strip().lower()
        matched = [
            stock
            for stock in self.stocks
            if not term or term in stock.ticker_symbol.lower() or term in stock.company.lower()
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


class FakeWatchlistRepo:
    """An in-memory stand-in for :class:`app.repos.watchlist.WatchlistRepo`.

    Same idea and the same discipline as :class:`FakeStockRepo`: it re-implements the
    *behaviour* of the real queries rather than pretending to be them, and it implements
    only the methods :class:`app.services.watchlist.WatchlistService` actually calls.

    Four behaviours are load-bearing and deliberately faithful — a forgiving fake would
    silently pass the very bugs ANV-15 exists to fix:

    * :meth:`get_by_id` **does not filter by owner**, because the real query does not: the
      repo layer provides no "owned by user" lookup on purpose (``CLAUDE.md`` §3), so a
      service that forgot to compare ``user_id`` gets somebody else's watchlist here exactly
      as it would in production.
    * :meth:`get_by_id` returns the row with :attr:`~app.models.Watchlist.entries` **empty**,
      and only :meth:`get_with_entries` populates it. The real ``get_by_id`` does not
      eager-load, and touching a lazy collection under asyncio raises ``MissingGreenlet``
      rather than returning ``[]`` — a detached ORM instance cannot reproduce that, so the
      fake reproduces the *observable* half instead: a service that served detail out of
      ``get_by_id`` renders an empty watchlist and its test fails.
    * :meth:`max_position` answers ``None`` for an empty watchlist rather than ``-1``, which
      is the whole reason :func:`app.domain.watchlist.next_position` exists.
    * :meth:`set_positions` ignores stock ids that are not on the watchlist and returns the
      number of rows whose position actually **changed**, both as documented on the real
      method — so "did the reorder rewrite anything" is assertable at unit speed.

    ``calls`` records ``(method, argument)`` in order, which is how the ownership tests pin
    the refusal at the boundary: another user's request must never reach ``list_entries`` or
    ``get_with_entries`` at all, not merely fail to return their result.
    """

    def __init__(
        self,
        *watchlists: Watchlist,
        entries: Sequence[WatchlistData] = (),
        catalogue: Sequence[Stock] = (),
    ) -> None:
        self.watchlists: list[Watchlist] = list(watchlists)
        self.entries: list[WatchlistData] = list(entries)
        #: The securities :meth:`add_entry` can attach to a row it creates. In production
        #: ``WatchlistData.stock`` is populated by the ``selectinload`` chain on
        #: ``get_with_entries``; a detached instance has no session to load it from, so the
        #: fake needs to be told which stocks exist. Only ids that appear here come back
        #: with a stock, which is faithful: a row referencing a security that is not in the
        #: table could not exist at all (``watchlist_data.stock_id`` is a foreign key).
        self.catalogue: list[Stock] = list(catalogue)
        self.calls: list[tuple[str, Any]] = []
        #: Raised (once, then cleared) by the next :meth:`add_entry`, for the race the
        #: ``pk_watchlist_data`` primary key closes and no pre-check can.
        self.add_entry_error: Exception | None = None

    # -- watchlists ---------------------------------------------------------------------

    def add(self, watchlist: Watchlist) -> Watchlist:
        self.watchlists.append(watchlist)
        return watchlist

    async def get_by_id(self, session: Any, watchlist_id: uuid.UUID) -> Watchlist | None:
        """The row alone, **unfiltered by owner and without its entries**."""
        self.calls.append(("get_by_id", watchlist_id))
        watchlist = self._find(watchlist_id)
        if watchlist is not None:
            watchlist.entries = []
        return watchlist

    async def get_with_entries(self, session: Any, watchlist_id: uuid.UUID) -> Watchlist | None:
        self.calls.append(("get_with_entries", watchlist_id))
        watchlist = self._find(watchlist_id)
        if watchlist is None:
            return None
        watchlist.entries = self._ordered(watchlist_id)
        return watchlist

    async def list_for_user(
        self, session: Any, user_id: uuid.UUID, *, limit: int, offset: int = 0
    ) -> tuple[list[Watchlist], int]:
        self.calls.append(("list_for_user", {"user_id": user_id, "limit": limit, "offset": offset}))
        matched = [row for row in self.watchlists if row.user_id == user_id]
        matched.sort(key=lambda row: (row.title, row.watchlist_id))
        # `total` counts every match and is taken *before* the window is applied.
        return matched[offset : offset + limit], len(matched)

    async def create(
        self, session: Any, *, user_id: uuid.UUID, title: str | None = None
    ) -> Watchlist:
        self.calls.append(("create", {"user_id": user_id, "title": title}))
        return self.add(make_watchlist(user_id=user_id, title=title or DEFAULT_TITLE))

    async def delete(self, session: Any, instance: Watchlist) -> None:
        """Delete the watchlist and, as ``ON DELETE CASCADE`` does, everything on it."""
        self.calls.append(("delete", instance.watchlist_id))
        self.watchlists = [
            row for row in self.watchlists if row.watchlist_id != instance.watchlist_id
        ]
        self.entries = [
            entry for entry in self.entries if entry.watchlist_id != instance.watchlist_id
        ]

    # -- entries ------------------------------------------------------------------------

    async def entry_exists(
        self, session: Any, watchlist_id: uuid.UUID, stock_id: uuid.UUID
    ) -> bool:
        self.calls.append(("entry_exists", (watchlist_id, stock_id)))
        return self._entry(watchlist_id, stock_id) is not None

    async def list_entries(self, session: Any, watchlist_id: uuid.UUID) -> list[WatchlistData]:
        self.calls.append(("list_entries", watchlist_id))
        return self._ordered(watchlist_id)

    async def max_position(self, session: Any, watchlist_id: uuid.UUID) -> int | None:
        """``None`` on an empty watchlist, never ``-1`` — see the class docstring."""
        self.calls.append(("max_position", watchlist_id))
        positions = [entry.position for entry in self._ordered(watchlist_id)]
        return max(positions) if positions else None

    async def add_entry(
        self,
        session: Any,
        *,
        watchlist_id: uuid.UUID,
        stock_id: uuid.UUID,
        position: int,
    ) -> WatchlistData:
        self.calls.append(
            (
                "add_entry",
                {
                    "watchlist_id": watchlist_id,
                    "stock_id": stock_id,
                    "position": position,
                },
            )
        )
        if self.add_entry_error is not None:
            error, self.add_entry_error = self.add_entry_error, None
            raise error
        stock = next(
            (candidate for candidate in self.catalogue if candidate.stock_id == stock_id),
            None,
        )
        entry = WatchlistData(
            watchlist_id=watchlist_id,
            stock_id=stock_id,
            position=position,
            stock=stock,
        )
        self.entries.append(entry)
        return entry

    async def remove_entry(
        self, session: Any, watchlist_id: uuid.UUID, stock_id: uuid.UUID
    ) -> bool:
        self.calls.append(("remove_entry", (watchlist_id, stock_id)))
        entry = self._entry(watchlist_id, stock_id)
        if entry is None:
            return False
        self.entries.remove(entry)
        return True

    async def set_positions(
        self, session: Any, watchlist_id: uuid.UUID, positions: Mapping[uuid.UUID, int]
    ) -> int:
        self.calls.append(("set_positions", dict(positions)))
        changed = 0
        for entry in self._ordered(watchlist_id):
            new_position = positions.get(entry.stock_id)
            if new_position is not None and entry.position != new_position:
                entry.position = new_position
                changed += 1
        return changed

    # -- internals ----------------------------------------------------------------------

    def _find(self, watchlist_id: uuid.UUID) -> Watchlist | None:
        return next((row for row in self.watchlists if row.watchlist_id == watchlist_id), None)

    def _entry(self, watchlist_id: uuid.UUID, stock_id: uuid.UUID) -> WatchlistData | None:
        return next(
            (
                entry
                for entry in self.entries
                if entry.watchlist_id == watchlist_id and entry.stock_id == stock_id
            ),
            None,
        )

    def _ordered(self, watchlist_id: uuid.UUID) -> list[WatchlistData]:
        """``ORDER BY position, stock_id`` — the real ``list_entries`` ordering."""
        return sorted(
            (entry for entry in self.entries if entry.watchlist_id == watchlist_id),
            key=lambda entry: (entry.position, entry.stock_id),
        )


class FakePoliticianRepo:
    """An in-memory stand-in for :class:`app.repos.politician.PoliticianRepo`.

    Same idea and the same discipline as :class:`FakeStockRepo`: it re-implements the
    *behaviour* of the real queries rather than pretending to be them, and it implements only
    the three methods :class:`app.services.politician.PoliticianService` actually calls.

    Four behaviours are load-bearing and deliberately faithful — a forgiving fake would
    silently pass the very bugs these tests exist to catch:

    * :meth:`list_politicians` matches **exactly and case-sensitively** on all three filters,
      because the real query does (``Politician.state == state``). A fake that folded case
      would pass a service that had forgotten to normalise, which is the single most likely
      defect in this resource.
    * ``total`` is counted **before** the window, exactly as ``BaseRepo._page`` does, so an
      ``offset`` past the end is ``([], total)`` and not ``([], 0)``.
    * rows come back ordered by ``(last_name, first_name, politician_id)`` — the real
      ``ORDER BY``, whose third key is what makes a page boundary between two identically
      named legislators stable.
    * :meth:`bulk_upsert` **raises on an internal duplicate**, the way Postgres does
      (``ON CONFLICT DO UPDATE command cannot affect row a second time``), rather than
      quietly keeping the last one. That is the whole reason
      :func:`app.domain.politician.dedupe_politicians` exists, so a fake that tolerated it
      would delete the test's subject.

    ``calls`` records ``(method, argument)`` in order, so a test can assert *what the service
    asked the repo for* — which is how filter normalisation gets pinned at the boundary
    rather than only at the result.
    """

    #: The message Postgres actually raises for a batch that hits one conflict target twice.
    DUPLICATE_MESSAGE = "ON CONFLICT DO UPDATE command cannot affect row a second time"

    def __init__(self, *politicians: Politician) -> None:
        self.politicians: list[Politician] = list(politicians)
        self.calls: list[tuple[str, Any]] = []
        #: Raised (once, then cleared) by the next :meth:`bulk_upsert`, for the failures no
        #: pre-check can close.
        self.bulk_upsert_error: Exception | None = None

    def add(self, politician: Politician) -> Politician:
        self.politicians.append(politician)
        return politician

    async def get_by_id(self, session: Any, politician_id: str) -> Politician | None:
        """Exact match, casing included — see the class docstring."""
        self.calls.append(("get_by_id", politician_id))
        return next((row for row in self.politicians if row.politician_id == politician_id), None)

    async def list_politicians(
        self,
        session: Any,
        *,
        state: str | None = None,
        party: str | None = None,
        chamber: str | None = None,
        limit: int,
        offset: int = 0,
    ) -> tuple[list[Politician], int]:
        self.calls.append(
            (
                "list_politicians",
                {
                    "state": state,
                    "party": party,
                    "chamber": chamber,
                    "limit": limit,
                    "offset": offset,
                },
            )
        )
        matched = [
            row
            for row in self.politicians
            if (state is None or row.state == state)
            and (party is None or row.party == party)
            and (chamber is None or row.chamber == chamber)
        ]
        matched.sort(key=lambda row: (row.last_name, row.first_name, row.politician_id))
        # `total` counts every match and is taken *before* the window is applied.
        return matched[offset : offset + limit], len(matched)

    async def bulk_upsert(self, session: Any, rows: Any) -> int:
        """Insert or refresh a batch keyed on ``politician_id``, and count what it touched.

        Refuses a batch with an internal duplicate, as Postgres does; returns ``0`` without
        doing anything for an empty batch, as the real one does (an empty ``VALUES`` list is
        a syntax error, so it issues no SQL at all).
        """
        values = [dict(row) for row in rows]
        self.calls.append(("bulk_upsert", values))
        if self.bulk_upsert_error is not None:
            error, self.bulk_upsert_error = self.bulk_upsert_error, None
            raise error
        if not values:
            return 0

        identifiers = [row["politician_id"] for row in values]
        if len(set(identifiers)) != len(identifiers):
            raise RuntimeError(self.DUPLICATE_MESSAGE)

        for row in values:
            existing = next(
                (
                    candidate
                    for candidate in self.politicians
                    if candidate.politician_id == row["politician_id"]
                ),
                None,
            )
            if existing is None:
                self.politicians.append(Politician(**row))
            else:
                for column, value in row.items():
                    setattr(existing, column, value)
        return len(values)


def make_politician(
    *,
    politician_id: str = "A000001",
    first_name: str = "Adelaide",
    last_name: str = "Ashgrove",
    party: str = "Democrat",
    state: str | None = "CA",
    chamber: str | None = "Senate",
    dob: date | None = date(1960, 5, 4),
    gender: str | None = "F",
) -> Politician:
    """Build a detached :class:`~app.models.Politician` for :class:`FakePoliticianRepo`.

    Not :class:`tests.factories.PoliticianFactory`, which flushes to a session — the whole
    point of the unit tier is that there is no session. Every column has a default because
    the roster's primary key is the only thing a test usually cares to vary; the four
    nullable columns default to values rather than ``None`` so a test that wants a null says
    so explicitly.
    """
    return Politician(
        politician_id=politician_id,
        first_name=first_name,
        last_name=last_name,
        party=party,
        state=state,
        chamber=chamber,
        dob=dob,
        gender=gender,
    )


def make_watchlist(
    *,
    user_id: uuid.UUID,
    title: str = DEFAULT_TITLE,
    watchlist_id: uuid.UUID | None = None,
) -> Watchlist:
    """Build a detached :class:`~app.models.Watchlist` for :class:`FakeWatchlistRepo`.

    Not :class:`tests.factories.WatchlistFactory`, which flushes to a session — the whole
    point of the unit tier is that there is no session. ``user_id`` is required for the same
    reason the factory requires a parent: a watchlist with an invented owner is a watchlist
    no ownership test can reason about.
    """
    return Watchlist(watchlist_id=watchlist_id or uuid.uuid4(), user_id=user_id, title=title)


def make_entry(*, watchlist_id: uuid.UUID, stock: Stock, position: int) -> WatchlistData:
    """Build a detached membership row, with its :class:`~app.models.Stock` attached.

    The stock is assigned rather than left to a lazy load, because
    :class:`~app.schemas.watchlist.WatchlistEntryDetailOut` reads it and the unit tier has
    no session to load it from — which is exactly what the repo's ``selectinload`` chain
    provides in production.
    """
    return WatchlistData(
        watchlist_id=watchlist_id,
        stock_id=stock.stock_id,
        position=position,
        stock=stock,
    )


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
    "FakePoliticianRepo",
    "FakeStockDataRepo",
    "FakeStockRepo",
    "FakeUserRepo",
    "FakeWatchlistRepo",
    "StubSession",
    "assert_error_envelope",
    "make_candle",
    "make_entry",
    "make_politician",
    "make_stock",
    "make_user",
    "make_watchlist",
    "override_session",
]
