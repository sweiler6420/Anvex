"""Pure rules about the roster: what a filter means, and what a seed batch may contain.

Two rules, and both exist because a layer below refuses to guess.

**Normalising a filter.** ``PoliticianRepo``'s three filters are exact and case-sensitive by
design — ``state="tx"`` matches nothing — because the columns hold enumerated values served
by ordinary indexes and folding case in SQL would defeat them. So somebody has to turn a
query string into the roster's own spelling, and ``CLAUDE.md`` §4 says who: *normalising an
identifier is the service's job, not the request schema's*, because a Celery task and a seed
script do not go through a request schema. ANV-13/14 established that shape with
:func:`app.domain.stock.normalise_ticker`; this is the same rule for three more fields.

Ticker normalisation is one line because a ticker's canonical spelling is mechanical
(``.upper()``). A chamber's is not — ``"senate"`` must become ``"Senate"`` and no case
transformation of ``"house of representatives"`` produces ``"House of Representatives"`` —
so the canonical spellings are enumerated in :data:`CHAMBERS` and :data:`PARTIES` and matched
case-insensitively. **An unrecognised value is passed through trimmed, never rewritten and
never rejected**: a filter Anvex does not know about is a legitimate empty page (the roster
will one day hold parties this file has never heard of), whereas rejecting it would turn a
harmless query into a 422 and rewriting it would answer a question the caller did not ask.

**Deduplicating a seed batch.** ``PoliticianRepo.bulk_upsert`` is a single
``INSERT ... ON CONFLICT DO UPDATE``, and Postgres refuses a statement that hits one conflict
target twice — ``ON CONFLICT DO UPDATE command cannot affect row a second time``. The repo
deliberately does not dedupe (``CLAUDE.md`` §3: a repo holds no rules), so the batch arrives
here first. That is what makes the seed idempotent *within* a run, which is a different
property from the upsert's idempotency *across* runs: the upsert stops a second run creating
duplicates, and this stops a single run crashing on one.

There is no I/O here and no clock: every function takes plain data and returns plain data.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final

from app.schemas.politician import PoliticianCreate

#: The noun a 404 about a legislator reports, and the ``details["resource"]`` a client
#: branches on. Lives in the domain rather than the service because the seed reports it too.
RESOURCE: Final[str] = "politician"

#: Canonical chamber spellings. Two today; the roster fixture writes exactly these.
CHAMBERS: Final[tuple[str, ...]] = ("House", "Senate")

#: Canonical party spellings. Not a closed set in the database — the column is a plain
#: ``VARCHAR`` and a roster may carry anything — only the set whose casing we can fix.
PARTIES: Final[tuple[str, ...]] = ("Democrat", "Republican", "Independent", "Libertarian")


def _blank_to_none(value: str | None) -> str | None:
    """An omitted filter and a filter of spaces are the same request: no filter.

    Without this, ``?state=`` would search for the empty string and return nothing, which
    reads to a caller as "there are no legislators" rather than "you filtered on nothing".
    """
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


def _canonical(value: str | None, vocabulary: Sequence[str]) -> str | None:
    """The vocabulary's spelling of ``value``, or ``value`` trimmed if it is not in it."""
    trimmed = _blank_to_none(value)
    if trimmed is None:
        return None
    folded = trimmed.casefold()
    return next((known for known in vocabulary if known.casefold() == folded), trimmed)


def normalise_state(state: str | None) -> str | None:
    """A state filter in the roster's spelling: trimmed and upper-cased.

    Purely mechanical, exactly like a ticker: the column holds two-letter postal codes, and
    ``"tx"``, ``"Tx"`` and ``" TX "`` name one state. A longer value is left alone apart
    from case — the column takes five characters, and a territory code is not this rule's
    business to validate.
    """
    trimmed = _blank_to_none(state)
    return trimmed.upper() if trimmed is not None else None


def normalise_party(party: str | None) -> str | None:
    """A party filter in the roster's spelling — see :data:`PARTIES` and the module docstring."""
    return _canonical(party, PARTIES)


def normalise_chamber(chamber: str | None) -> str | None:
    """A chamber filter in the roster's spelling — see :data:`CHAMBERS`."""
    return _canonical(chamber, CHAMBERS)


@dataclass(frozen=True, slots=True)
class RosterFilters:
    """The three filters, already in the roster's spelling. What the repo is about to get.

    Frozen for the same reason :class:`~app.domain.pagination.PageWindow` is: a service
    should never carry a "maybe normalised" filter around, and constructing one through
    :func:`resolve_filters` is the only way to get one.
    """

    state: str | None = None
    party: str | None = None
    chamber: str | None = None

    @property
    def is_unfiltered(self) -> bool:
        """No filter at all: the whole roster."""
        return self.state is None and self.party is None and self.chamber is None


def resolve_filters(
    *,
    state: str | None = None,
    party: str | None = None,
    chamber: str | None = None,
) -> RosterFilters:
    """All three rules at once — the single call a service makes before it touches the repo."""
    return RosterFilters(
        state=normalise_state(state),
        party=normalise_party(party),
        chamber=normalise_chamber(chamber),
    )


@dataclass(frozen=True, slots=True)
class RosterBatch:
    """A seed batch that is safe to hand to ``bulk_upsert``, and what was dropped to make it.

    ``duplicates`` is the ids that appeared more than once, in the order they were first
    seen. It is reported rather than swallowed: a roster file with a repeated id is very
    probably a mistake in the file, and the seed logs it so somebody can look — but it is
    not an *error*, because the resolution ("the later row wins") is unambiguous and
    failing the seed over it would be worse than fixing it.
    """

    rows: tuple[PoliticianCreate, ...]
    duplicates: tuple[str, ...]

    @property
    def has_duplicates(self) -> bool:
        return bool(self.duplicates)


def dedupe_politicians(rows: Iterable[PoliticianCreate]) -> RosterBatch:
    """Collapse a batch to one row per ``politician_id``. **The last occurrence wins.**

    Last rather than first because that is what the caller would have got had the rows been
    applied one at a time: two sequential upserts of the same id leave the second row's
    values in the table, so a batch must not disagree with the loop it replaces. It also
    matches how a file is read and edited — a row appended at the bottom is a correction of
    the one above, not a duplicate of it.

    Ordering is the order of **first** appearance, so a re-run of the same file produces the
    same statement in the same order and a diff of two seed logs is readable.

    Matching is exact: ``politician_id`` is the table's primary key, the upsert's conflict
    target is that column, and two ids differing only in case are two rows to Postgres. A
    fold here would drop a row the database would have happily kept.
    """
    kept: dict[str, PoliticianCreate] = {}
    duplicates: list[str] = []
    for row in rows:
        if row.politician_id in kept and row.politician_id not in duplicates:
            duplicates.append(row.politician_id)
        kept[row.politician_id] = row
    return RosterBatch(rows=tuple(kept.values()), duplicates=tuple(duplicates))


__all__ = [
    "CHAMBERS",
    "PARTIES",
    "RESOURCE",
    "RosterBatch",
    "RosterFilters",
    "dedupe_politicians",
    "normalise_chamber",
    "normalise_party",
    "normalise_state",
    "resolve_filters",
]
