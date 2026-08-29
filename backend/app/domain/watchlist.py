"""Pure rules for the ordinals on a watchlist: append, insert, move, drop.

A watchlist is an *ordered* set of stocks, and the order is stored as an integer per
membership row (``watchlist_data.position``). Everything about keeping those integers sane
is arithmetic on plain data — no database, no request, no clock — so ``CLAUDE.md`` §3 puts
it here, where it is cheap to test exhaustively and where a Celery task or a seed script
reaches the same answers an HTTP caller does. ``app/repos/watchlist.py`` supplies the
ordinals this module reads (``list_entries``, ``max_position``) and applies the ordinals it
returns (``set_positions``); the judgement in between is all here.

**The one invariant.** After any operation in this module the positions are exactly
``0..n-1``, one per stock, with no gaps and no ties. Nothing in the schema enforces that —
ANV-7 deliberately left ``position`` non-unique so a mid-swap state can be flushed in one
statement — so it is an invariant this module maintains rather than one the database
guarantees. That is precisely why every function here **renumbers the whole list** instead
of patching the rows it thinks changed: renumbering is total, so a list that had drifted
comes back correct, whereas a patch inherits whatever was already wrong.

**Why the move is keyed on ``stock_id`` and not on a "current index".** The endpoint this
replaces took ``(stock_id, current_index, destination_index)`` and then used
``current_index`` as a list subscript, ignoring ``stock_id`` entirely — so the row that
actually moved was whichever one happened to sit at that subscript. A client whose view was
one drag stale therefore reordered a *different* stock than the user dropped, silently and
with a 201. The server already knows where every stock is; the client's belief about that is
derived, second-hand and racy. So the caller says **which stock** and **where it should end
up**, and nothing else: :func:`reposition` looks the origin up itself. There is deliberately
no way to express "the thing at index 3", because that sentence cannot be verified.

**Why the arithmetic is a list splice.** The old handler had two mirrored shift loops, one
per direction. Traced by hand they are correct *while* positions are exactly ``0..n-1`` —
that part of the old code was not the bug. But two branches is two chances to be wrong, and
they are only ever correct under an assumption nothing enforces. Removing the stock from the
canonical order and re-inserting it at the destination has no branches, no direction and no
assumption: it is the same answer for a move up, a move down and a move to where the stock
already is, and it is correct for any starting positions whatsoever because
:func:`canonical_order` normalises them first.

**What is refused, and why it is not clamped.** A destination outside the list is a
:class:`~app.domain.errors.ValidationError` (422), not a clamp to the nearest end. The old
handler subscripted an unvalidated client integer: too large was an ``IndexError`` — a 500
for a request the API should simply have refused — and *negative* was worse, because Python
subscripts from the end, so ``destination = -1`` silently moved the stock to the back and
reported success. Clamping would keep that second failure mode's shape: the caller asked for
something impossible and got a plausible-looking answer, exactly the way ANV-14 argued an
inverted date range must not become a quietly empty page. The caller has a bug; say so.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Final

from app.domain.errors import ConflictError, NotFoundError, ValidationError

#: The first ordinal on a watchlist. Zero-based, matching ``app.schemas.watchlist.Position``
#: (``ge=0``) and the frontend's array indices.
ORIGIN: Final[int] = 0

#: ``details["resource"]`` on a refusal about the list itself. Spelled once here, in the
#: layer below the service, so the service and its errors cannot disagree about the noun.
RESOURCE: Final[str] = "watchlist"

#: ``details["resource"]`` on a refusal about one membership row. A distinct noun from
#: :data:`RESOURCE`: "no such watchlist" and "that stock is not on this watchlist" are
#: different facts, and a client that has already been told the list exists loses nothing
#: by being told which of the two it hit.
ENTRY_RESOURCE: Final[str] = "watchlist entry"

#: ``details["field"]`` on an out-of-range destination — the name the request schemas use
#: for it (``WatchlistEntryCreate.position`` / ``WatchlistEntryUpdate.position``), so a form
#: can highlight the field it actually rendered.
DESTINATION_FIELD: Final[str] = "position"


def canonical_order(positions: Mapping[uuid.UUID, int]) -> tuple[uuid.UUID, ...]:
    """The stocks in the order their ordinals put them, ties broken by ``stock_id``.

    The normalisation step every other function here starts from, and the reason none of
    them has to assume the ordinals were dense, unique or zero-based to begin with. Only
    the *relative* order of the ordinals is read; their values are discarded.

    The tie-break is ``stock_id`` because that is what
    :meth:`~app.repos.watchlist.WatchlistRepo.list_entries` orders by
    (``ORDER BY position, stock_id``), and Python compares :class:`uuid.UUID` by its
    integer value, which is the same order Postgres compares ``uuid`` in. So a list holding
    two rows at position 3 — legal, since ``position`` carries no unique constraint —
    canonicalises to the same sequence here as it reads back in from the database, rather
    than to whatever order a dict happened to be built in.
    """
    return tuple(sorted(positions, key=lambda stock_id: (positions[stock_id], stock_id)))


def dense_positions(order: Sequence[uuid.UUID]) -> dict[uuid.UUID, int]:
    """Number ``order`` from :data:`ORIGIN` upwards: the module's output shape.

    Total, not incremental — see the module docstring. Every function here ends in this
    call, which is what makes "positions are exactly ``0..n-1``" true of the result no
    matter what was true of the input.
    """
    return {stock_id: index for index, stock_id in enumerate(order, start=ORIGIN)}


def normalise(positions: Mapping[uuid.UUID, int]) -> dict[uuid.UUID, int]:
    """Re-densify a watchlist's ordinals without moving anything.

    The repair operation: gaps closed, duplicates broken apart, a non-zero-based run pulled
    down to :data:`ORIGIN`, and the relative order preserved throughout. Already-canonical
    input is returned unchanged, so a caller can apply it unconditionally and
    :meth:`~app.repos.watchlist.WatchlistRepo.set_positions` will report zero rows changed.
    """
    return dense_positions(canonical_order(positions))


def next_position(max_position: int | None) -> int:
    """Where an appended stock goes, given the highest ordinal currently in use.

    :meth:`~app.repos.watchlist.WatchlistRepo.max_position` answers ``None`` for an empty
    watchlist rather than ``-1`` — "there is no last position" is a different statement from
    "the last position is minus one", and the repo declines to bake the 0-based convention
    into a query. This is where that convention lives, so the translation happens once.

    **It is deliberately not spelled** ``(max_position or -1) + 1``, which is how
    ``CLAUDE.md`` and ``app/repos/watchlist.py`` both describe the rule in prose. That
    expression is wrong for exactly one input and it is not the empty case: ``0`` is falsy,
    so a watchlist holding a single stock at position 0 would append the second stock *also*
    at position 0 — a tie the schema permits and the ordering can never resolve. The ``is
    None`` test is the same rule without the hole, and ``tests/unit/test_domain_watchlist.py``
    pins the ``max_position == 0`` case so it cannot be "simplified" back.
    """
    return ORIGIN if max_position is None else max_position + 1


def reposition(
    positions: Mapping[uuid.UUID, int], *, stock_id: uuid.UUID, destination: int
) -> dict[uuid.UUID, int]:
    """Move one stock to ``destination`` and renumber the list around it.

    :param positions: the watchlist's current ``{stock_id: position}`` map, exactly as
        :meth:`~app.repos.watchlist.WatchlistRepo.list_entries` yields it. Its ordinals need
        not be dense, zero-based or even distinct; only their relative order is read.
    :param stock_id: **which stock moves.** Not an index — see the module docstring.
    :param destination: the index it should end up at, in ``0..n-1``.
    :returns: the complete new ``{stock_id: position}`` map, positions exactly ``0..n-1``.
        The same shape as the input, so the result can be fed straight back in.

    :raises NotFoundError: ``stock_id`` is not on this watchlist. Including when the
        watchlist is empty, where every destination is out of range as well — the missing
        stock is reported first, because it is the more specific fact.
    :raises ValidationError: ``destination`` falls outside ``0..n-1``.
    """
    order = canonical_order(positions)
    _require_member(order, stock_id)
    _require_destination(destination, ceiling=len(order) - 1)

    moved = [candidate for candidate in order if candidate != stock_id]
    moved.insert(destination, stock_id)
    return dense_positions(moved)


def insert(
    positions: Mapping[uuid.UUID, int],
    *,
    stock_id: uuid.UUID,
    destination: int | None = None,
) -> dict[uuid.UUID, int]:
    """Put a stock that is **not** yet on the watchlist at ``destination``.

    ``destination=None`` appends, which is what ``app/schemas/watchlist.py`` documents for
    an omitted ``position`` — and a change from the endpoint this replaces, which
    unconditionally *prepended* and pushed every existing stock down by one. Appending is
    the behaviour a "watch this too" button wants; prepending re-sorted a list the user had
    arranged every time they added anything.

    The valid range is ``0..n`` **inclusive** — one wider than :func:`reposition`'s, because
    inserting after the last stock is a real place to insert and moving after the last stock
    is not a real place to move.

    :raises ConflictError: the stock is already on this watchlist. The duplicate-add case,
        raised here rather than left to the caller so both the pure rule and the service
        agree on the noun.
    :raises ValidationError: ``destination`` falls outside ``0..n``.
    """
    order = list(canonical_order(positions))
    if stock_id in positions:
        raise ConflictError(ENTRY_RESOURCE, stock_id)

    destination = len(order) if destination is None else destination
    _require_destination(destination, ceiling=len(order))

    order.insert(destination, stock_id)
    return dense_positions(order)


def remove(positions: Mapping[uuid.UUID, int], *, stock_id: uuid.UUID) -> dict[uuid.UUID, int]:
    """Take a stock off the watchlist and close the gap it leaves.

    The renumbering matters: without it a five-stock list that has had its third stock
    removed keeps positions ``0,1,3,4``, and every later insert-at-index reasons about a
    list whose ordinals no longer match its indices.

    :raises NotFoundError: ``stock_id`` is not on this watchlist.
    """
    order = canonical_order(positions)
    _require_member(order, stock_id)
    return dense_positions([candidate for candidate in order if candidate != stock_id])


# ---------------------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------------------


def _require_member(order: Sequence[uuid.UUID], stock_id: uuid.UUID) -> None:
    """Refuse a move or a drop that names a stock the watchlist does not hold."""
    if stock_id not in order:
        raise NotFoundError(ENTRY_RESOURCE, stock_id)


def _require_destination(destination: int, *, ceiling: int) -> None:
    """Refuse an index outside ``ORIGIN..ceiling``, rather than clamping into it.

    ``ceiling`` is negative for an empty list, which makes *every* destination invalid —
    correct, and unreachable in practice because :func:`reposition` checks membership
    first and :func:`insert` passes a ceiling of ``0`` for an empty list.
    """
    if destination < ORIGIN or destination > ceiling:
        raise ValidationError(
            f"Position {destination} is outside this watchlist: "
            f"it holds positions {ORIGIN} to {max(ceiling, ORIGIN)}.",
            field=DESTINATION_FIELD,
            details={"position": destination, "min": ORIGIN, "max": ceiling},
        )


__all__ = [
    "DESTINATION_FIELD",
    "ENTRY_RESOURCE",
    "ORIGIN",
    "RESOURCE",
    "canonical_order",
    "dense_positions",
    "insert",
    "next_position",
    "normalise",
    "remove",
    "reposition",
]
