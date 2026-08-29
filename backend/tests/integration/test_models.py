"""The six ORM models and the migration that creates them, against a real Postgres.

Three things are being proved here, and they are different things:

1. **The models round-trip.** Every column persists and comes back as the Python type its
   annotation promises — which is the contract ANV-8's pydantic schemas will be written
   against.
2. **The constraints are real.** Every uniqueness rule, every foreign key and every
   ``ON DELETE`` behaviour is asserted by *violating* it and watching Postgres refuse. A
   constraint that is only declared in a model file is not a constraint.
3. **The migration and the models agree.** ``upgrade``/``downgrade``/``upgrade`` is clean,
   and ``alembic revision --autogenerate`` against the applied migration produces an
   *empty* diff. That last one is the check that catches a hand-edited migration drifting
   from the models — which is exactly what this ticket's migration is.

Skips when ``db-test`` is unreachable, like every other database test.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import re
import shutil
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import UniqueConstraint, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import selectinload
from sqlalchemy.pool import NullPool

from app.db.base import SCHEMA
from app.models import Politician, Stock, StockData, User, Watchlist, WatchlistData
from tests import database
from tests.factories import (
    PoliticianFactory,
    StockDataFactory,
    StockFactory,
    UserFactory,
    WatchlistDataFactory,
    WatchlistFactory,
)

#: Every table this ticket creates. Used by the migration cycle test.
MODEL_TABLES = frozenset(
    {"politicians", "stocks", "stock_data", "users", "watchlists", "watchlist_data"}
)

#: Every model, for the assertions that apply to all of them equally.
ALL_MODELS = (User, Stock, StockData, Watchlist, WatchlistData, Politician)


async def _count(session: AsyncSession, model: type) -> int:
    return int(await session.scalar(select(func.count()).select_from(model.__table__)) or 0)


# ---------------------------------------------------------------------------------------
# users
# ---------------------------------------------------------------------------------------


class TestUser:
    async def test_round_trip(self, db_session: AsyncSession) -> None:
        user = await UserFactory().create(db_session, username="stephen", email="s@example.com")
        db_session.expunge_all()

        fetched = await db_session.get(User, user.user_id)
        assert fetched is not None
        assert (fetched.username, fetched.email) == ("stephen", "s@example.com")

    async def test_the_primary_key_comes_from_gen_random_uuid(
        self, db_session: AsyncSession
    ) -> None:
        """Nothing in the app generates ids; the server default does (`CLAUDE.md` §4)."""
        first = await UserFactory().create(db_session)
        second = await UserFactory().create(db_session)
        assert isinstance(first.user_id, uuid.UUID)
        assert first.user_id != second.user_id

    async def test_created_at_is_timezone_aware(self, db_session: AsyncSession) -> None:
        """The old column was declared naive in the migration; this is the correction."""
        user = await UserFactory().create(db_session)
        assert user.created_at.tzinfo is not None
        assert user.created_at.utcoffset() is not None

    async def test_duplicate_email_is_rejected(self, db_session: AsyncSession) -> None:
        user = await UserFactory().create(db_session)
        with pytest.raises(IntegrityError, match="uq_users_email"):
            await UserFactory().create(db_session, email=user.email)

    async def test_duplicate_username_is_rejected(self, db_session: AsyncSession) -> None:
        user = await UserFactory().create(db_session)
        with pytest.raises(IntegrityError, match="uq_users_username"):
            await UserFactory().create(db_session, username=user.username)


# ---------------------------------------------------------------------------------------
# stocks
# ---------------------------------------------------------------------------------------


class TestStock:
    async def test_round_trip(self, db_session: AsyncSession) -> None:
        stock = await StockFactory().create(
            db_session, ticker_symbol="AAPL", company="Apple Inc.", market="NASDAQ"
        )
        db_session.expunge_all()

        fetched = await db_session.get(Stock, stock.stock_id)
        assert fetched is not None
        assert (fetched.ticker_symbol, fetched.company, fetched.market) == (
            "AAPL",
            "Apple Inc.",
            "NASDAQ",
        )

    async def test_a_ticker_longer_than_five_characters_fits(
        self, db_session: AsyncSession
    ) -> None:
        """The headline widening: `VARCHAR(5)` could not hold a real suffixed ticker."""
        stock = await StockFactory().create(db_session, ticker_symbol="BRK.B-OLD")
        db_session.expunge_all()

        fetched = await db_session.get(Stock, stock.stock_id)
        assert fetched is not None
        assert fetched.ticker_symbol == "BRK.B-OLD"

    async def test_duplicate_ticker_is_rejected(self, db_session: AsyncSession) -> None:
        stock = await StockFactory().create(db_session)
        with pytest.raises(IntegrityError, match="uq_stocks_ticker_symbol"):
            await StockFactory().create(db_session, ticker_symbol=stock.ticker_symbol)

    async def test_two_share_classes_may_share_a_company_name(
        self, db_session: AsyncSession
    ) -> None:
        """Why `company` is indexed but **not** unique — GOOG and GOOGL are both Alphabet."""
        await StockFactory().create(db_session, ticker_symbol="GOOG", company="Alphabet Inc.")
        await StockFactory().create(db_session, ticker_symbol="GOOGL", company="Alphabet Inc.")
        assert await _count(db_session, Stock) == 2

    async def test_isin_is_optional(self, db_session: AsyncSession) -> None:
        """AlphaVantage returns no ISIN, so ingest must be able to create a stock without."""
        stock = await StockFactory().create(db_session, isin=None)
        assert stock.isin is None

    async def test_two_stocks_may_both_omit_their_isin(self, db_session: AsyncSession) -> None:
        """`isin` is unique, and Postgres does not collide NULLs — that is the point."""
        await StockFactory().create(db_session, isin=None)
        await StockFactory().create(db_session, isin=None)
        assert await _count(db_session, Stock) == 2

    async def test_duplicate_isin_is_rejected(self, db_session: AsyncSession) -> None:
        stock = await StockFactory().create(db_session)
        with pytest.raises(IntegrityError, match="uq_stocks_isin"):
            await StockFactory().create(db_session, isin=stock.isin)


# ---------------------------------------------------------------------------------------
# stock_data
# ---------------------------------------------------------------------------------------


class TestStockData:
    async def test_round_trip(self, db_session: AsyncSession) -> None:
        stock = await StockFactory().create(db_session)
        candle = await StockDataFactory().create(
            db_session,
            stock=stock,
            date=dt.date(2026, 3, 2),
            time=dt.time(9, 35),
            open_price=Decimal("101.0000"),
            high_price=Decimal("102.5000"),
            low_price=Decimal("100.7500"),
            close_price=Decimal("102.2500"),
            volume=123_456,
        )
        db_session.expunge_all()

        fetched = await db_session.get(StockData, candle.id)
        assert fetched is not None
        assert fetched.date == dt.date(2026, 3, 2)
        assert fetched.time == dt.time(9, 35)
        assert fetched.close_price == Decimal("102.2500")
        assert fetched.volume == 123_456

    async def test_the_id_is_a_generated_bigint(self, db_session: AsyncSession) -> None:
        """The old schema defaulted this from a sequence no migration ever created."""
        stock = await StockFactory().create(db_session)
        first = await StockDataFactory().create(db_session, stock=stock)
        second = await StockDataFactory().create(db_session, stock=stock)
        assert isinstance(first.id, int)
        assert second.id > first.id

    async def test_a_price_above_the_old_numeric_8_2_ceiling_fits(
        self, db_session: AsyncSession
    ) -> None:
        """`NUMERIC(8, 2)` overflows at 1,000,000 — BRK.A is within an order of magnitude."""
        stock = await StockFactory().create(db_session)
        candle = await StockDataFactory().create(
            db_session,
            stock=stock,
            open_price=Decimal("1250000.0000"),
            high_price=Decimal("1250000.0000"),
            low_price=Decimal("1250000.0000"),
            close_price=Decimal("1250000.0000"),
        )
        db_session.expunge_all()

        fetched = await db_session.get(StockData, candle.id)
        assert fetched is not None
        assert fetched.close_price == Decimal("1250000.0000")

    async def test_the_same_candle_twice_is_rejected(self, db_session: AsyncSession) -> None:
        """ANV-22's idempotency depends on this being a database rule, not a code habit."""
        stock = await StockFactory().create(db_session)
        candle = await StockDataFactory().create(db_session, stock=stock)
        with pytest.raises(IntegrityError, match="uq_stock_data_stock_id_date_time"):
            await StockDataFactory().create(
                db_session, stock=stock, date=candle.date, time=candle.time
            )

    async def test_two_stocks_may_share_a_timestamp(self, db_session: AsyncSession) -> None:
        """The constraint is per stock — every stock ticks at 09:35."""
        first, second = await StockFactory().create_many(db_session, 2)
        moment = {"date": dt.date(2026, 3, 2), "time": dt.time(9, 35)}
        await StockDataFactory().create(db_session, stock=first, **moment)
        await StockDataFactory().create(db_session, stock=second, **moment)
        assert await _count(db_session, StockData) == 2

    async def test_a_candle_needs_a_real_stock(self, db_session: AsyncSession) -> None:
        with pytest.raises(IntegrityError, match="fk_stock_data_stock_id_stocks"):
            await StockDataFactory().create(db_session, stock_id=uuid.uuid4())

    async def test_deleting_a_stock_takes_its_candles(self, db_session: AsyncSession) -> None:
        """`ON DELETE CASCADE`: a candle has no meaning without the security it prices."""
        stock = await StockFactory().create(db_session)
        await StockDataFactory().create_many(db_session, 3, stock=stock)
        assert await _count(db_session, StockData) == 3

        await db_session.delete(stock)
        await db_session.flush()
        assert await _count(db_session, StockData) == 0


# ---------------------------------------------------------------------------------------
# watchlists
# ---------------------------------------------------------------------------------------


class TestWatchlist:
    async def test_round_trip(self, db_session: AsyncSession) -> None:
        user = await UserFactory().create(db_session)
        watchlist = await WatchlistFactory().create(db_session, user=user, title="Semis")
        db_session.expunge_all()

        fetched = await db_session.get(Watchlist, watchlist.watchlist_id)
        assert fetched is not None
        assert fetched.title == "Semis"
        assert fetched.user_id == user.user_id

    async def test_the_title_has_a_server_default(self, db_session: AsyncSession) -> None:
        user = await UserFactory().create(db_session)
        watchlist = Watchlist(user_id=user.user_id)
        db_session.add(watchlist)
        await db_session.flush()
        await db_session.refresh(watchlist)
        assert watchlist.title == "My Watchlist"

    async def test_a_watchlist_needs_a_real_user(self, db_session: AsyncSession) -> None:
        with pytest.raises(IntegrityError, match="fk_watchlists_user_id_users"):
            await WatchlistFactory().create(db_session, user_id=uuid.uuid4())

    async def test_deleting_a_user_takes_their_watchlists_and_entries(
        self, db_session: AsyncSession
    ) -> None:
        """`ON DELETE CASCADE` twice over: a watchlist is meaningless without its owner."""
        user = await UserFactory().create(db_session)
        stock = await StockFactory().create(db_session)
        watchlist = await WatchlistFactory().create(db_session, user=user)
        await WatchlistDataFactory().create(db_session, watchlist=watchlist, stock=stock)

        await db_session.delete(user)
        await db_session.flush()

        assert await _count(db_session, Watchlist) == 0
        assert await _count(db_session, WatchlistData) == 0
        assert await _count(db_session, Stock) == 1, "the stock itself must survive"


# ---------------------------------------------------------------------------------------
# watchlist_data — the composite primary key
# ---------------------------------------------------------------------------------------


class TestWatchlistDataCompositeKey:
    """The headline defect this ticket fixes.

    The old model faked its key with `__mapper_args__ = {"primary_key": [...]}`, which told
    the ORM what to treat as a key and left the *table* with none — so the same stock could
    be added to the same watchlist any number of times.
    """

    def test_the_table_really_has_a_two_column_primary_key(self) -> None:
        primary_key = WatchlistData.__table__.primary_key
        assert [column.name for column in primary_key.columns] == ["watchlist_id", "stock_id"]
        assert primary_key.name == "pk_watchlist_data"

    async def test_the_pair_addresses_a_row(self, db_session: AsyncSession) -> None:
        """`session.get` with the tuple works — the ORM key and the table key are one."""
        user = await UserFactory().create(db_session)
        stock = await StockFactory().create(db_session)
        watchlist = await WatchlistFactory().create(db_session, user=user)
        await WatchlistDataFactory().create(
            db_session, watchlist=watchlist, stock=stock, position=0
        )
        db_session.expunge_all()

        fetched = await db_session.get(WatchlistData, (watchlist.watchlist_id, stock.stock_id))
        assert fetched is not None
        assert fetched.position == 0

    async def test_the_same_stock_twice_on_one_watchlist_is_rejected(
        self, db_session: AsyncSession
    ) -> None:
        user = await UserFactory().create(db_session)
        stock = await StockFactory().create(db_session)
        watchlist = await WatchlistFactory().create(db_session, user=user)
        await WatchlistDataFactory().create(db_session, watchlist=watchlist, stock=stock)

        duplicate = WatchlistData(
            watchlist_id=watchlist.watchlist_id, stock_id=stock.stock_id, position=9
        )
        db_session.add(duplicate)
        with pytest.raises(IntegrityError, match="pk_watchlist_data"):
            await db_session.flush()

    async def test_one_stock_may_appear_on_two_watchlists(self, db_session: AsyncSession) -> None:
        user = await UserFactory().create(db_session)
        stock = await StockFactory().create(db_session)
        first, second = await WatchlistFactory().create_many(db_session, 2, user=user)
        await WatchlistDataFactory().create(db_session, watchlist=first, stock=stock)
        await WatchlistDataFactory().create(db_session, watchlist=second, stock=stock)
        assert await _count(db_session, WatchlistData) == 2

    async def test_two_entries_may_share_a_position(self, db_session: AsyncSession) -> None:
        """`position` is deliberately not unique — ANV-15's reorder swaps ordinals, and a
        non-deferrable unique constraint would reject the intermediate state."""
        user = await UserFactory().create(db_session)
        first_stock, second_stock = await StockFactory().create_many(db_session, 2)
        watchlist = await WatchlistFactory().create(db_session, user=user)
        await WatchlistDataFactory().create(
            db_session, watchlist=watchlist, stock=first_stock, position=0
        )
        await WatchlistDataFactory().create(
            db_session, watchlist=watchlist, stock=second_stock, position=0
        )
        assert await _count(db_session, WatchlistData) == 2

    async def test_deleting_a_watched_stock_is_refused(self, db_session: AsyncSession) -> None:
        """`ON DELETE RESTRICT`: reference data someone depends on is not silently removed."""
        user = await UserFactory().create(db_session)
        stock = await StockFactory().create(db_session)
        watchlist = await WatchlistFactory().create(db_session, user=user)
        await WatchlistDataFactory().create(db_session, watchlist=watchlist, stock=stock)

        await db_session.delete(stock)
        with pytest.raises(IntegrityError, match="fk_watchlist_data_stock_id_stocks"):
            await db_session.flush()

    async def test_deleting_a_watchlist_takes_its_entries(self, db_session: AsyncSession) -> None:
        user = await UserFactory().create(db_session)
        stock = await StockFactory().create(db_session)
        watchlist = await WatchlistFactory().create(db_session, user=user)
        await WatchlistDataFactory().create(db_session, watchlist=watchlist, stock=stock)

        await db_session.delete(watchlist)
        await db_session.flush()
        assert await _count(db_session, WatchlistData) == 0
        assert await _count(db_session, Stock) == 1


# ---------------------------------------------------------------------------------------
# politicians
# ---------------------------------------------------------------------------------------


class TestPolitician:
    async def test_round_trip(self, db_session: AsyncSession) -> None:
        politician = await PoliticianFactory().create(
            db_session,
            politician_id="N000147",
            first_name="Nancy",
            last_name="Pelosi",
            state="CA",
            chamber="House",
            party="Democrat",
            dob=dt.date(1940, 3, 26),
        )
        db_session.expunge_all()

        fetched = await db_session.get(Politician, politician.politician_id)
        assert fetched is not None
        assert (fetched.first_name, fetched.last_name) == ("Nancy", "Pelosi")
        assert fetched.dob == dt.date(1940, 3, 26)

    async def test_the_optional_columns_really_are_optional(
        self, db_session: AsyncSession
    ) -> None:
        """The roster omits these for some rows; ANV-16 must not have to invent them."""
        politician = await PoliticianFactory().create(
            db_session, state=None, chamber=None, dob=None, gender=None
        )
        db_session.expunge_all()

        fetched = await db_session.get(Politician, politician.politician_id)
        assert fetched is not None
        assert (fetched.state, fetched.chamber, fetched.dob, fetched.gender) == (
            None,
            None,
            None,
            None,
        )

    async def test_the_external_id_is_the_primary_key(self, db_session: AsyncSession) -> None:
        """Which is what lets ANV-16's seed be idempotent."""
        await PoliticianFactory().create(db_session, politician_id="P000001")
        with pytest.raises(IntegrityError, match="pk_politicians"):
            await PoliticianFactory().create(db_session, politician_id="P000001")


# ---------------------------------------------------------------------------------------
# relationships
# ---------------------------------------------------------------------------------------


class TestRelationships:
    """Every navigation ANV-9's repos will need, eagerly loaded.

    Lazy loading is impossible under asyncio, so each of these uses `selectinload` — the
    same thing the repos will have to do.
    """

    async def test_the_whole_graph_resolves_in_one_query_chain(
        self, db_session: AsyncSession
    ) -> None:
        user = await UserFactory().create(db_session)
        stock = await StockFactory().create(db_session, ticker_symbol="NVDA")
        watchlist = await WatchlistFactory().create(db_session, user=user, title="Semis")
        await WatchlistDataFactory().create(
            db_session, watchlist=watchlist, stock=stock, position=0
        )
        db_session.expunge_all()

        loaded = await db_session.scalar(
            select(User)
            .where(User.user_id == user.user_id)
            .options(
                selectinload(User.watchlists)
                .selectinload(Watchlist.entries)
                .selectinload(WatchlistData.stock)
            )
        )
        assert loaded is not None
        assert [w.title for w in loaded.watchlists] == ["Semis"]
        assert [e.stock.ticker_symbol for e in loaded.watchlists[0].entries] == ["NVDA"]

    async def test_watchlist_entries_come_back_in_position_order(
        self, db_session: AsyncSession
    ) -> None:
        """`order_by` lives on the relationship, so no caller has to remember to sort."""
        user = await UserFactory().create(db_session)
        watchlist = await WatchlistFactory().create(db_session, user=user)
        stocks = await StockFactory().create_many(db_session, 3)
        # Inserted deliberately out of order.
        for position, stock in zip((2, 0, 1), stocks, strict=True):
            await WatchlistDataFactory().create(
                db_session, watchlist=watchlist, stock=stock, position=position
            )
        db_session.expunge_all()

        loaded = await db_session.scalar(
            select(Watchlist)
            .where(Watchlist.watchlist_id == watchlist.watchlist_id)
            .options(selectinload(Watchlist.entries))
        )
        assert loaded is not None
        assert [entry.position for entry in loaded.entries] == [0, 1, 2]

    async def test_a_candle_navigates_back_to_its_stock(self, db_session: AsyncSession) -> None:
        stock = await StockFactory().create(db_session, ticker_symbol="MSFT")
        candle = await StockDataFactory().create(db_session, stock=stock)
        db_session.expunge_all()

        loaded = await db_session.scalar(
            select(StockData)
            .where(StockData.id == candle.id)
            .options(selectinload(StockData.stock))
        )
        assert loaded is not None
        assert loaded.stock.ticker_symbol == "MSFT"

    async def test_assigning_the_relationship_populates_the_foreign_key(
        self, db_session: AsyncSession
    ) -> None:
        """`WatchlistFactory().create(session, user=user)` really does set `user_id`."""
        user = await UserFactory().create(db_session)
        watchlist = await WatchlistFactory().create(db_session, user=user)
        assert watchlist.user_id == user.user_id


# ---------------------------------------------------------------------------------------
# the migration
# ---------------------------------------------------------------------------------------


def _upgrade_body(script: Path) -> list[str]:
    """The executable lines of a revision's ``upgrade()``, comments and blanks removed."""
    source = script.read_text(encoding="utf-8")
    body = re.split(r"^def upgrade\(\).*:$", source, flags=re.MULTILINE)[1]
    body = re.split(r"^def downgrade\(\)", body, flags=re.MULTILINE)[0]
    return [
        line.strip()
        for line in body.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _sandboxed_migrations(tmp_path: Path, url: str) -> Config:
    """A copy of the migration tree that ``alembic revision`` may safely write into.

    Autogenerating against the real ``app/db/migrations/versions/`` would leave a stray
    revision in the repository every time the suite ran. The copy behaves identically —
    ``env.py`` still imports ``app.models`` from the source tree — and is thrown away with
    ``tmp_path``. It is built **without** ``alembic.ini``, for the reasons
    :func:`tests.database.alembic_config` documents; that also keeps the ruff post-write
    hook out of the test.
    """
    destination = tmp_path / "migrations"
    shutil.copytree(
        database.MIGRATIONS_DIR,
        destination,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    config = Config()
    config.set_main_option("script_location", str(destination))
    config.attributes["sqlalchemy.url"] = url
    return config


def test_the_models_and_the_migration_agree(throwaway_database_url: str, tmp_path: Path) -> None:
    """``alembic revision --autogenerate`` finds **nothing** to do.

    This is the test that earns the hand-formatted migration. Autogenerate compares the
    live database against ``Base.metadata`` with exactly the options ``env.py`` configures
    (``compare_type``, ``compare_server_default``, ``include_schemas`` restricted to
    ``anvex``), so an empty result means every column type, nullability, server default,
    index, unique constraint and foreign key in the migration matches the models — in both
    directions. Any hand edit that drifts fails here.
    """
    config = _sandboxed_migrations(tmp_path, throwaway_database_url)
    versions = tmp_path / "migrations" / "versions"

    command.upgrade(config, "head")

    before = set(versions.glob("*.py"))
    command.revision(config, message="drift check", autogenerate=True)
    generated = set(versions.glob("*.py")) - before
    assert len(generated) == 1, "autogenerate should have written exactly one revision"

    body = _upgrade_body(generated.pop())
    assert body == ["pass"], (
        "autogenerate proposed changes, so the migration and the models have drifted:\n"
        + "\n".join(body)
    )


def test_upgrade_downgrade_upgrade_is_clean(throwaway_database_url: str) -> None:
    """The history reverses and re-applies. A downgrade nobody runs is one nobody trusts."""
    config = database.alembic_config(throwaway_database_url)

    command.upgrade(config, "head")
    assert _model_tables_present(throwaway_database_url) == MODEL_TABLES

    command.downgrade(config, "base")
    assert _model_tables_present(throwaway_database_url) == frozenset()

    command.upgrade(config, "head")
    assert _model_tables_present(throwaway_database_url) == MODEL_TABLES


def _model_tables_present(url: str) -> frozenset[str]:
    """Which of :data:`MODEL_TABLES` currently exist in the ``anvex`` schema."""

    async def _run() -> frozenset[str]:
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                rows = await connection.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = :schema"),
                    {"schema": SCHEMA},
                )
                return frozenset({row[0] for row in rows}) & MODEL_TABLES
        finally:
            await engine.dispose()

    return asyncio.run(_run())


class TestSchemaShape:
    """Assertions about ``Base.metadata`` itself — pure, no database needed."""

    def test_every_model_lives_in_the_anvex_schema(self) -> None:
        """No model sets `__table_args__ = {"schema": ...}`; the metadata carries it."""
        for model in ALL_MODELS:
            assert model.__table__.schema == SCHEMA

    def test_every_constraint_is_named_by_the_convention(self) -> None:
        """Postgres never gets to invent a name alembic cannot reproduce (`CLAUDE.md` §4)."""
        prefixes = ("pk_", "fk_", "uq_", "ix_", "ck_")
        for model in ALL_MODELS:
            table = model.__table__
            names = [c.name for c in table.constraints] + [i.name for i in table.indexes]
            assert all(name and str(name).startswith(prefixes) for name in names), names

    def test_stock_data_has_the_ingest_constraint_and_the_range_index(self) -> None:
        table = StockData.__table__
        unique = {
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        assert ("stock_id", "date", "time") in unique, "ANV-22's upsert conflict target"
        indexes = {tuple(column.name for column in index.columns) for index in table.indexes}
        assert ("date",) in indexes, "cross-stock date windows need their own index"

    def test_the_foreign_keys_declare_their_delete_behaviour(self) -> None:
        """The old schema declared none, so deleting a user simply failed."""
        expected = {
            ("stock_data", "stock_id"): "CASCADE",
            ("watchlists", "user_id"): "CASCADE",
            ("watchlist_data", "watchlist_id"): "CASCADE",
            ("watchlist_data", "stock_id"): "RESTRICT",
        }
        actual = {
            (key.parent.table.name, key.parent.name): key.ondelete
            for model in (StockData, Watchlist, WatchlistData)
            for key in model.__table__.foreign_keys
        }
        assert actual == expected

    def test_the_mapper_key_and_the_table_key_are_the_same_thing(self) -> None:
        """What `__mapper_args__ = {"primary_key": [...]}` used to fake."""
        mapper_key = [column.name for column in inspect(WatchlistData).primary_key]
        table_key = [column.name for column in WatchlistData.__table__.primary_key.columns]
        assert mapper_key == table_key == ["watchlist_id", "stock_id"]
