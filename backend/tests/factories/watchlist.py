"""Builders for :class:`app.models.Watchlist` and :class:`app.models.WatchlistData`."""

from __future__ import annotations

from typing import Any

from app.models import Watchlist, WatchlistData
from tests.factories.base import Factory, register


@register
class WatchlistFactory(Factory[Watchlist]):
    """A user's watchlist. The caller supplies ``user=`` or ``user_id=``.

    Nothing here is unique, so ``title`` is only sequence-derived to keep a failing
    assertion readable ("Watchlist 2" beats three identical titles).
    """

    model = Watchlist

    def defaults(self) -> dict[str, Any]:
        return {"title": f"Watchlist {self.sequence()}"}


@register
class WatchlistDataFactory(Factory[WatchlistData]):
    """One stock's membership of one watchlist.

    Both halves of the composite primary key come from the caller
    (``create(session, watchlist=w, stock=s)``); only ``position`` is generated, 0-based in
    creation order. Pin it explicitly whenever the ordering is what the test is about.
    """

    model = WatchlistData

    def defaults(self) -> dict[str, Any]:
        return {"position": self.sequence() - 1}


__all__ = ["WatchlistDataFactory", "WatchlistFactory"]
