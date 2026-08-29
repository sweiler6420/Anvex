"""Roster use cases: a filtered list, one legislator, and the seed that fills the table.

Written to the shape ``app/services/auth.py`` established (``CLAUDE.md`` §3) — collaborators
in the constructor defaulting to the repo singletons, one ``async`` method per use case,
keyword-only arguments, a schema out, ``app.domain.errors`` on the way out, and the
``commit()`` here because repos only flush.

**Reference data has no owner, so the 404 is the plain kind.** ANV-15's ownership gate
(``_resolve_owned``, the 404-not-403 refusal, the isolation sweep) exists because a watchlist
belongs to somebody. A legislator belongs to nobody: every authenticated caller sees the same
roster, there is no cross-account case to leak, and importing that machinery here would be
cargo-culting a rule whose premise is absent. The refusal here is
``app/services/stock.py``'s — the row is not there, so it is a
:class:`~app.domain.errors.NotFoundError`, and that is the whole story.

**Filter normalisation is this layer's, not the request schema's** (``CLAUDE.md`` §4).
``PoliticianRepo``'s filters are exact and case-sensitive by design, so ``?state=tx`` would
return nothing at all; the rule that fixes it lives in :mod:`app.domain.politician` where the
seed script and a future Celery task reach the same answer an HTTP caller does. Exactly the
argument that put :func:`app.domain.stock.normalise_ticker` there.

**The seed is idempotent twice over, and the two halves are different mechanisms.** Across
runs, ``PoliticianRepo.bulk_upsert`` is ``INSERT ... ON CONFLICT (politician_id) DO UPDATE``
on the real primary key, so a second run refreshes the same rows instead of duplicating
them. Within a run, :func:`app.domain.politician.dedupe_politicians` collapses the batch
first, because Postgres rejects a statement whose conflict target is hit twice
(``ON CONFLICT DO UPDATE command cannot affect row a second time``) and the repo deliberately
does no deduplication of its own. Neither half substitutes for the other.

**The loader is not caught.** :class:`~app.data.loader.SeedDataError` means a file that ships
with the repository is broken, which is a defect rather than a request, and the seed path is
reached from a script — never from a route — so there is no HTTP response for it to become.
It propagates, the script exits non-zero, and the message names the file, the row and the
field. Contrast ``app/utils/`` exceptions (``CLAUDE.md`` §4), which *are* translated here,
because those describe input a user supplied.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.politicians import load_politicians, politicians_provenance
from app.domain.errors import NotFoundError
from app.domain.pagination import resolve_window
from app.domain.politician import RESOURCE, dedupe_politicians, resolve_filters
from app.repos.politician import PoliticianRepo, politician_repo
from app.schemas.pagination import Page
from app.schemas.politician import PoliticianCreate, PoliticianOut
from app.settings import Settings

logger = structlog.get_logger("anvex.politicians")


@dataclass(frozen=True, slots=True)
class SeedReport:
    """What one run of :meth:`PoliticianService.seed_roster` did.

    Deliberately **not** an ``app/schemas/`` model: nothing serves this over HTTP, and
    ``CLAUDE.md`` §3 reserves that package for the API's public shape. It is the return value
    of an operational method, so it is a plain frozen value the seed script prints.

    ``loaded`` counts rows in the file, ``written`` counts rows the statement touched, and
    ``duplicates`` names the ids that appeared more than once — so ``loaded - written`` is
    explained rather than merely observed. On a *second* run of the same roster ``written``
    is unchanged: the upsert updates every row it matches, and updating fifty-four rows to
    the values they already hold is what idempotency looks like from here.
    """

    loaded: int
    written: int
    duplicates: tuple[str, ...]

    @property
    def deduplicated(self) -> int:
        """Rows dropped before the statement was built."""
        return len(self.duplicates)


class PoliticianService:
    """Reading the roster, and filling it."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        politicians: PoliticianRepo = politician_repo,
    ) -> None:
        self.session = session
        self.settings = settings
        #: Keyword-defaulted to the module-level singleton, which is the seam a unit test
        #: replaces with :class:`tests.helpers.FakePoliticianRepo` to run without Postgres.
        self.politicians = politicians

    # -----------------------------------------------------------------------------------
    # Use cases
    # -----------------------------------------------------------------------------------

    async def list_politicians(
        self,
        *,
        state: str | None = None,
        party: str | None = None,
        chamber: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> Page[PoliticianOut]:
        """One window of the roster, filtered, in the standard envelope.

        The three filters combine with ``AND`` and each is independently optional, so "every
        legislator", "everyone from Texas" and "Republican senators from Texas" are one call.
        Each is normalised first (:func:`app.domain.politician.resolve_filters`), which is
        what makes ``?state=tx&chamber=senate`` find anybody at all — the repo's matching is
        exact and case-sensitive, deliberately.

        A filter value the roster has never heard of is an empty page, not a 422: the party
        column is free text in the database and Anvex does not own the vocabulary.

        ``total`` counts every matching row regardless of the window, so an ``offset`` past
        the end is an empty page with a truthful total.
        """
        filters = resolve_filters(state=state, party=party, chamber=chamber)
        window = resolve_window(limit=limit, offset=offset)
        rows, total = await self.politicians.list_politicians(
            self.session,
            state=filters.state,
            party=filters.party,
            chamber=filters.chamber,
            limit=window.limit,
            offset=window.offset,
        )
        return Page[PoliticianOut](
            items=[PoliticianOut.model_validate(row) for row in rows],
            total=total,
            limit=window.limit,
            offset=window.offset,
        )

    async def get_politician(self, *, politician_id: str) -> PoliticianOut:
        """The legislator this roster id names.

        The id is **not** normalised beyond having its surrounding whitespace trimmed, and
        that is a decision rather than an omission. A ticker has a canonical spelling Anvex
        can derive (``.upper()``); a roster id does not — it is an opaque string minted by
        whoever publishes the roster, and case-folding one would make a genuinely distinct
        id unfindable while pretending to be helpful. Trimming is safe because no id has a
        leading space and a caller pasting one out of a spreadsheet frequently does.

        :raises NotFoundError: no legislator carries that id.
        """
        identifier = politician_id.strip()
        politician = await self.politicians.get_by_id(self.session, identifier)
        if politician is None:
            raise NotFoundError(RESOURCE, identifier)
        return PoliticianOut.model_validate(politician)

    async def seed_roster(self, *, rows: Sequence[PoliticianCreate] | None = None) -> SeedReport:
        """Load the checked-in roster into the table, or refresh what is already there.

        Safe to run repeatedly — that is the point, and both halves of why are in the module
        docstring. ``rows`` is the seam a test uses to seed a batch it built itself;
        production passes nothing and the roster comes from ``app/data/politicians.json``.

        An empty roster is a no-op returning zero rather than an error: ``bulk_upsert``
        issues no SQL for an empty batch (an empty ``VALUES`` list is a syntax error), and a
        seed with nothing to do has done its job.

        :raises SeedDataError: the checked-in file is missing, unparseable, unattributed or
            holds an invalid row. Not translated — see the module docstring.
        """
        if rows is None:
            loaded = load_politicians()
            logger.info(
                "politicians.seed_loaded",
                rows=len(loaded),
                provenance=politicians_provenance(),
            )
        else:
            loaded = list(rows)

        batch = dedupe_politicians(loaded)
        if batch.has_duplicates:
            logger.warning(
                "politicians.seed_duplicates",
                count=len(batch.duplicates),
                politician_ids=list(batch.duplicates),
            )

        written = await self.politicians.bulk_upsert(
            self.session, [row.model_dump() for row in batch.rows]
        )
        await self.session.commit()
        logger.info(
            "politicians.seeded",
            loaded=len(loaded),
            written=written,
            duplicates=len(batch.duplicates),
        )
        return SeedReport(loaded=len(loaded), written=written, duplicates=batch.duplicates)


__all__ = ["RESOURCE", "PoliticianService", "SeedReport"]
