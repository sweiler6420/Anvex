"""Faker-backed model builders — one ``@register``ed :class:`Factory` per model.

Read :mod:`tests.factories.base` before adding one; it carries the pattern and the two
rules that matter:

* **Unique columns come from** ``self.sequence()``, **never from faker.** The seed is reset
  before every test, so ``fake.email()`` returns the same address twice *within* one test
  and the unique constraint fires. ``test_faker_alone_would_repeat_within_a_test`` in
  ``tests/integration/test_harness.py`` documents the trap.
* **A factory flushes; it never commits.** The transaction boundary belongs to the service
  layer (``CLAUDE.md`` §3) and, in tests, to the rollback fixture.

Association and child models (``StockData``, ``Watchlist``, ``WatchlistData``) take their
parent from the caller rather than inventing one::

    user = await UserFactory().create(db_session)
    stock = await StockFactory().create(db_session)
    watchlist = await WatchlistFactory().create(db_session, user=user)
    await WatchlistDataFactory().create(db_session, watchlist=watchlist, stock=stock)

That keeps a test's object graph exactly as large as the test says it is.
"""

from __future__ import annotations

from tests.factories.base import (
    DEFAULT_SEED,
    Factory,
    factory_for,
    fake,
    next_in_sequence,
    register,
    registered_factories,
    reset_randomness,
)
from tests.factories.politician import PoliticianFactory
from tests.factories.stock import StockDataFactory, StockFactory
from tests.factories.user import UserFactory
from tests.factories.watchlist import WatchlistDataFactory, WatchlistFactory

__all__ = [
    "DEFAULT_SEED",
    "Factory",
    "PoliticianFactory",
    "StockDataFactory",
    "StockFactory",
    "UserFactory",
    "WatchlistDataFactory",
    "WatchlistFactory",
    "factory_for",
    "fake",
    "next_in_sequence",
    "register",
    "registered_factories",
    "reset_randomness",
]
