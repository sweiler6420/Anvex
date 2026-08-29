"""The data access layer — **the only place SQLAlchemy queries are written**.

``CLAUDE.md`` §3 states it without qualification: *if you typed ``select(`` outside
``app/repos/``, it is in the wrong file.* Everything above this layer — services, routes,
Celery jobs — reaches the database through one of the classes exported here, which is what
makes "how do we store it" a question with exactly one answer per aggregate.

**The shape, in one paragraph.** One repo class per aggregate, each subclassing
:class:`app.repos.base.BaseRepo`. Every method is ``async`` and **takes an
``AsyncSession`` as its first argument** — a repo never holds one, so an instance is
stateless and the transaction stays owned by whoever opened it. Methods return models,
scalars, ``None`` or ``(rows, total)``; they never commit, never raise a domain error and
never mention HTTP. Read :mod:`app.repos.base` for the naming convention and the reasoning.

Each module also exports a ready-made instance (``user_repo``, ``stock_repo``, …) for the
common case where a service wants the default behaviour; constructing your own is equally
fine and is what a test does when it wants to subclass one.

    from app.repos import stock_repo

    stocks, total = await stock_repo.list_stocks(session, search="nvda", limit=25)
"""

from __future__ import annotations

from app.repos.base import BaseRepo
from app.repos.politician import PoliticianRepo, politician_repo
from app.repos.stock import StockRepo, stock_repo
from app.repos.stock_data import StockDataRepo, stock_data_repo
from app.repos.user import UserRepo, user_repo
from app.repos.watchlist import WatchlistRepo, watchlist_repo

__all__ = [
    "BaseRepo",
    "PoliticianRepo",
    "StockDataRepo",
    "StockRepo",
    "UserRepo",
    "WatchlistRepo",
    "politician_repo",
    "stock_data_repo",
    "stock_repo",
    "user_repo",
    "watchlist_repo",
]
