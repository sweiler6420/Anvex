"""Unit tests for ``app/services/user.py`` — the service's own logic, no database.

The fast tier (``CLAUDE.md`` §6): :class:`tests.helpers.FakeUserRepo` stands in for the
repo and :class:`tests.helpers.StubSession` counts the transaction boundary, so every
branch below runs with Docker stopped. What is *not* asserted here is SQL — that
``uq_users_email`` really rejects a second row is a database guarantee, proved in
``tests/integration/test_repos_user.py``.

Four properties are being pinned, and they are different properties:

1. **A duplicate is a conflict that names its field.** Email and username produce distinct
   messages and a ``details["field"]`` a form can act on, and the submitted value is never
   echoed back.
2. **The pre-check is not what makes registration correct.** Two sign-ups can pass it
   simultaneously; the unique index catches the loser, and the service turns that
   ``IntegrityError`` into the *identical* conflict rather than a 500. An
   ``IntegrityError`` about anything else is left alone, because that one *is* a bug.
3. **``PasswordTooLongError`` becomes ``ValidationError`` here or nowhere.**
   ``app/utils/`` cannot import ``app/domain/`` (``CLAUDE.md`` §3), so the service is the
   only translation point — and the path is reachable, because ANV-8's cap counts
   characters while bcrypt counts bytes.
4. **``get_user`` is not a directory.** Somebody else's id and a nonexistent id produce
   byte-identical refusals, so the response never confirms another account exists.

Bcrypt is genuinely slow (~250 ms a call at cost factor 12), so registrations below are
kept to the ones that are actually about hashing.
"""

from __future__ import annotations

import ast
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from app.domain.errors import AnvexError, ConflictError, NotFoundError, ValidationError
from app.schemas.user import UserCreate, UserOut
from app.services import user as user_service_module
from app.services.user import (
    EMAIL_TAKEN_MESSAGE,
    PASSWORD_TOO_LONG_MESSAGE,
    USERNAME_TAKEN_MESSAGE,
    UserService,
)
from app.settings import Settings
from app.utils.security import password_byte_length, verify_password
from tests.helpers import FakeUserRepo, StubSession, make_user

PASSWORD = "correct-horse-battery"
USERNAME = "stephen1"
EMAIL = "stephen@example.com"

#: 25 characters — inside ANV-8's 72-**character** cap — and 75 bytes, which is outside
#: bcrypt's 72-**byte** one. The gap the translation in ``UserService._hash`` exists for.
MULTIBYTE_PASSWORD = "漢" * 25


def build_settings(**overrides: Any) -> Settings:
    """Settings that ignore the developer's ``.env``. The user service reads none of them."""
    values: dict[str, Any] = {"jwt_secret_key": "unit-test-jwt-secret"}
    values.update(overrides)
    return Settings(**values)


def build_service(*users: Any) -> tuple[UserService, FakeUserRepo, StubSession]:
    """A :class:`UserService` over an in-memory repo and a counting session stub."""
    repo = FakeUserRepo(*users)
    session = StubSession()
    service = UserService(session=session, settings=build_settings(), users=repo)  # type: ignore[arg-type]
    return service, repo, session


def registration(**overrides: Any) -> UserCreate:
    values: dict[str, Any] = {"username": USERNAME, "email": EMAIL, "password": PASSWORD}
    values.update(overrides)
    return UserCreate(**values)


def existing_user(**overrides: Any) -> Any:
    return make_user(username=USERNAME, email=EMAIL, password_hash="", **overrides)


def unique_violation(constraint: str) -> IntegrityError:
    """What Postgres hands back when a unique index rejects the insert.

    The constraint name arrives in the message text, which is what
    ``UserService._constraint_hint`` falls back to when the adapted DBAPI error carries no
    ``constraint_name`` attribute.
    """
    return IntegrityError(
        "INSERT INTO anvex.users ...",
        {},
        Exception(f'duplicate key value violates unique constraint "{constraint}"'),
    )


# ---------------------------------------------------------------------------------------
# register
# ---------------------------------------------------------------------------------------


class TestRegister:
    async def test_a_new_account_comes_back_as_the_public_schema(self) -> None:
        service, _, _ = build_service()

        created = await service.register(registration())

        assert isinstance(created, UserOut)
        assert created.username == USERNAME
        assert created.email == EMAIL
        assert isinstance(created.user_id, uuid.UUID)

    async def test_the_password_is_hashed_not_stored(self) -> None:
        service, repo, _ = build_service()

        await service.register(registration())

        stored = repo.users[-1].password
        assert stored != PASSWORD
        assert stored.startswith("$2b$")
        assert verify_password(PASSWORD, stored)

    async def test_the_result_has_no_password_field_at_all(self) -> None:
        """Not "it is empty" — the field does not exist on the way out."""
        service, _, _ = build_service()

        created = await service.register(registration())

        assert "password" not in created.model_dump()
        assert PASSWORD not in created.model_dump_json()

    async def test_the_service_owns_the_transaction(self) -> None:
        """Repos only flush (``CLAUDE.md`` §3), so the commit has to happen here."""
        service, _, session = build_service()

        await service.register(registration())

        assert (session.commits, session.rollbacks) == (1, 0)

    async def test_a_duplicate_email_is_a_conflict(self) -> None:
        service, _, session = build_service(existing_user())

        with pytest.raises(ConflictError) as caught:
            await service.register(registration(username="somebodyelse"))

        assert caught.value.code == "conflict"
        assert caught.value.message == EMAIL_TAKEN_MESSAGE
        assert caught.value.details == {"resource": "user", "field": "email"}
        assert session.commits == 0

    async def test_a_duplicate_username_is_a_conflict(self) -> None:
        service, _, session = build_service(existing_user())

        with pytest.raises(ConflictError) as caught:
            await service.register(registration(email="someone.else@example.com"))

        assert caught.value.message == USERNAME_TAKEN_MESSAGE
        assert caught.value.details == {"resource": "user", "field": "username"}
        assert session.commits == 0

    async def test_the_two_duplicates_are_told_apart(self) -> None:
        """A sign-up form has to know which box to put the red border round."""
        assert EMAIL_TAKEN_MESSAGE != USERNAME_TAKEN_MESSAGE

    async def test_a_conflict_never_echoes_the_submitted_value(self) -> None:
        """The value is the caller's own, but it should not be reflected into logs either."""
        service, _, _ = build_service(existing_user())

        with pytest.raises(ConflictError) as caught:
            await service.register(registration())

        assert EMAIL not in caught.value.message
        assert EMAIL not in str(caught.value.details)

    async def test_a_duplicate_never_reaches_the_insert(self) -> None:
        service, repo, _ = build_service(existing_user())

        with pytest.raises(ConflictError):
            await service.register(registration())

        assert "create" not in [name for name, _ in repo.calls]

    async def test_a_free_identity_is_checked_on_both_columns(self) -> None:
        service, repo, _ = build_service()

        await service.register(registration())

        assert [name for name, _ in repo.calls] == ["email_exists", "username_exists", "create"]


# ---------------------------------------------------------------------------------------
# register — the race the pre-check cannot close
# ---------------------------------------------------------------------------------------


class TestRegisterRace:
    """Two sign-ups pass the pre-check together; the unique index catches the loser.

    Without the translation below that loser is an unhandled ``IntegrityError``, i.e. a
    500 for a request that is merely late.
    """

    @pytest.mark.parametrize(
        ("constraint", "message", "field"),
        [
            ("uq_users_email", EMAIL_TAKEN_MESSAGE, "email"),
            ("uq_users_username", USERNAME_TAKEN_MESSAGE, "username"),
        ],
    )
    async def test_a_unique_violation_becomes_the_same_conflict(
        self, constraint: str, message: str, field: str
    ) -> None:
        service, repo, _ = build_service()
        repo.create_error = unique_violation(constraint)

        with pytest.raises(ConflictError) as caught:
            await service.register(registration())

        assert caught.value.message == message
        assert caught.value.details == {"resource": "user", "field": field}

    async def test_the_race_answer_is_identical_to_the_pre_checks(self) -> None:
        """A client cannot tell "you were second" from "it was already taken"."""
        losing, repo, _ = build_service()
        repo.create_error = unique_violation("uq_users_email")
        checked, _, _ = build_service(existing_user())

        with pytest.raises(ConflictError) as raced:
            await losing.register(registration(username="somebodyelse"))
        with pytest.raises(ConflictError) as pre_checked:
            await checked.register(registration(username="somebodyelse"))

        assert raced.value.code == pre_checked.value.code
        assert raced.value.message == pre_checked.value.message
        assert raced.value.details == pre_checked.value.details

    async def test_the_constraint_name_is_also_read_off_the_driver_error(self) -> None:
        """asyncpg exposes ``constraint_name``; SQLAlchemy's adapter may keep only the text.

        Both are checked by the service, because which one survives the trip through the
        DBAPI adapter is nobody's public API.
        """

        class AsyncpgLikeError(Exception):
            constraint_name = "uq_users_username"

        service, repo, _ = build_service()
        repo.create_error = IntegrityError("INSERT ...", {}, AsyncpgLikeError("uniqueness"))

        with pytest.raises(ConflictError) as caught:
            await service.register(registration())

        assert caught.value.details["field"] == "username"

    async def test_the_failed_transaction_is_rolled_back_and_not_committed(self) -> None:
        service, repo, session = build_service()
        repo.create_error = unique_violation("uq_users_email")

        with pytest.raises(ConflictError):
            await service.register(registration())

        assert (session.commits, session.rollbacks) == (0, 1)

    async def test_an_unrelated_integrity_error_is_left_alone(self) -> None:
        """Only the two identity constraints mean "taken". Anything else is a bug, i.e. 500."""
        service, repo, _ = build_service()
        repo.create_error = unique_violation("ck_users_something_impossible")

        with pytest.raises(IntegrityError):
            await service.register(registration())


# ---------------------------------------------------------------------------------------
# register — the bcrypt byte boundary
# ---------------------------------------------------------------------------------------


class TestPasswordTooLong:
    def test_the_multibyte_password_really_does_pass_the_schema(self) -> None:
        """If it did not, the translation below would be dead code and this suite a lie."""
        assert len(MULTIBYTE_PASSWORD) == 25
        assert password_byte_length(MULTIBYTE_PASSWORD) == 75

        accepted = registration(password=MULTIBYTE_PASSWORD)

        assert accepted.password == MULTIBYTE_PASSWORD

    async def test_it_becomes_a_validation_error_not_a_value_error(self) -> None:
        """``app/utils/`` cannot import ``app/domain/``; the service is the only seam."""
        service, _, _ = build_service()

        with pytest.raises(ValidationError) as caught:
            await service.register(registration(password=MULTIBYTE_PASSWORD))

        assert caught.value.code == "validation_error"
        assert caught.value.message == PASSWORD_TOO_LONG_MESSAGE
        assert caught.value.details == {"field": "password"}

    async def test_the_original_refusal_is_kept_as_the_cause(self) -> None:
        """So the log still says how many bytes it actually was."""
        service, _, _ = build_service()

        with pytest.raises(ValidationError) as caught:
            await service.register(registration(password=MULTIBYTE_PASSWORD))

        assert isinstance(caught.value.__cause__, user_service_module.PasswordTooLongError)
        assert "75 bytes" in str(caught.value.__cause__)

    async def test_nothing_is_written_and_nothing_is_committed(self) -> None:
        service, repo, session = build_service()

        with pytest.raises(ValidationError):
            await service.register(registration(password=MULTIBYTE_PASSWORD))

        assert "create" not in [name for name, _ in repo.calls]
        assert session.commits == 0

    async def test_a_72_byte_password_is_still_accepted(self) -> None:
        """The boundary is 72 bytes, not "anything unusual"."""
        service, _, _ = build_service()
        password = "a" * 72

        created = await service.register(registration(password=password))

        assert created.username == USERNAME


# ---------------------------------------------------------------------------------------
# get_user
# ---------------------------------------------------------------------------------------


class TestGetUser:
    async def test_your_own_id_resolves(self) -> None:
        user = existing_user()
        service, _, _ = build_service(user)

        found = await service.get_user(user_id=user.user_id, requester=user)

        assert isinstance(found, UserOut)
        assert found.user_id == user.user_id

    async def test_somebody_elses_id_is_a_404_not_a_403(self) -> None:
        """The old API served any user row to any authenticated caller.

        403 would confirm the account exists, which is the half worth protecting; the
        product has no directory, so there is nothing to serve here anyway.
        """
        me = existing_user()
        them = make_user(username="otherperson", email="other@example.com")
        service, _, _ = build_service(me, them)

        with pytest.raises(NotFoundError) as caught:
            await service.get_user(user_id=them.user_id, requester=me)

        assert caught.value.code == "not_found"

    async def test_a_refused_lookup_never_touches_the_other_row(self) -> None:
        """No query at all, so response time cannot answer "does that account exist"."""
        me = existing_user()
        them = make_user(username="otherperson", email="other@example.com")
        service, repo, _ = build_service(me, them)

        with pytest.raises(NotFoundError):
            await service.get_user(user_id=them.user_id, requester=me)

        assert repo.calls == []

    async def test_a_stranger_and_a_ghost_are_indistinguishable(self) -> None:
        """The property the 404 exists for."""
        me = existing_user()
        them = make_user(username="otherperson", email="other@example.com")
        service, _, _ = build_service(me, them)

        nobody = uuid.uuid4()

        with pytest.raises(NotFoundError) as stranger:
            await service.get_user(user_id=them.user_id, requester=me)
        with pytest.raises(NotFoundError) as ghost:
            await service.get_user(user_id=nobody, requester=me)

        # Everything but the id the caller themselves supplied has to match.
        assert stranger.value.code == ghost.value.code
        assert stranger.value.details["resource"] == ghost.value.details["resource"]
        assert set(stranger.value.details) == set(ghost.value.details)
        blanked = stranger.value.message.replace(str(them.user_id), "?")
        assert blanked == ghost.value.message.replace(str(nobody), "?")

    async def test_your_own_id_with_the_row_gone_is_a_404(self) -> None:
        """The account was deleted between authenticating and this read."""
        user = existing_user()
        service, repo, _ = build_service(user)
        repo.remove(user)

        with pytest.raises(NotFoundError):
            await service.get_user(user_id=user.user_id, requester=user)

    async def test_the_result_carries_no_password(self) -> None:
        user = make_user(username=USERNAME, email=EMAIL, password_hash="$2b$12$notasecretreally")
        service, _, _ = build_service(user)

        found = await service.get_user(user_id=user.user_id, requester=user)

        assert "password" not in found.model_dump()


# ---------------------------------------------------------------------------------------
# current_user
# ---------------------------------------------------------------------------------------


class TestCurrentUser:
    async def test_the_signed_in_account_is_projected_onto_the_public_schema(self) -> None:
        user = make_user(username=USERNAME, email=EMAIL, password_hash="$2b$12$notasecretreally")
        service, _, _ = build_service(user)

        me = await service.current_user(user=user)

        assert isinstance(me, UserOut)
        assert (me.user_id, me.username, me.email) == (user.user_id, USERNAME, EMAIL)

    async def test_the_digest_does_not_come_back(self) -> None:
        user = make_user(username=USERNAME, email=EMAIL, password_hash="$2b$12$notasecretreally")
        service, _, _ = build_service(user)

        me = await service.current_user(user=user)

        assert "password" not in me.model_dump()
        assert "$2b$" not in me.model_dump_json()

    async def test_it_does_not_query_again(self) -> None:
        """``get_current_user`` already re-read the row on this very request."""
        user = existing_user()
        service, repo, _ = build_service(user)

        await service.current_user(user=user)

        assert repo.calls == []


# ---------------------------------------------------------------------------------------
# layering
# ---------------------------------------------------------------------------------------


def service_tree() -> ast.Module:
    return ast.parse(Path(user_service_module.__file__).read_text(encoding="utf-8"))


class TestLayering:
    """`CLAUDE.md` §3, checked rather than trusted — prose conventions get broken."""

    def test_the_service_imports_no_web_framework(self) -> None:
        modules: set[str] = set()
        for node in ast.walk(service_tree()):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                modules.add(node.module)
        roots = {module.split(".")[0] for module in modules}

        assert "fastapi" not in roots
        assert "starlette" not in roots
        assert "app.api" not in modules
        assert "app.deps" not in modules

    def test_the_service_raises_no_http_exception(self) -> None:
        used = {
            node.id if isinstance(node, ast.Name) else node.attr
            for node in ast.walk(service_tree())
            if isinstance(node, ast.Name | ast.Attribute)
        }
        assert "HTTPException" not in used

    def test_every_error_it_raises_is_a_domain_error(self) -> None:
        raised = {
            node.exc.func.id
            for node in ast.walk(service_tree())
            if isinstance(node, ast.Raise)
            and isinstance(node.exc, ast.Call)
            and isinstance(node.exc.func, ast.Name)
        }
        for name in raised:
            assert issubclass(getattr(user_service_module, name), AnvexError), name

    def test_no_sqlalchemy_query_is_written_here(self) -> None:
        """`CLAUDE.md` §3: if you typed `select(` outside `app/repos/`, it is the wrong file."""
        source = Path(user_service_module.__file__).read_text(encoding="utf-8")
        assert "select(" not in source
