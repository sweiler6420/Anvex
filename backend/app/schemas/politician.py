"""Contracts for the ``politicians`` roster.

Reference data, not user data: the rows arrive from ``app/data/politicians.json`` through
ANV-16's loader, and the API only ever reads them.

There is **no ``PoliticianUpdate``**. Nothing edits a legislator field by field — the seed
is idempotent on ``politician_id``, the roster's own external identifier, and a re-run
replaces a row wholesale. An update schema would exist only to describe an endpoint that
should not be written.

:class:`PoliticianCreate` does exist even though no client posts one: it is the shape the
seed loader validates each JSON row against, which turns a malformed roster into a clear
error at load time instead of an ``IntegrityError`` halfway through a bulk insert.

Four columns are nullable and they are the only nullable columns outside ``stocks.isin``:
a presidential-level entry has no ``state``, and historical entries frequently lack
``chamber``, ``dob`` or ``gender``.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.models.politician import (
    CHAMBER_MAX_LENGTH,
    GENDER_MAX_LENGTH,
    NAME_MAX_LENGTH,
    PARTY_MAX_LENGTH,
    POLITICIAN_ID_MAX_LENGTH,
    STATE_MAX_LENGTH,
)

#: The roster's identifier, kept as the natural primary key so the seed has something to be
#: idempotent against.
PoliticianId = Annotated[
    str, Field(min_length=1, max_length=POLITICIAN_ID_MAX_LENGTH, examples=["N000147"])
]

Name = Annotated[str, Field(min_length=1, max_length=NAME_MAX_LENGTH, examples=["Pelosi"])]

State = Annotated[str, Field(min_length=1, max_length=STATE_MAX_LENGTH, examples=["CA"])]

Chamber = Annotated[
    str, Field(min_length=1, max_length=CHAMBER_MAX_LENGTH, examples=["House of Representatives"])
]

Gender = Annotated[str, Field(min_length=1, max_length=GENDER_MAX_LENGTH, examples=["Female"])]

Party = Annotated[str, Field(min_length=1, max_length=PARTY_MAX_LENGTH, examples=["Democrat"])]


class PoliticianCreate(BaseModel):
    """One roster row on its way in, carrying its own natural key."""

    politician_id: PoliticianId
    first_name: Name
    last_name: Name
    party: Party
    state: State | None = None
    chamber: Chamber | None = None
    dob: dt.date | None = None
    gender: Gender | None = None


class PoliticianOut(BaseModel):
    """The public shape of a legislator."""

    model_config = ConfigDict(from_attributes=True)

    politician_id: str
    first_name: str
    last_name: str
    party: str
    #: The four nullable columns. Absent upstream, not absent by accident.
    state: str | None
    chamber: str | None
    dob: dt.date | None
    gender: str | None


__all__ = [
    "Chamber",
    "Gender",
    "Name",
    "Party",
    "PoliticianCreate",
    "PoliticianId",
    "PoliticianOut",
    "State",
]
