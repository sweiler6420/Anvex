"""The ``users`` table.

Persistence shape only — no password hashing, no validation, no "is this user allowed to"
(``CLAUDE.md`` §3). Hashing lives in ``app/utils/security.py`` (ANV-10) and the rules that
use it in ``app/services/user.py`` (ANV-12).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:  # pragma: no cover - import cycle broken for type checkers only
    from app.models.watchlist import Watchlist

#: Practical ceiling for a username. The old schema left this an unbounded ``String`` in
#: the model while the migration created ``VARCHAR(20)`` — the two disagreed, and 20 is
#: below the 7+ character minimum plus any realistic headroom ANV-12 needs.
USERNAME_MAX_LENGTH = 50

#: RFC 5321's maximum forward-path length. Chosen over an unbounded ``TEXT`` so a garbage
#: 10 MB "email" is rejected by the database, not merely by pydantic.
EMAIL_MAX_LENGTH = 320

#: bcrypt hashes are 60 characters; the headroom is for a future algorithm change.
PASSWORD_HASH_MAX_LENGTH = 255


class User(Base):
    """A registered account."""

    __tablename__ = "users"

    user_id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    username: Mapped[str] = mapped_column(String(USERNAME_MAX_LENGTH), unique=True)
    email: Mapped[str] = mapped_column(String(EMAIL_MAX_LENGTH), unique=True)
    #: The **hash**, never a plaintext password. ANV-8's ``UserOut`` must not expose it.
    password: Mapped[str] = mapped_column(String(PASSWORD_HASH_MAX_LENGTH))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )

    watchlists: Mapped[list[Watchlist]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        # The FK is ON DELETE CASCADE, so let Postgres do the deleting rather than having
        # the ORM load every child first only to issue a DELETE per row.
        passive_deletes=True,
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User {self.username!r}>"
