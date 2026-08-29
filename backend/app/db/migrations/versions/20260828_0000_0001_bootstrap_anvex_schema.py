"""bootstrap anvex schema and pgcrypto

Creates the namespace every later migration builds inside, and the extension that
supplies the `gen_random_uuid()` server default used by every UUID primary key
(`CLAUDE.md` §4).

Revision ID: 0001_bootstrap
Revises:
Create Date: 2026-08-28

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from app.settings import get_settings

# revision identifiers, used by Alembic.
revision: str = "0001_bootstrap"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = get_settings().postgres_schema


def upgrade() -> None:
    # `env.py` has already bootstrapped the schema (it has to — `alembic_version` lives in
    # it). Declaring it here as well keeps the migration history a complete description of
    # the database, and both statements are idempotent.
    op.execute(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"')
    # Deliberately left in the default (public) schema so `gen_random_uuid()` resolves
    # without every table's server default having to qualify it.
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')


def downgrade() -> None:
    op.execute('DROP EXTENSION IF EXISTS "pgcrypto"')
    # The schema itself is intentionally *not* dropped: `alembic_version` lives inside it
    # (`version_table_schema`), and alembic deletes this revision's row immediately after
    # `downgrade()` returns — dropping the schema would take that table with it and the
    # downgrade would fail. An empty `anvex` schema is harmless, and `env.py` recreates it
    # on the next upgrade anyway.
