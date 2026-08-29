"""The ``watchlists`` and ``watchlist_data`` tables.

``watchlist_data`` is the association table between a watchlist and the stocks on it, plus
the ordinal a user has dragged each stock to. The *rule* for recomputing those ordinals is
pure and lives in ``app/domain/watchlist.py`` (ANV-15) — nothing here knows it.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:  # pragma: no cover - import cycle broken for type checkers only
    from app.models.stock import Stock
    from app.models.user import User

TITLE_MAX_LENGTH = 50

#: What a watchlist is called until its owner renames it.
DEFAULT_TITLE = "My Watchlist"


class Watchlist(Base):
    """A named, ordered collection of stocks belonging to one user."""

    __tablename__ = "watchlists"

    watchlist_id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    title: Mapped[str] = mapped_column(
        String(TITLE_MAX_LENGTH),
        server_default=text(f"'{DEFAULT_TITLE}'"),
    )
    #: Indexed because "list the watchlists I own" is the only way this table is ever read.
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"),
        index=True,
    )

    user: Mapped[User] = relationship(back_populates="watchlists")
    entries: Mapped[list[WatchlistData]] = relationship(
        back_populates="watchlist",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="WatchlistData.position",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Watchlist {self.title!r}>"


class WatchlistData(Base):
    """One stock's membership of one watchlist, at one position.

    **The composite primary key is real.** The old model declared
    ``__mapper_args__ = {"primary_key": [watchlist_id, stock_id]}``, which only told the
    ORM what to *treat* as a key — the table itself had none, so nothing stopped the same
    stock being added to the same watchlist twice, and there was no index to make the join
    in ANV-15's "watchlist with ordered stocks" query cheap.

    ``position`` is deliberately **not** unique within a watchlist: reordering swaps two
    ordinals, and a non-deferrable unique constraint would reject the intermediate state.
    """

    __tablename__ = "watchlist_data"

    watchlist_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("watchlists.watchlist_id", ondelete="CASCADE"),
        primary_key=True,
    )
    #: ``RESTRICT``, not ``CASCADE``: a stock is reference data, and deleting one that
    #: people are actively watching is a mistake worth surfacing rather than a silent
    #: rewrite of their watchlists.
    stock_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stocks.stock_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(Integer)

    watchlist: Mapped[Watchlist] = relationship(back_populates="entries")
    stock: Mapped[Stock] = relationship(back_populates="watchlist_entries")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<WatchlistData {self.watchlist_id} {self.stock_id} @{self.position}>"
