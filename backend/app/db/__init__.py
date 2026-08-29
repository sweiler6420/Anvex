"""Database plumbing: engine, session factory, declarative base, migrations.

Nothing in this package knows what a Stock is (``CLAUDE.md`` §3) — models live in
``app/models/`` and queries in ``app/repos/``.
"""

from app.db.base import NAMING_CONVENTION, SCHEMA, Base, metadata
from app.db.engine import create_engine, dispose_engine, get_engine
from app.db.health import ping
from app.db.session import get_session, get_sessionmaker

__all__ = [
    "NAMING_CONVENTION",
    "SCHEMA",
    "Base",
    "create_engine",
    "dispose_engine",
    "get_engine",
    "get_session",
    "get_sessionmaker",
    "metadata",
    "ping",
]
