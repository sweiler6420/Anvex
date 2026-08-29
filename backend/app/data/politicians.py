"""Reading ``politicians.json`` — the checked-in roster fixture.

The file beside this one is **synthetic**: every person in it is invented, and it says so in
its own ``provenance`` key (which :mod:`app.data.loader` requires of every reference file, so
an unattributed roster cannot be loaded at all). It exists so a fresh database has something
plausible in ``anvex.politicians`` to develop and demo against — a real, licensed roster
replaces it wholesale.

Nothing here touches a database. :func:`load_politicians` returns
:class:`~app.schemas.politician.PoliticianCreate` values and stops; deduplicating them and
writing them is :class:`app.services.politician.PoliticianService`'s job (``CLAUDE.md`` §3).
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from app.data.loader import DATA_DIR, load_rows, provenance_of
from app.schemas.politician import PoliticianCreate

#: The checked-in roster. Resolved against :data:`~app.data.loader.DATA_DIR` rather than the
#: working directory, so a seed script started from anywhere finds it.
POLITICIANS_FILE: Final[Path] = DATA_DIR / "politicians.json"


def load_politicians(path: Path | None = None) -> list[PoliticianCreate]:
    """Every row of the roster, validated.

    Validating against :class:`~app.schemas.politician.PoliticianCreate` — the same schema an
    HTTP body would have been checked against — is what makes a bad roster fail at *load*
    rather than as an ``IntegrityError`` partway through the insert. A row missing a party,
    carrying an 81-character surname or spelling ``dob`` as ``"March"`` is a
    :class:`~app.data.loader.SeedDataError` naming the row index and the field.

    ``path`` exists for tests, which point it at a temporary file. Production passes nothing.

    :raises SeedDataError: the file is missing, unparseable, unattributed, or holds a row
        that is not a valid roster entry.
    """
    return load_rows(path or POLITICIANS_FILE, PoliticianCreate)


def politicians_provenance(path: Path | None = None) -> str:
    """Where the roster came from, in its own words — logged by the seed so a database can
    be traced back to the fixture that filled it."""
    return provenance_of(path or POLITICIANS_FILE)


__all__ = ["POLITICIANS_FILE", "load_politicians", "politicians_provenance"]
