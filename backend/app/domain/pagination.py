"""The pure paging rule: how a caller's ``limit``/``offset`` becomes a window to query.

One rule, and it lives here because it now has three callers. It was written in
``app/domain/stock_data.py`` (ANV-14) when candles were the only paginated collection with
an optional window; ANV-15's ``WatchlistService.list_mine`` was the second caller and
ANV-16's ``PoliticianService.list_politicians`` is the third. ``CLAUDE.md`` §4 — "a pure
rule with a second caller moves *down*, never sideways" — makes a rule shared by three
aggregates a rule that belongs to none of them, so it has a neutral home and each aggregate
imports it downward. ``app/domain/stock_data.py`` re-exports the three names so ANV-14's
import path (and its tests) keep resolving to *these* objects rather than to copies.

**Why a resolved window is a type rather than two integers.** A service should never carry
a "maybe resolved" limit around: :class:`PageWindow` is what the repo is about to be asked
for *and* what :class:`~app.schemas.pagination.Page` will echo back, so the two cannot
disagree. Building one is the only way to get there.

**Why both bounds are clamped rather than refused.** The HTTP edge already rejects an
over-large ``limit`` and a negative ``offset`` with a 422 (``Query(ge=…, le=…)``), so an
HTTP client is never quietly handed a window it did not ask for. The clamp exists for every
*other* caller — a Celery task, a seed script, a job whose page arithmetic went negative —
which has no request to reject and would otherwise ask Postgres for the whole table or hand
it a negative ``OFFSET``. Two layers, and they are not redundant: see ``CLAUDE.md`` §4.

The limit half is delegated to :func:`~app.schemas.pagination.resolve_page_limit` rather
than re-derived, so ``DEFAULT_PAGE_LIMIT`` and ``MAX_PAGE_LIMIT`` live beside the
:class:`~app.schemas.pagination.Page` that enforces them and there is exactly one answer to
"how big is a page".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from app.schemas.pagination import resolve_page_limit

#: The smallest offset that means anything. A negative one is not a smaller page, it is a
#: nonsense request; see :func:`resolve_window` for why it is clamped rather than refused.
MIN_OFFSET: Final[int] = 0


@dataclass(frozen=True, slots=True)
class PageWindow:
    """A resolved ``limit``/``offset`` pair: exactly what the repo is about to be asked for.

    Both numbers are already safe to hand to Postgres *and* to
    :class:`~app.schemas.pagination.Page`, which is the point — a service should never carry
    a "maybe resolved" limit around.
    """

    limit: int
    offset: int


def resolve_window(*, limit: int | None = None, offset: int | None = None) -> PageWindow:
    """The window to actually query for a caller that asked for ``limit``/``offset``.

    ``limit`` is delegated to :func:`~app.schemas.pagination.resolve_page_limit` rather than
    re-derived, so the bounds live beside the :class:`~app.schemas.pagination.Page` that
    enforces them and there is one answer to "how big is a page".

    ``offset`` is **clamped** to :data:`MIN_OFFSET` rather than refused, for the same reason
    the limit is clamped rather than refused (``CLAUDE.md`` §4): the HTTP edge already
    rejects a negative offset with a 422 via ``Query(ge=0)``, so an HTTP client is never
    quietly moved somewhere it did not ask to be — while a job or a script that computed
    ``offset = page * size - size`` into a negative gets the first page instead of a
    ``SQL`` error nobody will read.
    """
    return PageWindow(
        limit=resolve_page_limit(limit),
        offset=MIN_OFFSET if offset is None else max(MIN_OFFSET, offset),
    )


__all__ = ["MIN_OFFSET", "PageWindow", "resolve_window"]
