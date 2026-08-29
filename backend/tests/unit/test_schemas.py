"""Unit tests for ``app/schemas/`` — the API's public contract.

Pure tier (``CLAUDE.md`` §6): no fixtures, no app, no database. ORM instances are built in
memory, which is all ``from_attributes`` needs — a pydantic model does not care whether the
object it reads attributes off is attached to a session.

Four things are being proved, and they are different things:

1. **The password digest cannot escape.** :class:`TestPasswordNeverEscapes` walks every
   pydantic model in the ``app.schemas`` package — not a hand-written list — so a schema
   added by a future ticket is checked the moment it exists.
2. **The edge rejects what the database would.** Every length cap is asserted both
   structurally (the constraint is declared, against the model's own constant) and
   behaviourally (the boundary value passes, one more character fails), so a request is
   answered with a 422 instead of a ``StringDataRightTruncation``.
3. **Money keeps its precision.** Prices are ``Decimal`` end to end, including through a
   JSON round trip. A float would already have lost the fourth decimal place by the time
   anything asserted on it.
4. **Optionality tells the truth.** Only the five genuinely nullable columns are ``| None``
   on an output schema.
"""

from __future__ import annotations

import datetime as dt
import importlib
import pkgutil
import types
import typing
import uuid
from decimal import Decimal
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel, ValidationError

import app.schemas
from app.models import Politician, Stock, StockData, User, Watchlist, WatchlistData
from app.models.politician import NAME_MAX_LENGTH as POLITICIAN_NAME_MAX_LENGTH
from app.models.stock import (
    COMPANY_MAX_LENGTH,
    ISIN_LENGTH,
    MARKET_MAX_LENGTH,
    TICKER_MAX_LENGTH,
)
from app.models.user import EMAIL_MAX_LENGTH, USERNAME_MAX_LENGTH
from app.models.watchlist import DEFAULT_TITLE, TITLE_MAX_LENGTH
from app.schemas import (
    MAX_PAGE_LIMIT,
    Page,
    PasswordChange,
    PoliticianCreate,
    PoliticianOut,
    RecoveryRequest,
    RefreshRequest,
    StockCreate,
    StockDataCreate,
    StockDataOut,
    StockDataPoint,
    StockOut,
    StockUpdate,
    TokenPair,
    TokenPayload,
    UserCreate,
    UserOut,
    UserUpdate,
    WatchlistCreate,
    WatchlistDetailOut,
    WatchlistEntryOut,
    WatchlistOut,
    WatchlistUpdate,
)
from app.schemas.user import PASSWORD_MIN_LENGTH, USERNAME_MIN_LENGTH

#: A stand-in for the bcrypt digest stored in ``users.password``. Distinctive on purpose:
#: the leak tests search serialised output for this exact string.
PASSWORD_HASH = "$2b$12$THISMUSTNEVERAPPEARINANYRESPONSEBODYWHATSOEVER0123456789ab"

#: The resource modules. ``errors`` and ``health`` are framework-level bodies from ANV-4
#: and are not built from ORM rows, so the ``from_attributes`` and nullability rules below
#: do not apply to them.
RESOURCE_MODULES = frozenset(
    {"auth", "pagination", "politician", "stock", "stock_data", "user", "watchlist"}
)

#: The **only** nullable columns in the whole schema (ANV-7), and therefore the only
#: output fields allowed to be ``| None``.
NULLABLE_OUTPUT_FIELDS = frozenset(
    {
        ("StockOut", "isin"),
        ("PoliticianOut", "state"),
        ("PoliticianOut", "chamber"),
        ("PoliticianOut", "dob"),
        ("PoliticianOut", "gender"),
    }
)


# ---------------------------------------------------------------------------------------
# discovery — every model in the package, found rather than listed
# ---------------------------------------------------------------------------------------


def _all_schema_models() -> dict[str, type[BaseModel]]:
    """Every pydantic model defined anywhere under ``app/schemas/``, keyed by name.

    Walks the package with :mod:`pkgutil` instead of reading ``__all__`` so a model that a
    future ticket forgets to export is still covered by the leak test below — an
    unexported schema is exactly the one nobody would think to check by hand.
    """
    found: dict[str, type[BaseModel]] = {}
    for info in pkgutil.iter_modules(app.schemas.__path__):
        module = importlib.import_module(f"{app.schemas.__name__}.{info.name}")
        for name, obj in vars(module).items():
            if (
                isinstance(obj, type)
                and issubclass(obj, BaseModel)
                and obj.__module__ == module.__name__
            ):
                found[name] = obj
    return found


SCHEMA_MODELS = _all_schema_models()

#: The output schemas built from an ORM row.
ORM_OUTPUT_SCHEMAS = [
    PoliticianOut,
    StockDataOut,
    StockDataPoint,
    StockOut,
    UserOut,
    WatchlistDetailOut,
    WatchlistEntryOut,
    WatchlistOut,
]


def _allows_none(annotation: Any) -> bool:
    """Whether ``annotation`` accepts ``None`` — ``X | None`` in either union spelling."""
    if annotation is None or annotation is type(None):
        return True
    if typing.get_origin(annotation) in (typing.Union, types.UnionType):
        return any(arg is type(None) for arg in typing.get_args(annotation))
    return False


def _max_length_in_metadata(items: Any) -> int | None:
    """First ``max_length`` among constraint objects, including nested ``FieldInfo`` ones."""
    for item in items:
        length = getattr(item, "max_length", None)
        if length is not None:
            return int(length)
        nested = getattr(item, "metadata", None)
        if nested:
            found = _max_length_in_metadata(nested)
            if found is not None:
                return found
    return None


def _max_length_in_annotation(annotation: Any) -> int | None:
    """Dig a ``max_length`` out of an annotation, through ``Annotated`` and ``X | None``."""
    found = _max_length_in_metadata(getattr(annotation, "__metadata__", ()))
    if found is not None:
        return found
    for arg in typing.get_args(annotation):
        found = _max_length_in_annotation(arg)
        if found is not None:
            return found
    return None


def _max_length(model: type[BaseModel], field: str) -> int | None:
    """The declared ``max_length`` of ``model.field``, wherever pydantic recorded it.

    A plain ``Ticker`` field records its constraints on the ``FieldInfo``; an optional
    ``Isin | None`` keeps them inside the union's ``Annotated`` member instead. Both spell
    the same contract, so the test that compares a cap against its column has to see both.
    """
    info = model.model_fields[field]
    found = _max_length_in_metadata(info.metadata)
    return found if found is not None else _max_length_in_annotation(info.annotation)


# ---------------------------------------------------------------------------------------
# in-memory model instances — no session required
# ---------------------------------------------------------------------------------------


def make_user(**overrides: Any) -> User:
    """A transient :class:`~app.models.User`, hash and all."""
    values: dict[str, Any] = {
        "user_id": uuid.UUID("7c1a4b3e-0000-4000-8000-000000000001"),
        "username": "stephen",
        "email": "stephen@example.com",
        "password": PASSWORD_HASH,
        "created_at": dt.datetime(2026, 1, 1, 14, 30, tzinfo=dt.UTC),
    }
    values.update(overrides)
    return User(**values)


def make_stock(**overrides: Any) -> Stock:
    values: dict[str, Any] = {
        "stock_id": uuid.UUID("7c1a4b3e-0000-4000-8000-000000000002"),
        "ticker_symbol": "NVDA",
        "company": "NVIDIA Corporation",
        "market": "NASDAQ",
        "isin": "US67066G1040",
    }
    values.update(overrides)
    return Stock(**values)


def make_candle(**overrides: Any) -> StockData:
    values: dict[str, Any] = {
        "id": 42,
        "stock_id": uuid.UUID("7c1a4b3e-0000-4000-8000-000000000002"),
        "date": dt.date(2026, 1, 5),
        "time": dt.time(9, 30),
        "open_price": Decimal("1234.5678"),
        "high_price": Decimal("1240.0001"),
        "low_price": Decimal("1230.9999"),
        "close_price": Decimal("1234.5678"),
        "volume": 1_048_576,
    }
    values.update(overrides)
    return StockData(**values)


def make_watchlist() -> Watchlist:
    """A watchlist holding one entry, which holds one stock — the whole eager-load chain."""
    stock = make_stock()
    watchlist_id = uuid.UUID("7c1a4b3e-0000-4000-8000-000000000003")
    entry = WatchlistData(
        watchlist_id=watchlist_id,
        stock_id=stock.stock_id,
        position=0,
        stock=stock,
    )
    return Watchlist(
        watchlist_id=watchlist_id,
        user_id=uuid.UUID("7c1a4b3e-0000-4000-8000-000000000001"),
        title="Semiconductors",
        entries=[entry],
    )


def make_politician(**overrides: Any) -> Politician:
    values: dict[str, Any] = {
        "politician_id": "N000147",
        "first_name": "Nancy",
        "last_name": "Pelosi",
        "party": "Democrat",
        "state": "CA",
        "chamber": "House of Representatives",
        "dob": dt.date(1940, 3, 26),
        "gender": "Female",
    }
    values.update(overrides)
    return Politician(**values)


# ---------------------------------------------------------------------------------------
# the password digest
# ---------------------------------------------------------------------------------------


class TestPasswordNeverEscapes:
    """``users.password`` is the bcrypt hash. It may be *accepted*; it is never *returned*.

    The allowlist below is the whole of the permitted surface. A new schema with a
    password-ish field fails :meth:`test_only_input_schemas_mention_a_password` until
    somebody adds it here deliberately — which is the point: the leak becomes a decision
    somebody had to write down, not an oversight nobody reviewed.
    """

    #: schema name -> the password fields it is allowed to declare. Inputs only.
    ALLOWED: ClassVar[dict[str, set[str]]] = {
        "UserCreate": {"password"},
        "PasswordChange": {"current_password", "new_password"},
    }

    def test_the_walk_actually_found_the_schemas(self) -> None:
        """Guards the guard: an empty walk would make every test below vacuously pass."""
        assert {"UserOut", "UserCreate", "TokenPair", "Page"} <= set(SCHEMA_MODELS)

    def test_only_input_schemas_mention_a_password(self) -> None:
        offenders = {
            name: sorted(field for field in model.model_fields if "password" in field.lower())
            for name, model in SCHEMA_MODELS.items()
        }
        actual = {name: set(fields) for name, fields in offenders.items() if fields}
        expected = {name: set(fields) for name, fields in self.ALLOWED.items()}
        assert actual == expected, (
            "a schema grew a password field. If it is an input, add it to "
            "TestPasswordNeverEscapes.ALLOWED; if it is an output, remove the field."
        )

    def test_no_output_schema_declares_a_password_field(self) -> None:
        """Belt and braces: nothing named ``*Out`` may carry one, allowlist or not."""
        for name, model in SCHEMA_MODELS.items():
            if name.endswith("Out"):
                leaking = [field for field in model.model_fields if "password" in field.lower()]
                assert leaking == [], f"{name} would serialise {leaking}"

    @pytest.mark.parametrize("schema", ORM_OUTPUT_SCHEMAS, ids=lambda s: s.__name__)
    def test_no_orm_output_schema_can_be_given_a_password(
        self, schema: type[BaseModel]
    ) -> None:
        """Even by name: pydantic ignores unknown keys, so the field must simply not exist."""
        assert "password" not in schema.model_fields

    def test_user_out_drops_the_digest_from_a_real_model_instance(self) -> None:
        user = make_user()
        assert user.password == PASSWORD_HASH  # the source really does carry it

        out = UserOut.model_validate(user)

        assert "password" not in out.model_dump()
        assert PASSWORD_HASH not in out.model_dump_json()
        assert set(out.model_dump()) == {"user_id", "username", "email", "created_at"}

    def test_the_digest_does_not_survive_a_page_of_users(self) -> None:
        """The envelope must not reintroduce what the item schema removed."""
        page = Page[UserOut](
            items=[UserOut.model_validate(make_user())],
            total=1,
            limit=50,
            offset=0,
        )
        assert PASSWORD_HASH not in page.model_dump_json()

    def test_a_create_body_does_not_echo_the_password_into_an_output(self) -> None:
        """``UserCreate`` holds a plaintext password; ``UserOut`` cannot be built from it."""
        created = UserCreate(username="stephen", email="s@example.com", password="hunter22")
        assert created.password == "hunter22"
        with pytest.raises(ValidationError):
            UserOut.model_validate(created.model_dump())


# ---------------------------------------------------------------------------------------
# the shape of the contract
# ---------------------------------------------------------------------------------------


class TestContractShape:
    @pytest.mark.parametrize("schema", ORM_OUTPUT_SCHEMAS, ids=lambda s: s.__name__)
    def test_output_schemas_read_from_attributes(self, schema: type[BaseModel]) -> None:
        """Without this every handler would have to hand-build a dict from its model."""
        assert schema.model_config.get("from_attributes") is True

    def test_only_the_five_nullable_columns_are_optional_on_an_output(self) -> None:
        """A defensive ``| None`` on a ``NOT NULL`` column is a null check clients pay for."""
        optional = {
            (name, field)
            for name, model in SCHEMA_MODELS.items()
            if name.endswith("Out") and model.__module__.rsplit(".", 1)[-1] in RESOURCE_MODULES
            for field, info in model.model_fields.items()
            if _allows_none(info.annotation)
        }
        assert optional == set(NULLABLE_OUTPUT_FIELDS)

    def test_every_exported_name_resolves(self) -> None:
        for name in app.schemas.__all__:
            assert hasattr(app.schemas, name), name

    def test_the_token_pair_keys_are_the_ones_the_frontend_reads(self) -> None:
        pair = TokenPair(access_token="a", refresh_token="r")
        assert pair.model_dump() == {
            "access_token": "a",
            "refresh_token": "r",
            "token_type": "bearer",
        }

    def test_a_token_payload_names_which_half_of_the_pair_it_is(self) -> None:
        """The old API accepted an access token at ``/v1/refresh``; this claim stops that."""
        payload = TokenPayload(
            sub=str(uuid.uuid4()),
            exp=1_800_000_000,
            iat=1_799_999_100,
            type="refresh",
        )
        assert payload.type == "refresh"
        with pytest.raises(ValidationError):
            TokenPayload(sub=str(uuid.uuid4()), exp=1, iat=1, type="session")

    def test_a_token_payload_subject_is_a_uuid(self) -> None:
        user_id = uuid.uuid4()
        assert TokenPayload(sub=str(user_id), exp=1, iat=1, type="access").sub == user_id
        with pytest.raises(ValidationError):
            TokenPayload(sub="not-a-uuid", exp=1, iat=1, type="access")

    def test_a_refresh_body_rejects_an_empty_token(self) -> None:
        assert RefreshRequest(refresh_token="abc").refresh_token == "abc"
        with pytest.raises(ValidationError):
            RefreshRequest(refresh_token="")

    def test_a_recovery_request_is_keyed_on_the_username(self) -> None:
        assert RecoveryRequest(username="stephen").username == "stephen"

    def test_a_new_watchlist_defaults_to_the_columns_own_default(self) -> None:
        assert WatchlistCreate().title == DEFAULT_TITLE

    def test_an_update_schema_leaves_absent_fields_unset(self) -> None:
        """``exclude_unset`` is how a service tells "unchanged" from "set to null"."""
        update = StockUpdate(company="Renamed Inc.")
        assert update.model_dump(exclude_unset=True) == {"company": "Renamed Inc."}
        cleared = StockUpdate(isin=None)
        assert cleared.model_dump(exclude_unset=True) == {"isin": None}

    def test_a_watchlist_update_only_offers_the_title(self) -> None:
        assert set(WatchlistUpdate.model_fields) == {"title"}

    def test_a_user_update_cannot_change_the_password(self) -> None:
        assert set(UserUpdate.model_fields) == {"username", "email"}

    def test_a_candle_body_carries_no_stock_id(self) -> None:
        """The parent comes from the path, not from the body — one source of truth."""
        assert "stock_id" not in StockDataCreate.model_fields


# ---------------------------------------------------------------------------------------
# validation: the length caps and the email format
# ---------------------------------------------------------------------------------------


def _user_create(**overrides: Any) -> dict[str, Any]:
    return {
        "username": "stephen",
        "email": "stephen@example.com",
        "password": "hunter22",
        **overrides,
    }


def _stock_create(**overrides: Any) -> dict[str, Any]:
    return {
        "ticker_symbol": "NVDA",
        "company": "NVIDIA Corporation",
        "market": "NASDAQ",
        "isin": "US67066G1040",
        **overrides,
    }


def _politician_create(**overrides: Any) -> dict[str, Any]:
    return {
        "politician_id": "N000147",
        "first_name": "Nancy",
        "last_name": "Pelosi",
        "party": "Democrat",
        **overrides,
    }


#: ``(schema, field, cap, payload builder)`` — every ceiling ANV-7 handed to this ticket.
LENGTH_CAPS = [
    (UserCreate, "username", USERNAME_MAX_LENGTH, _user_create),
    (UserCreate, "email", EMAIL_MAX_LENGTH, _user_create),
    (StockCreate, "ticker_symbol", TICKER_MAX_LENGTH, _stock_create),
    (StockCreate, "company", COMPANY_MAX_LENGTH, _stock_create),
    (StockCreate, "market", MARKET_MAX_LENGTH, _stock_create),
    (StockCreate, "isin", ISIN_LENGTH, _stock_create),
    (WatchlistCreate, "title", TITLE_MAX_LENGTH, lambda **kw: dict(kw)),
    (PoliticianCreate, "first_name", POLITICIAN_NAME_MAX_LENGTH, _politician_create),
    (PoliticianCreate, "last_name", POLITICIAN_NAME_MAX_LENGTH, _politician_create),
]


class TestLengthCaps:
    """Each cap is asserted twice: that it is declared, and that it bites.

    The declaration is compared against the *model's* constant rather than a literal, so
    widening a column and forgetting the schema fails here rather than in production.
    """

    @pytest.mark.parametrize(
        ("schema", "field", "cap"),
        [(schema, field, cap) for schema, field, cap, _ in LENGTH_CAPS],
        ids=[f"{schema.__name__}.{field}" for schema, field, _, _ in LENGTH_CAPS],
    )
    def test_the_cap_matches_the_column(
        self, schema: type[BaseModel], field: str, cap: int
    ) -> None:
        assert _max_length(schema, field) == cap

    @pytest.mark.parametrize(
        ("schema", "field", "cap", "build"),
        LENGTH_CAPS,
        ids=[f"{schema.__name__}.{field}" for schema, field, _, _ in LENGTH_CAPS],
    )
    def test_one_character_past_the_cap_is_rejected(
        self,
        schema: type[BaseModel],
        field: str,
        cap: int,
        build: Any,
    ) -> None:
        too_long = "a" * (cap + 1) if field != "email" else "a" * (cap - 11) + "@example.com"
        with pytest.raises(ValidationError) as excinfo:
            schema(**build(**{field: too_long}))
        assert field in str(excinfo.value)

    @pytest.mark.parametrize(
        ("schema", "field", "cap", "build"),
        [entry for entry in LENGTH_CAPS if entry[1] != "email"],
        ids=[f"{s.__name__}.{f}" for s, f, _, _ in LENGTH_CAPS if f != "email"],
    )
    def test_exactly_the_cap_is_accepted(
        self,
        schema: type[BaseModel],
        field: str,
        cap: int,
        build: Any,
    ) -> None:
        """The boundary belongs to the client, not to us."""
        value = "US" + "0" * (cap - 2) if field == "isin" else "a" * cap
        instance = schema(**build(**{field: value}))
        assert len(getattr(instance, field)) == cap

    def test_an_isin_must_be_exactly_twelve_characters(self) -> None:
        for wrong in ("US6706G1040", "US67066G10401"):
            with pytest.raises(ValidationError):
                StockCreate(**_stock_create(isin=wrong))

    def test_a_username_below_the_floor_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UserCreate(**_user_create(username="a" * (USERNAME_MIN_LENGTH - 1)))
        assert UserCreate(**_user_create(username="a" * USERNAME_MIN_LENGTH))

    def test_a_password_shorter_than_the_floor_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UserCreate(**_user_create(password="a" * (PASSWORD_MIN_LENGTH - 1)))

    def test_a_password_longer_than_bcrypt_can_hash_is_rejected(self) -> None:
        """Past 72 bytes bcrypt ignores the rest, making two passwords interchangeable."""
        with pytest.raises(ValidationError):
            UserCreate(**_user_create(password="a" * 73))
        with pytest.raises(ValidationError):
            PasswordChange(current_password="hunter22", new_password="a" * 73)

    def test_a_blank_string_is_not_a_name(self) -> None:
        for payload in (_stock_create(company=""), _stock_create(market="")):
            with pytest.raises(ValidationError):
                StockCreate(**payload)


class TestEmailFormat:
    @pytest.mark.parametrize(
        "address",
        ["stephen@example.com", "first.last+tag@sub.example.co.uk", "s@ex.io"],
    )
    def test_a_real_address_is_accepted(self, address: str) -> None:
        assert UserCreate(**_user_create(email=address)).email == address

    @pytest.mark.parametrize(
        "address",
        ["stephen", "stephen@", "@example.com", "stephen example.com", "stephen@example", ""],
    )
    def test_a_malformed_address_is_rejected(self, address: str) -> None:
        with pytest.raises(ValidationError) as excinfo:
            UserCreate(**_user_create(email=address))
        assert "email" in str(excinfo.value)

    def test_an_optional_email_is_still_validated_on_update(self) -> None:
        assert UserUpdate().email is None
        with pytest.raises(ValidationError):
            UserUpdate(email="nope")


class TestNormalisation:
    """A security has one canonical spelling; the schema is where it acquires it."""

    def test_a_ticker_is_trimmed_and_upper_cased(self) -> None:
        assert StockCreate(**_stock_create(ticker_symbol="  nvda ")).ticker_symbol == "NVDA"

    def test_an_isin_is_upper_cased(self) -> None:
        assert StockCreate(**_stock_create(isin="us67066g1040")).isin == "US67066G1040"

    def test_normalisation_happens_before_the_length_check(self) -> None:
        """Whitespace must not consume a client's allowance of real characters."""
        padded = "  " + "A" * TICKER_MAX_LENGTH + "  "
        assert len(StockCreate(**_stock_create(ticker_symbol=padded)).ticker_symbol) == (
            TICKER_MAX_LENGTH
        )

    def test_a_non_string_ticker_still_reports_a_type_error(self) -> None:
        with pytest.raises(ValidationError):
            StockCreate(**_stock_create(ticker_symbol=17))

    def test_a_null_isin_passes_through_untouched(self) -> None:
        assert StockCreate(**_stock_create(isin=None)).isin is None


# ---------------------------------------------------------------------------------------
# from_attributes: a real model instance in, a contract out
# ---------------------------------------------------------------------------------------


class TestFromAttributes:
    def test_a_user_model_becomes_a_user_out(self) -> None:
        out = UserOut.model_validate(make_user())
        assert (out.username, out.email) == ("stephen", "stephen@example.com")
        assert out.user_id == uuid.UUID("7c1a4b3e-0000-4000-8000-000000000001")

    def test_a_stock_model_becomes_a_stock_out(self) -> None:
        out = StockOut.model_validate(make_stock())
        assert out.ticker_symbol == "NVDA"
        assert out.isin == "US67066G1040"

    def test_a_stock_without_an_isin_is_not_an_error(self) -> None:
        """``isin`` is the one nullable column on ``stocks``."""
        assert StockOut.model_validate(make_stock(isin=None)).isin is None

    def test_a_candle_model_becomes_a_stock_data_out(self) -> None:
        out = StockDataOut.model_validate(make_candle())
        assert (out.id, out.date, out.time) == (42, dt.date(2026, 1, 5), dt.time(9, 30))
        assert out.volume == 1_048_576

    def test_a_politician_model_becomes_a_politician_out(self) -> None:
        out = PoliticianOut.model_validate(make_politician())
        assert (out.politician_id, out.last_name) == ("N000147", "Pelosi")

    def test_a_politician_missing_the_nullable_columns_still_validates(self) -> None:
        out = PoliticianOut.model_validate(
            make_politician(state=None, chamber=None, dob=None, gender=None)
        )
        assert (out.state, out.chamber, out.dob, out.gender) == (None, None, None, None)

    def test_the_whole_watchlist_graph_serialises(self) -> None:
        """``Watchlist -> entries -> stock``: the chain ANV-9's repos must eager-load."""
        out = WatchlistDetailOut.model_validate(make_watchlist())

        assert out.title == "Semiconductors"
        assert [entry.position for entry in out.entries] == [0]
        assert out.entries[0].stock.ticker_symbol == "NVDA"
        assert out.entries[0].watchlist_id == out.watchlist_id

    def test_a_watchlist_entry_is_identified_by_its_pair(self) -> None:
        """There is no surrogate key, so both halves are part of the contract."""
        assert set(WatchlistEntryOut.model_fields) == {"watchlist_id", "stock_id", "position"}

    def test_the_summary_view_does_not_carry_the_entries(self) -> None:
        out = WatchlistOut.model_validate(make_watchlist())
        assert "entries" not in out.model_dump()

    def test_a_plain_dict_still_validates(self) -> None:
        """``from_attributes`` adds a source; it does not remove the JSON one."""
        payload = UserOut.model_validate(make_user()).model_dump()
        assert UserOut.model_validate(payload) == UserOut.model_validate(make_user())


# ---------------------------------------------------------------------------------------
# money
# ---------------------------------------------------------------------------------------


class TestDecimalPrecision:
    """``NUMERIC(12, 4)`` in, ``Decimal`` out. A float would already have lost the fourth
    decimal place before any of these assertions ran."""

    def test_a_price_stays_a_decimal(self) -> None:
        out = StockDataOut.model_validate(make_candle())
        assert isinstance(out.close_price, Decimal)
        assert out.close_price == Decimal("1234.5678")

    def test_the_exact_value_survives_a_json_round_trip(self) -> None:
        original = StockDataOut.model_validate(make_candle())
        restored = StockDataOut.model_validate_json(original.model_dump_json())
        assert restored.close_price == Decimal("1234.5678")
        assert str(restored.close_price) == "1234.5678"
        assert restored == original

    def test_the_fourth_decimal_place_reaches_the_wire(self) -> None:
        payload = StockDataOut.model_validate(make_candle()).model_dump_json()
        assert "1234.5678" in payload

    def test_a_float_is_not_quietly_kept_as_one(self) -> None:
        """A JSON number is still coerced to ``Decimal`` rather than left binary."""
        candle = StockDataCreate(
            date=dt.date(2026, 1, 5),
            time=dt.time(9, 30),
            open_price=1234.5678,
            high_price=1234.5678,
            low_price=1234.5678,
            close_price=1234.5678,
            volume=1,
        )
        assert isinstance(candle.close_price, Decimal)
        assert candle.close_price == Decimal("1234.5678")

    def test_more_precision_than_the_column_holds_is_rejected(self) -> None:
        """Better a 422 than a value Postgres silently rounds to something else."""
        with pytest.raises(ValidationError):
            StockDataCreate(
                date=dt.date(2026, 1, 5),
                time=dt.time(9, 30),
                open_price=Decimal("1.00001"),
                high_price=Decimal("1"),
                low_price=Decimal("1"),
                close_price=Decimal("1"),
                volume=1,
            )

    def test_a_negative_price_or_volume_is_rejected(self) -> None:
        for bad in ({"close_price": Decimal("-1")}, {"volume": -1}):
            with pytest.raises(ValidationError):
                StockDataCreate(
                    **{
                        "date": dt.date(2026, 1, 5),
                        "time": dt.time(9, 30),
                        "open_price": Decimal("1"),
                        "high_price": Decimal("1"),
                        "low_price": Decimal("1"),
                        "close_price": Decimal("1"),
                        "volume": 1,
                        **bad,
                    }
                )


# ---------------------------------------------------------------------------------------
# time
# ---------------------------------------------------------------------------------------


class TestDatetimes:
    def test_created_at_keeps_its_offset(self) -> None:
        out = UserOut.model_validate(make_user())
        assert out.created_at.tzinfo is not None
        assert out.created_at.utcoffset() == dt.timedelta(0)

    def test_created_at_serialises_with_an_offset(self) -> None:
        payload = UserOut.model_validate(make_user()).model_dump_json()
        assert "Z" in payload or "+00:00" in payload

    def test_created_at_survives_a_json_round_trip_as_the_same_instant(self) -> None:
        original = UserOut.model_validate(make_user())
        restored = UserOut.model_validate_json(original.model_dump_json())
        assert restored.created_at == original.created_at

    def test_a_non_utc_offset_is_preserved_rather_than_normalised(self) -> None:
        eastern = dt.timezone(dt.timedelta(hours=-5))
        moment = dt.datetime(2026, 1, 1, 9, 30, tzinfo=eastern)
        out = UserOut.model_validate(make_user(created_at=moment))
        assert out.created_at == moment
        assert out.created_at.utcoffset() == dt.timedelta(hours=-5)

    def test_a_naive_created_at_is_a_validation_error(self) -> None:
        """The column is ``TIMESTAMPTZ``; a naive value here would be an ambiguous instant."""
        with pytest.raises(ValidationError):
            UserOut.model_validate(make_user(created_at=dt.datetime(2026, 1, 1, 14, 30)))

    def test_a_candle_point_recombines_the_two_columns(self) -> None:
        """The whole of ANV-14's "date + time -> datetime" step, in the schema layer."""
        point = StockDataPoint.from_row(make_candle())
        assert point.datetime == dt.datetime(2026, 1, 5, 9, 30)
        assert point.close_price == Decimal("1234.5678")
        assert point.stock_id == make_candle().stock_id

    def test_a_candle_point_is_naive_on_purpose(self) -> None:
        """``stock_data.time`` is the exchange's local clock; 09:30 in New York is not UTC."""
        point = StockDataPoint.from_row(make_candle())
        assert point.datetime.tzinfo is None
        assert point.model_dump_json().count("2026-01-05T09:30:00") == 1

    def test_a_candle_point_refuses_an_offset(self) -> None:
        with pytest.raises(ValidationError):
            StockDataPoint(
                stock_id=uuid.uuid4(),
                datetime=dt.datetime(2026, 1, 5, 9, 30, tzinfo=dt.UTC),
                open_price=Decimal("1"),
                high_price=Decimal("1"),
                low_price=Decimal("1"),
                close_price=Decimal("1"),
                volume=1,
            )


# ---------------------------------------------------------------------------------------
# the list envelope
# ---------------------------------------------------------------------------------------


def _page_of_users(**overrides: Any) -> Page[UserOut]:
    values: dict[str, Any] = {
        "items": [UserOut.model_validate(make_user())],
        "total": 1,
        "limit": 50,
        "offset": 0,
    }
    values.update(overrides)
    return Page[UserOut](**values)


class TestPage:
    """One envelope, any item type — every list endpoint from ANV-13 onward returns it."""

    def test_it_carries_users(self) -> None:
        page = _page_of_users()
        assert isinstance(page.items[0], UserOut)
        assert page.items[0].username == "stephen"

    def test_it_carries_stocks(self) -> None:
        page = Page[StockOut](
            items=[StockOut.model_validate(make_stock())],
            total=1,
            limit=50,
            offset=0,
        )
        assert isinstance(page.items[0], StockOut)
        assert page.items[0].ticker_symbol == "NVDA"

    def test_it_carries_candles(self) -> None:
        page = Page[StockDataPoint](
            items=[StockDataPoint.from_row(make_candle())],
            total=1,
            limit=1,
            offset=0,
        )
        assert page.items[0].close_price == Decimal("1234.5678")

    def test_the_item_type_is_enforced(self) -> None:
        """A ``Page[StockOut]`` of users is a programming error, not a loose dict."""
        with pytest.raises(ValidationError):
            Page[StockOut](items=[{"username": "stephen"}], total=1, limit=50, offset=0)

    def test_items_are_coerced_into_the_item_schema(self) -> None:
        page = Page[StockOut](
            items=[StockOut.model_validate(make_stock()).model_dump()],
            total=1,
            limit=50,
            offset=0,
        )
        assert isinstance(page.items[0], StockOut)

    def test_two_pages_of_different_types_are_different_classes(self) -> None:
        assert Page[UserOut] is not Page[StockOut]

    def test_an_empty_page_is_valid(self) -> None:
        page = Page[UserOut](items=[], total=0, limit=50, offset=0)
        assert page.items == []
        assert page.has_more is False

    @pytest.mark.parametrize(
        ("count", "total", "offset", "expected"),
        [(1, 1, 0, False), (1, 3, 0, True), (1, 3, 2, False), (0, 5, 5, False), (2, 5, 1, True)],
    )
    def test_has_more_is_the_arithmetic_no_client_should_repeat(
        self, count: int, total: int, offset: int, expected: bool
    ) -> None:
        page = Page[UserOut](
            items=[UserOut.model_validate(make_user()) for _ in range(count)],
            total=total,
            limit=2,
            offset=offset,
        )
        assert page.has_more is expected

    def test_has_more_is_serialised_not_merely_computed(self) -> None:
        """A client reads it off the body; it is not a Python-only convenience."""
        assert _page_of_users(total=9).model_dump()["has_more"] is True
        assert '"has_more":true' in _page_of_users(total=9).model_dump_json()

    def test_the_envelope_keys_are_the_documented_set(self) -> None:
        assert set(_page_of_users().model_dump()) == {
            "items",
            "total",
            "limit",
            "offset",
            "has_more",
        }

    @pytest.mark.parametrize(
        "invalid",
        [{"total": -1}, {"limit": 0}, {"limit": MAX_PAGE_LIMIT + 1}, {"offset": -1}],
    )
    def test_an_impossible_window_is_rejected(self, invalid: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            _page_of_users(**invalid)

    def test_the_ceiling_itself_is_allowed(self) -> None:
        assert _page_of_users(limit=MAX_PAGE_LIMIT).limit == MAX_PAGE_LIMIT
