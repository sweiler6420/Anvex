"""Builder for :class:`app.models.Politician`."""

from __future__ import annotations

import datetime as dt
from typing import Any

from app.models import Politician
from tests.factories.base import Factory, fake, register

CHAMBERS = ("House", "Senate")
PARTIES = ("Democrat", "Republican", "Independent")
STATES = ("CA", "TX", "NY", "FL", "WA", "OH")


@register
class PoliticianFactory(Factory[Politician]):
    """A legislator. ``politician_id`` is the primary key, so it is sequence-derived."""

    model = Politician

    def defaults(self) -> dict[str, Any]:
        n = self.sequence()
        return {
            "politician_id": f"P{n:06d}",
            "state": STATES[n % len(STATES)],
            "chamber": CHAMBERS[n % len(CHAMBERS)],
            "dob": dt.date(1950 + (n % 40), 1 + (n % 12), 1 + (n % 28)),
            "first_name": fake.first_name(),
            "last_name": fake.last_name(),
            "gender": "F" if n % 2 else "M",
            "party": PARTIES[n % len(PARTIES)],
        }


__all__ = ["PoliticianFactory"]
