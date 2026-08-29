"""``UserService`` against real Postgres — the parts only a database can answer.

``tests/unit/test_services_user.py`` covers the branches; this module covers the one claim
a fake cannot support. :meth:`~app.services.user.UserService.register` translates an
``IntegrityError`` by **matching the constraint name** ``uq_users_email`` /
``uq_users_username``, and whether those names actually survive the trip out of Postgres,
through asyncpg, through SQLAlchemy's DBAPI adapter and into the exception the service
catches is not something a hand-built ``IntegrityError`` can prove. If they do not, the
race path silently degrades from a 409 to a 500 and no unit test notices.

The race itself is reproduced by blinding the pre-check — a repo whose ``*_exists`` always
answer "free" is exactly the state two simultaneous sign-ups are in when they both look
before either inserts.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import ConflictError, NotFoundError
from app.repos.user import UserRepo
from app.schemas.user import UserCreate
from app.services.user import EMAIL_TAKEN_MESSAGE, USERNAME_TAKEN_MESSAGE, UserService
from app.settings import Settings
from app.utils.security import verify_password
from tests.factories import UserFactory

PASSWORD = "correct-horse-battery"


class BlindUserRepo(UserRepo):
    """The real repo with its uniqueness pre-checks disabled.

    Not a fake: every query it runs is the real one against real Postgres. Only the two
    "is this taken" lookups are forced to answer "free", which puts the insert in the same
    position as the loser of a genuine race.
    """

    async def email_exists(self, *args: Any, **kwargs: Any) -> bool:
        return False

    async def username_exists(self, *args: Any, **kwargs: Any) -> bool:
        return False


def build_service(session: AsyncSession, *, users: UserRepo | None = None) -> UserService:
    settings = Settings(jwt_secret_key="integration-test-jwt-secret")
    return UserService(session, settings, users=users or UserRepo())


def registration(**overrides: Any) -> UserCreate:
    body: dict[str, Any] = {
        "username": "newperson",
        "email": "new.person@example.com",
        "password": PASSWORD,
    }
    body.update(overrides)
    return UserCreate(**body)


class TestRegister:
    async def test_the_row_is_written_and_readable_afterwards(
        self, db_session: AsyncSession
    ) -> None:
        service = build_service(db_session)

        created = await service.register(registration())

        stored = await UserRepo().get_by_id(db_session, created.user_id)
        assert stored is not None
        assert stored.email == "new.person@example.com"
        assert stored.created_at.tzinfo is not None

    async def test_only_the_digest_reaches_the_column(self, db_session: AsyncSession) -> None:
        service = build_service(db_session)

        created = await service.register(registration())

        stored = await UserRepo().get_by_id(db_session, created.user_id)
        assert stored is not None
        assert stored.password != PASSWORD
        assert verify_password(PASSWORD, stored.password)

    @pytest.mark.parametrize(
        ("clashing_field", "message", "field"),
        [
            ("email", EMAIL_TAKEN_MESSAGE, "email"),
            ("username", USERNAME_TAKEN_MESSAGE, "username"),
        ],
    )
    async def test_the_real_unique_index_still_produces_a_conflict(
        self,
        db_session: AsyncSession,
        clashing_field: str,
        message: str,
        field: str,
    ) -> None:
        """**The point of this module.** Postgres rejects it; the caller still gets a 409.

        Proves the constraint names in ``_UNIQUE_CONSTRAINTS`` are the ones the driver
        really reports — a typo there would turn this into an unhandled ``IntegrityError``.
        """
        taken = await UserFactory().create(db_session)
        service = build_service(db_session, users=BlindUserRepo())

        with pytest.raises(ConflictError) as caught:
            await service.register(registration(**{clashing_field: getattr(taken, clashing_field)}))

        assert caught.value.message == message
        assert caught.value.details == {"resource": "user", "field": field}

    async def test_the_session_is_usable_again_after_the_conflict(
        self, db_session: AsyncSession
    ) -> None:
        """The rollback is not cosmetic. Postgres puts a transaction that hit a constraint
        violation into an aborted state and refuses every later statement in it, so without
        the ``rollback()`` in ``register`` the 409 would be followed by an
        ``InFailedSQLTransaction`` on whatever ran next."""
        taken = await UserFactory().create(db_session)
        service = build_service(db_session, users=BlindUserRepo())

        with pytest.raises(ConflictError):
            await service.register(registration(email=taken.email))

        recovered = await service.register(registration())
        assert recovered.username == "newperson"


class TestGetUser:
    async def test_your_own_row_comes_back(self, db_session: AsyncSession) -> None:
        user = await UserFactory().create(db_session)
        service = build_service(db_session)

        found = await service.get_user(user_id=user.user_id, requester=user)

        assert found.user_id == user.user_id
        assert found.email == user.email

    async def test_another_real_row_is_still_a_404(self, db_session: AsyncSession) -> None:
        me = await UserFactory().create(db_session)
        them = await UserFactory().create(db_session)
        service = build_service(db_session)

        with pytest.raises(NotFoundError):
            await service.get_user(user_id=them.user_id, requester=me)

    async def test_a_nonexistent_id_is_a_404_too(self, db_session: AsyncSession) -> None:
        me = await UserFactory().create(db_session)
        service = build_service(db_session)

        with pytest.raises(NotFoundError):
            await service.get_user(user_id=uuid.uuid4(), requester=me)
