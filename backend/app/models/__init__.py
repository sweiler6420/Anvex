"""Every ORM model, re-exported so ``Base.metadata`` is complete on one import.

Alembic's ``env.py`` imports this package and nothing else from ``app/models/``; a table
whose module is not imported here is invisible to ``--autogenerate``, which then cheerfully
proposes **dropping** it. Adding a model means adding it to both lists below.

Layering (``CLAUDE.md`` §3): these classes describe persistence shape only. No business
rules, no I/O, no methods that decide anything — those belong in ``app/domain/`` and
``app/services/``, and queries belong in ``app/repos/``.
"""

from __future__ import annotations

from app.models.politician import Politician
from app.models.stock import Stock, StockData
from app.models.user import User
from app.models.watchlist import Watchlist, WatchlistData

__all__ = [
    "Politician",
    "Stock",
    "StockData",
    "User",
    "Watchlist",
    "WatchlistData",
]
