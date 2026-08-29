"""The declarative base every Anvex ORM model inherits from.

Per ``CLAUDE.md`` §3 this module is pure plumbing: it knows the Postgres *schema* the
application owns and the naming rules for its constraints, and nothing at all about what
a Stock or a User is. Models live in ``app/models/`` (ANV-7).
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

from app.settings import get_settings

#: Deterministic names for every implicitly-created constraint and index.
#:
#: Without this, Postgres invents names ("stocks_ticker_symbol_key") that Alembic cannot
#: reliably reproduce, so autogenerate emits noisy drop/create churn and a downgrade has
#: nothing stable to reference. Setting it up front is cheap; retrofitting it means
#: renaming every constraint in a live database.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

#: All Anvex tables live in one non-``public`` Postgres schema (``CLAUDE.md`` §4).
SCHEMA: str = get_settings().postgres_schema

metadata = MetaData(schema=SCHEMA, naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    """Declarative base bound to the ``anvex`` schema and the naming convention.

    Subclass this for every table so that ``Base.metadata`` stays the single source of
    truth Alembic autogenerates against.
    """

    metadata = metadata
