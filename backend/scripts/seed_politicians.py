"""Load ``app/data/politicians.json`` into the database. Safe to run as often as you like.

    uv run python -m scripts.seed_politicians

A thin entry point and nothing more, which is the same shape ``CLAUDE.md`` §3 gives a Celery
task and an API handler: resolve the dependencies, call **one** service method, report. Every
rule it depends on — validating the file, deduplicating the batch, upserting on the natural
key — lives behind :meth:`app.services.politician.PoliticianService.seed_roster`, so when
ANV-22's ingest wants the same behaviour on a schedule it calls the same method rather than
shelling out to this file.

**Idempotent.** Run it twice and the table holds the same rows: the batch is deduplicated in
``app/domain/politician.py`` before the statement is built (Postgres refuses a statement whose
conflict target is hit twice) and the statement itself is
``INSERT ... ON CONFLICT (politician_id) DO UPDATE``, so a second run refreshes rows instead
of adding them. ``written`` is therefore the same number both times — the upsert touches every
row it matches, and re-writing a row to the values it already holds is what idempotency looks
like from the statement's side.

**It talks to whatever ``.env`` points at.** There is no ``--database`` flag, because
``app/settings.py`` is the single source of the DSN (``CLAUDE.md`` §4) and a script with its
own connection string is how a fixture ends up in production. Point it somewhere else by
pointing the environment somewhere else.

Exit codes: ``0`` seeded, ``1`` the checked-in file is unusable (the message names the file,
the row and the field), ``2`` the database refused.

The ``print`` calls below are this program's *output*, not logging — ``CLAUDE.md`` §4's "no
bare ``print``" is about the application, whose structured ``politicians.seeded`` line the
service emits regardless of who called it.
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy.exc import SQLAlchemyError

from app.data.loader import SeedDataError
from app.db.engine import dispose_engine
from app.db.session import get_session
from app.services.politician import PoliticianService, SeedReport
from app.settings import get_settings

EXIT_OK = 0
EXIT_BAD_DATA = 1
EXIT_DATABASE = 2


async def seed() -> SeedReport:
    """Open one session, run the seed, close everything.

    ``app.db.session.get_session`` is the same context manager the API's dependency wraps —
    there is deliberately no second sessionmaker and no second engine, so this script uses
    the pool the application would have used.
    """
    settings = get_settings()
    try:
        async with get_session() as session:
            service = PoliticianService(session, settings)
            return await service.seed_roster()
    finally:
        await dispose_engine()


def main() -> int:
    """Run the seed and print what it did. Returns the process exit code."""
    try:
        report = asyncio.run(seed())
    except SeedDataError as error:
        print(f"seed failed: {error}", file=sys.stderr)
        return EXIT_BAD_DATA
    except SQLAlchemyError as error:
        print(f"seed failed: the database refused the roster: {error}", file=sys.stderr)
        return EXIT_DATABASE

    print(
        f"politicians seeded: {report.loaded} row(s) in the file, "
        f"{report.deduplicated} duplicate(s) collapsed, {report.written} row(s) written."
    )
    if report.duplicates:
        print(f"  duplicate roster ids: {', '.join(report.duplicates)}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
