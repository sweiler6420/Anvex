"""The shared behaviour every repository inherits.

``app/repos/`` is the **only** place in the codebase where SQLAlchemy queries are written
(``CLAUDE.md`` §3). This module carries the mechanics that every repo would otherwise
retype — add-and-flush, apply-and-flush, delete-and-flush, "one row or ``None``", and the
count/limit/offset arithmetic behind :class:`app.schemas.pagination.Page` — so a concrete
repo module contains nothing but the queries that are actually about its aggregate.

**Two rules this base exists to enforce.**

*A repo never commits.* Every write helper here ends in ``flush()``, never ``commit()``.
Flushing is what makes a generated key (``gen_random_uuid()``, ``BIGSERIAL``) readable and
what makes a constraint violation surface *at the call that caused it* rather than at some
later commit; the transaction boundary still belongs to the service. The test harness's
``db_session`` relies on this too — it rolls the whole thing back at teardown.

*A repo never interprets.* These helpers return models, scalars, ``None`` or ``(rows,
total)``. "Not found" is a fact, not an error; turning it into a ``NotFoundError`` is the
service's job and turning *that* into a 404 is the middleware's. Nothing in this package
imports ``fastapi`` or ``app.domain.errors``, and an ``IntegrityError`` from a flush is
allowed to propagate untouched — ``StockRepo.delete`` on a watched stock is exactly that
case, and ANV-13 maps it to ``ConflictError``.

**Method naming, used identically by every repo in this package:**

============================  =====================================================
``get_*``                     one model or ``None``
``list_*``                    ``(rows, total)`` when paginated, ``list[Model]`` when not
``count_*`` / ``max_*``       a scalar
``*_exists``                  ``bool``
``create*`` / ``add_*``       insert and flush, returning the persisted model
``update`` / ``set_*``        mutate and flush
``delete*`` / ``remove_*``    delete and flush
``bulk_upsert``              ``INSERT ... ON CONFLICT DO UPDATE``, returning a row count
============================  =====================================================

**Sessions are passed in, never held** (``CLAUDE.md`` §3: "each method takes an
``AsyncSession``"). A repo instance is therefore stateless and safe to construct anywhere —
including as a module-level singleton — while the session stays owned by whoever owns the
transaction: the FastAPI dependency for a request, ``app.db.session.get_session`` for a
Celery task. A repo that stored a session would have to be built per request, which turns
every service into a factory and makes a repo that outlives its session a live footgun.

**Eager loading is mandatory, not an optimisation.** Under asyncio a lazy load raises
``MissingGreenlet``, so any relationship a caller will touch must be named in
``.options(selectinload(...))`` by the query that loads it. Concrete repos do that
explicitly; there is deliberately no "load everything" default here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession


class BaseRepo[ModelT]:
    """Query and write mechanics shared by every aggregate repository.

    Subclasses set :attr:`model` and add the queries their aggregate needs, reaching for
    the ``_``-prefixed helpers below rather than re-deriving pagination or existence
    checks::

        class WidgetRepo(BaseRepo[Widget]):
            model = Widget

            async def get_by_id(self, session: AsyncSession, widget_id: UUID) -> Widget | None:
                return await self._one_or_none(
                    session, select(Widget).where(Widget.widget_id == widget_id)
                )
    """

    #: The SQLAlchemy model this repository is responsible for. Set on the subclass.
    model: ClassVar[type[Any]]

    # -----------------------------------------------------------------------------------
    # Writes — every one of them flushes, none of them commits
    # -----------------------------------------------------------------------------------

    async def add(self, session: AsyncSession, instance: ModelT) -> ModelT:
        """Persist ``instance`` and flush, so server defaults and generated keys are set."""
        session.add(instance)
        await session.flush()
        return instance

    # There is deliberately no ORM `add_all`. The one place Anvex writes in bulk is ingest,
    # and it needs `INSERT ... ON CONFLICT` (see `StockDataRepo.bulk_upsert`) rather than a
    # unit-of-work flush that would fail the second time it ran.

    async def update(
        self, session: AsyncSession, instance: ModelT, values: Mapping[str, Any]
    ) -> ModelT:
        """Apply ``values`` to ``instance`` and flush.

        Purely mechanical: it sets exactly the keys it is given and decides nothing. That
        is what lets a service pass ``payload.model_dump(exclude_unset=True)`` straight
        through and have "clear the ISIN" (``{"isin": None}``) mean something different
        from "leave the ISIN alone" (the key absent) — a distinction the attribute alone
        cannot express (ANV-8).
        """
        for field, value in values.items():
            setattr(instance, field, value)
        await session.flush()
        return instance

    async def delete(self, session: AsyncSession, instance: ModelT) -> None:
        """Delete ``instance`` and flush.

        The flush is the point: a foreign key declared ``ON DELETE RESTRICT`` raises
        ``IntegrityError`` here, at the call that caused it, instead of at a commit three
        layers away. The exception is deliberately **not** caught (see the module
        docstring).
        """
        await session.delete(instance)
        await session.flush()

    # -----------------------------------------------------------------------------------
    # Reads
    # -----------------------------------------------------------------------------------

    async def _one_or_none(
        self, session: AsyncSession, stmt: Select[tuple[ModelT]]
    ) -> ModelT | None:
        """The first row of ``stmt`` as a model, or ``None`` when nothing matches."""
        result = await session.scalars(stmt.limit(1))
        return result.first()

    async def _all(self, session: AsyncSession, stmt: Select[tuple[ModelT]]) -> list[ModelT]:
        """Every row of ``stmt`` as a list of models."""
        result = await session.scalars(stmt)
        return list(result.unique())

    async def _count(self, session: AsyncSession, stmt: Select[Any]) -> int:
        """How many rows ``stmt`` matches, ignoring any ``limit``/``offset``.

        Counts over ``stmt`` as a subquery rather than rebuilding the ``WHERE`` clause, so
        the count provably matches the query it describes even when that query joins.
        ``ORDER BY`` is stripped first: it cannot change a count and Postgres rejects it
        inside some subqueries.
        """
        counted = select(func.count()).select_from(stmt.order_by(None).subquery())
        return int(await session.scalar(counted) or 0)

    async def _page(
        self,
        session: AsyncSession,
        stmt: Select[tuple[ModelT]],
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[ModelT], int]:
        """One window of ``stmt`` plus the total matching row count.

        Returns the two things :class:`app.schemas.pagination.Page` cannot be built
        without. Building the envelope itself is the *service's* job — a repo does not
        know about the API's contracts — so this hands back a plain tuple::

            rows, total = await repo.list_stocks(session, limit=limit, offset=offset)
            return Page[StockOut](items=rows, total=total, limit=limit, offset=offset)

        ``total`` is computed before the window is applied, so an ``offset`` past the end
        yields ``([], total)`` rather than ``([], 0)`` — which is what lets a client that
        paged too far render "page 9 of 3" instead of "no results".
        """
        total = await self._count(session, stmt)
        rows = await self._all(session, stmt.limit(limit).offset(offset))
        return rows, total

    async def _exists(self, session: AsyncSession, stmt: Select[Any]) -> bool:
        """Whether ``stmt`` matches anything, as one ``SELECT EXISTS (...)``.

        Cheaper than fetching the row and, more importantly, it says what it means: a
        duplicate check wants a yes/no, not an object nobody will look at.
        """
        return bool(await session.scalar(select(stmt.exists())))

    # -----------------------------------------------------------------------------------
    # Shared query fragments
    # -----------------------------------------------------------------------------------

    @staticmethod
    def _contains(term: str) -> str:
        """Escape ``term`` for use as a case-insensitive ``LIKE`` substring pattern.

        Without this a search for ``100%`` matches every row and a search for ``A_B``
        matches ``AxB``. ``\\`` is the escape character passed to ``ilike``.
        """
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"%{escaped}%"


__all__ = ["BaseRepo"]
