"""The ``politicians`` table.

Reference data seeded from ``app/data/politicians.json`` (ANV-16), not user-generated, so
there is no owning user and no timestamps. ``politician_id`` is the roster's own external
identifier and is kept as the natural primary key — a surrogate UUID would give the seed
nothing to be idempotent against.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Date, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

POLITICIAN_ID_MAX_LENGTH = 50
STATE_MAX_LENGTH = 5
CHAMBER_MAX_LENGTH = 50
NAME_MAX_LENGTH = 80
GENDER_MAX_LENGTH = 25
PARTY_MAX_LENGTH = 50


class Politician(Base):
    """A sitting or former legislator."""

    __tablename__ = "politicians"

    politician_id: Mapped[str] = mapped_column(
        String(POLITICIAN_ID_MAX_LENGTH),
        primary_key=True,
    )
    #: Nullable throughout where the upstream roster genuinely omits values — a
    #: presidential-level entry has no state, and historical rows often lack a date of
    #: birth or a recorded gender.
    state: Mapped[str | None] = mapped_column(String(STATE_MAX_LENGTH))
    chamber: Mapped[str | None] = mapped_column(String(CHAMBER_MAX_LENGTH))
    dob: Mapped[dt.date | None] = mapped_column(Date)
    first_name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH))
    last_name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH))
    gender: Mapped[str | None] = mapped_column(String(GENDER_MAX_LENGTH))
    party: Mapped[str] = mapped_column(String(PARTY_MAX_LENGTH))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Politician {self.politician_id!r} {self.last_name!r}>"
