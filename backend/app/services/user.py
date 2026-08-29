"""Account use cases: register, read the signed-in account, read an account by id.

Written to the shape :class:`~app.services.auth.AuthService` established (``CLAUDE.md``
§3) — collaborators in the constructor, one ``async`` method per use case, a schema out,
``app.domain.errors`` on the way out, and the ``commit()`` here because repos only flush.

Three decisions in this module are worth reading before changing it.

**Registration answers "that is already taken", and that is a deliberate exception to the
no-oracle rule.** ``CLAUDE.md`` §4 says an endpoint keyed on an unauthenticated identifier
must answer identically whether or not that identifier exists, which is why login and
recovery are uniform. ``POST /v1/users`` cannot be: both columns are unique, so a sign-up
form that will not say *which* field clashed either fails with an unactionable error or
silently creates nothing. The only design that closes the leak is "always accept, then
mail the address" — and Anvex has no mail client (see ``AuthService.recovery``). So the
conflict is reported, with the clashing **field** in ``details`` and the submitted value
*not* echoed back into the response or the logs.

**The pre-check is for the message; the constraint is for the correctness.**
:meth:`~app.repos.user.UserRepo.email_exists` and ``username_exists`` are what turn a
duplicate into a clean 409 naming the field. They cannot make registration correct on
their own: two requests can both pass the check before either inserts, and the loser then
trips ``uq_users_email``/``uq_users_username`` at the flush. That race is closed by the
unique indexes, and :meth:`UserService.register` translates the resulting
``IntegrityError`` into the *same* :class:`~app.domain.errors.ConflictError` the pre-check
would have raised — so the two callers get one 201 and one 409, never a 500. An
``IntegrityError`` naming any other constraint is re-raised untouched: that is a bug, and a
bug should be a 500.

**``GET /v1/users/{user_id}`` serves you your own account and nothing else.** The old API
let any authenticated caller fetch any user row by id, which — because registration is
self-service — meant anybody could obtain a token and then read any account's email
address. Anvex has no directory, no social graph and no admin role, so nothing needs that
capability; the route exists because a client holding a ``user_id`` (from a token, from a
watchlist's owner field) wants to resolve it. A request for somebody else's id is refused
with :class:`~app.domain.errors.NotFoundError`, **identical** to the answer for an id that
does not exist at all: 403 would confirm that the account is real, which is the half of
the information worth protecting. When the product grows a genuine reason to see another
user — a shared watchlist, say — it grows a *public profile* projection to go with it,
rather than widening this one.
"""

from __future__ import annotations

import uuid
from typing import Final

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import ConflictError, NotFoundError, ValidationError
from app.models import User
from app.repos.user import UserRepo, user_repo
from app.schemas.user import UserCreate, UserOut
from app.settings import Settings
from app.utils.security import PasswordTooLongError, hash_password

logger = structlog.get_logger("anvex.users")

#: The resource name every error in this module reports, so ``details["resource"]`` is
#: stable across the pre-check and the race path.
RESOURCE: Final[str] = "user"

EMAIL_TAKEN_MESSAGE: Final[str] = "That email address is already registered."
USERNAME_TAKEN_MESSAGE: Final[str] = "That username is already taken."

#: ANV-8 caps the password at 72 *characters* and bcrypt counts 72 *bytes*, so a
#: 25-character password of three-byte characters passes validation and still overflows
#: (``app/utils/security.py``). The message says "bytes" because saying "characters" would
#: be a lie the user could not act on.
PASSWORD_TOO_LONG_MESSAGE: Final[str] = (
    "The password is too long: bcrypt hashes at most 72 bytes, and some characters cost "
    "more than one byte each."
)

#: The unique indexes standing behind the pre-checks, mapped to the conflict each one
#: means. Names come from ``Base.metadata``'s naming convention (``CLAUDE.md`` §4), so
#: they are reproducible rather than whatever Postgres would otherwise have invented.
_UNIQUE_CONSTRAINTS: Final[dict[str, tuple[str, str]]] = {
    "uq_users_email": (EMAIL_TAKEN_MESSAGE, "email"),
    "uq_users_username": (USERNAME_TAKEN_MESSAGE, "username"),
}


class UserService:
    """Registering an account and reading one back."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        users: UserRepo = user_repo,
    ) -> None:
        self.session = session
        self.settings = settings
        #: Keyword-defaulted to the module-level singleton, which is the seam a unit test
        #: replaces with :class:`tests.helpers.FakeUserRepo` to run without Postgres.
        self.users = users

    # -----------------------------------------------------------------------------------
    # Use cases
    # -----------------------------------------------------------------------------------

    async def register(self, data: UserCreate) -> UserOut:
        """Create an account and return its public shape.

        The plaintext password is hashed here and the digest alone reaches the repo; it is
        never logged, never stored as given, and :class:`~app.schemas.user.UserOut` has no
        field it could be returned in.

        :raises ConflictError: the email address or the username is already registered —
            ``details["field"]`` says which.
        :raises ValidationError: the password is longer than bcrypt can hash.
        """
        await self._refuse_taken_identity(email=data.email, username=data.username)

        try:
            user = await self.users.create(
                self.session,
                username=data.username,
                email=data.email,
                password=self._hash(data.password),
            )
            # The service owns the transaction boundary: repos only ever flush.
            await self.session.commit()
        except IntegrityError as exc:
            # Somebody else registered the same identity between the pre-check above and
            # this flush. The unique index is what makes that safe; this makes it civil.
            await self.session.rollback()
            conflict = self._as_conflict(exc)
            if conflict is None:
                raise
            logger.warning("users.register_lost_the_race", field=conflict.details.get("field"))
            raise conflict from exc

        logger.info("users.registered", user_id=str(user.user_id))
        return UserOut.model_validate(user)

    async def get_user(self, *, user_id: uuid.UUID, requester: User) -> UserOut:
        """The account ``user_id`` names, provided it is ``requester``'s own.

        See the module docstring for why this is not a directory lookup. Both refusals —
        "not you" and "no such row" — raise the same error with the same ``details``, so
        the response never confirms that another account exists.

        :raises NotFoundError: the id belongs to somebody else, or to nobody.
        """
        if user_id != requester.user_id:
            logger.info(
                "users.cross_account_read_refused",
                user_id=str(requester.user_id),
                requested_id=str(user_id),
            )
            raise NotFoundError(RESOURCE, user_id)

        user = await self.users.get_by_id(self.session, user_id)
        if user is None:
            raise NotFoundError(RESOURCE, user_id)
        return UserOut.model_validate(user)

    async def current_user(self, *, user: User) -> UserOut:
        """The signed-in account, projected onto the public schema.

        ``user`` has already been re-read from the database by
        :func:`~app.deps.auth.get_current_user` on this very request, so this deliberately
        does not query again. It stays a service method rather than a projection inlined
        into the handler because a handler returns *one service call's* result
        (``CLAUDE.md`` §3), and because everything ``/me`` grows later — a watchlist count,
        a preferences blob — belongs on this side of the line.
        """
        return UserOut.model_validate(user)

    # -----------------------------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------------------------

    async def _refuse_taken_identity(self, *, email: str, username: str) -> None:
        """Turn a duplicate into a 409 that names the field the user has to change."""
        if await self.users.email_exists(self.session, email):
            raise self._conflict(EMAIL_TAKEN_MESSAGE, field="email")
        if await self.users.username_exists(self.session, username):
            raise self._conflict(USERNAME_TAKEN_MESSAGE, field="username")

    def _hash(self, password: str) -> str:
        """Hash ``password``, translating the utils-layer refusal into a domain error.

        ``app/utils/`` has no Anvex meaning and therefore cannot import ``app/domain/``
        (``CLAUDE.md`` §3), so :class:`~app.utils.security.PasswordTooLongError` is a plain
        ``ValueError`` and **this is the only place it can become a**
        :class:`~app.domain.errors.ValidationError` — i.e. a 422 rather than a 500.

        The path is genuinely reachable: ANV-8's schema cap counts characters and bcrypt
        counts bytes, so a multibyte password can pass validation and still overflow.
        """
        try:
            return hash_password(password)
        except PasswordTooLongError as exc:
            raise ValidationError(PASSWORD_TOO_LONG_MESSAGE, field="password") from exc

    @staticmethod
    def _conflict(message: str, *, field: str) -> ConflictError:
        """A duplicate-identity conflict. The submitted value is never put in the body."""
        return ConflictError(RESOURCE, message=message, details={"field": field})

    def _as_conflict(self, exc: IntegrityError) -> ConflictError | None:
        """The :class:`ConflictError` ``exc`` means, or ``None`` if it means a bug.

        Matches on the constraint name, which asyncpg supplies as an attribute and which
        also appears in the message text — both are checked, because the error travels
        through SQLAlchemy's DBAPI adapter and which of the two survives the trip is not
        part of anybody's public API.
        """
        hint = self._constraint_hint(exc)
        for constraint, (message, field) in _UNIQUE_CONSTRAINTS.items():
            if constraint in hint:
                return self._conflict(message, field=field)
        return None

    @staticmethod
    def _constraint_hint(exc: IntegrityError) -> str:
        """Whatever ``exc`` can tell us about which constraint it violated."""
        original = exc.orig
        for candidate in (original, getattr(original, "__cause__", None)):
            name = getattr(candidate, "constraint_name", None)
            if isinstance(name, str) and name:
                return name
        return str(original) if original is not None else str(exc)


__all__ = [
    "EMAIL_TAKEN_MESSAGE",
    "PASSWORD_TOO_LONG_MESSAGE",
    "RESOURCE",
    "USERNAME_TAKEN_MESSAGE",
    "UserService",
]
