"""``/v1/politicians`` — the legislator roster: filtered list, resolve by roster id.

Written to the handler shape ``app/api/v1/stocks.py`` established (``CLAUDE.md`` §3): accept
a validated request, call **one** service method, return a schema. No ``try``, no ``if``, no
session, no ``HTTPException``. Why the routes behave as they do is documented in
``app/services/politician.py``.

**Read-only, and authenticated.** There is no ``POST``/``PATCH``/``DELETE``: the roster is
reference data filled by ``backend/scripts/seed_politicians.py`` through the service, and an
HTTP endpoint that let a signed-in user rewrite it would be a different product. Every route
takes ``user: CurrentUser`` — the same requirement stocks carry, for the same reason.

**The filters are plain strings.** ``?state=tx`` finds Texans because
``app/services/politician.py`` normalises the value, not because anything happens at the
edge — the rule has to be reachable by the seed script and by a future Celery task, and a
request schema is the one layer neither goes through (``CLAUDE.md`` §4). A filter value the
roster has never heard of is an empty page rather than a 422: the party column is free text
in the database, so refusing an unknown one would mean Anvex claiming to own a vocabulary it
does not.

**No literal-before-parameterised trap here.** There is exactly one parameterised route and
no literal sibling, so nothing can shadow anything; the ordering rule (``CLAUDE.md`` §4) has
nothing to bite on. ``{politician_id}`` is a ``str`` rather than a ``UUID``: this table's
primary key is the roster's own external identifier, which is the whole reason the seed has
something to be idempotent against.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from app.deps.auth import CurrentUser
from app.deps.politician import PoliticianServiceDep
from app.schemas.errors import ErrorResponse
from app.schemas.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, Page
from app.schemas.politician import PoliticianOut

router = APIRouter(prefix="/politicians", tags=["politicians"])

#: Documented on both routes: both are guarded, so a 401 here is ordinary traffic and the
#: client needs to know which ``code`` to branch on — ``token_expired`` means refresh, the
#: rest mean sign in again.
UNAUTHORIZED_RESPONSE = {
    status.HTTP_401_UNAUTHORIZED: {
        "model": ErrorResponse,
        "description": (
            "`unauthorized` (no credentials, or a deleted account), `invalid_token`, "
            "`token_expired`, or `wrong_token_type`."
        ),
    }
}

NOT_FOUND_RESPONSE = {
    status.HTTP_404_NOT_FOUND: {
        "model": ErrorResponse,
        "description": "`not_found` — no legislator carries that roster id.",
    }
}


@router.get(
    "",
    response_model=Page[PoliticianOut],
    summary="List legislators",
    responses=UNAUTHORIZED_RESPONSE,
)
async def list_politicians(
    user: CurrentUser,
    service: PoliticianServiceDep,
    state: Annotated[
        str | None,
        Query(
            description=(
                "Two-letter state code, in any casing — it is normalised server-side. "
                "Blank means no filter."
            ),
            examples=["TX"],
        ),
    ] = None,
    party: Annotated[
        str | None,
        Query(
            description=(
                "Party affiliation, in any casing. A party the roster does not hold is an "
                "empty page, not an error."
            ),
            examples=["Republican"],
        ),
    ] = None,
    chamber: Annotated[
        str | None,
        Query(description="`House` or `Senate`, in any casing.", examples=["Senate"]),
    ] = None,
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=MAX_PAGE_LIMIT,
            description="Window size. Above the ceiling is a 422, never a silent clamp.",
        ),
    ] = DEFAULT_PAGE_LIMIT,
    offset: Annotated[int, Query(ge=0, description="Rows to skip.")] = 0,
) -> Page[PoliticianOut]:
    """One window of the roster, ordered by surname then forename then roster id.

    The three filters combine with ``AND``, so "Republican senators from Texas" is one
    request. ``total`` counts every match regardless of the window, so an ``offset`` past the
    end is an empty page with a truthful total rather than an implied end of the collection.
    """
    return await service.list_politicians(
        state=state, party=party, chamber=chamber, limit=limit, offset=offset
    )


@router.get(
    "/{politician_id}",
    response_model=PoliticianOut,
    summary="A legislator by roster id",
    responses={**UNAUTHORIZED_RESPONSE, **NOT_FOUND_RESPONSE},
)
async def read_politician(
    politician_id: Annotated[
        str,
        Path(description="The roster's own external identifier for this legislator."),
    ],
    user: CurrentUser,
    service: PoliticianServiceDep,
) -> PoliticianOut:
    """Resolve a roster id. Reference data belongs to nobody, so the 404 is the plain kind."""
    return await service.get_politician(politician_id=politician_id)


__all__ = ["router"]
