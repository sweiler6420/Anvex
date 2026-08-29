"""Authentication use cases: sign in, rotate a token pair, ask for account recovery.

This is the first service in the codebase, so it is also the worked example every later
one copies (``CLAUDE.md`` §3). Its shape:

* **Constructed with its collaborators, never with a request.** A session, a
  :class:`~app.settings.Settings`, and its repos — all injected. Nothing here imports
  ``fastapi``, reads ``os.environ`` or knows what a status code is, which is what lets the
  same object be driven from an API handler today and a Celery task later.
* **It raises domain errors, never ``HTTPException``.** ``app/middleware/errors.py`` is the
  only place that maps an error to a status.
* **It reads the clock exactly once per operation.** ``datetime.now(UTC)`` is called at the
  top of each public method and that single value is passed down; nothing below this layer
  is allowed to read a clock at all (``CLAUDE.md`` §4). One reading per operation is also
  what makes a minted pair share an ``iat``.
* **It unwraps the secrets.** ``settings.jwt_secret_key`` is a ``SecretStr``; the domain
  takes a plain ``str``, so ``.get_secret_value()`` happens here and only here.

**The security fix this ticket exists for lives in** :meth:`AuthService.refresh`. The old
API's ``/v1/refresh`` ran the same ``verify_access_token`` over whatever it was handed and
minted a fresh long-lived pair from it, so a leaked *access* token could be renewed
forever. This one calls :func:`~app.domain.auth.decode_refresh_token`, which rejects an
access token with ``wrong_token_type``, and then **re-reads the account** — a token stays
cryptographically valid until it expires, so without the re-read a deleted user could keep
refreshing for a week.

**Login does not say which half failed.** An unknown identifier and a wrong password raise
the *same* bare :class:`~app.domain.errors.UnauthorizedError`: same status, same code, same
message. Deliberately not a :class:`~app.domain.auth.TokenError` either — ``token_expired``
and friends tell a client to go and refresh, which is nonsense for a failed password.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Final

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.auth import (
    create_token_pair,
    decode_access_token,
    decode_refresh_token,
)
from app.domain.errors import UnauthorizedError
from app.models import User
from app.repos.user import UserRepo, user_repo
from app.schemas.auth import RecoveryAccepted, TokenPair
from app.settings import Settings
from app.utils.security import verify_password

logger = structlog.get_logger("anvex.auth")

#: The one sentence every failed credential check produces. Spelled once so the "unknown
#: identifier" and "wrong password" paths cannot drift into two distinguishable answers.
INVALID_CREDENTIALS_MESSAGE: Final[str] = "The username or password is incorrect."

#: What a bearer of a structurally valid token hears when the account behind it is gone.
UNKNOWN_ACCOUNT_MESSAGE: Final[str] = "The account this token belongs to no longer exists."

#: A real bcrypt digest of a random string nobody knows. :meth:`AuthService.login` verifies
#: against it when the identifier matches no row, so the miss costs the same ~100 ms of
#: hashing as a wrong password does. Without it the two arms are trivially distinguishable
#: by response time, which turns the login endpoint into an account-existence oracle — and
#: no amount of care over the *message* fixes that.
_ABSENT_USER_PASSWORD_HASH: Final[str] = (
    "$2b$12$ZfmDW8dl.Mwoq2lRkIsDjuSmq0yoaSQUpBdC6uw8TU2OF0nBhS9Xa"
)


class AuthService:
    """Sign-in, token rotation and recovery for one request (or one task)."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        users: UserRepo = user_repo,
    ) -> None:
        self.session = session
        self.settings = settings
        #: Injectable so a unit test can drive the service with a fake repo and no
        #: database. Repos are stateless singletons, so the default costs nothing.
        self.users = users

    # -----------------------------------------------------------------------------------
    # Use cases
    # -----------------------------------------------------------------------------------

    async def login(self, *, identifier: str, password: str) -> TokenPair:
        """Exchange credentials for a fresh token pair.

        ``identifier`` is an email address **or** a username: the old API accepted either
        in ``OAuth2PasswordRequestForm.username`` and that behaviour is preserved, resolved
        by a single ``OR`` statement in the repo rather than two sequential lookups.

        :raises UnauthorizedError: no such account, **or** the password is wrong. One error
            for both, with identical ``code``, ``message`` and ``details``.
        """
        now = datetime.now(UTC)
        user = await self.users.get_by_email_or_username(self.session, identifier)

        # Always hash. `verify_password` against the decoy below can never succeed — the
        # secret behind it is random and discarded — so this is a timing equaliser, not a
        # second authentication path.
        stored_hash = user.password if user is not None else _ABSENT_USER_PASSWORD_HASH
        matched = verify_password(password, stored_hash)

        if user is None or not matched:
            logger.warning("auth.login_failed", identifier_length=len(identifier))
            raise UnauthorizedError(INVALID_CREDENTIALS_MESSAGE)

        logger.info("auth.login_succeeded", user_id=str(user.user_id))
        return self._mint_pair(user, now=now)

    async def refresh(self, *, refresh_token: str) -> TokenPair:
        """Rotate a refresh token into a brand-new pair.

        Three things have to be true, in this order: the token verifies **as a refresh
        token** (an access token raises
        :class:`~app.domain.auth.WrongTokenTypeError` — the whole point of this epic), it
        has not expired, and the account it names still exists.

        Both halves are re-minted rather than just the access token. Rotating the refresh
        token on every use is what puts a bound on a stolen one in practice.

        :raises TokenError: malformed, expired, or the wrong half of the pair.
        :raises UnauthorizedError: the account has since been deleted.
        """
        now = datetime.now(UTC)
        payload = decode_refresh_token(
            refresh_token,
            now=now,
            secret=self._secret,
            algorithm=self.settings.jwt_algorithm,
        )
        user = await self._load_subject(payload.sub)
        logger.info("auth.refreshed", user_id=str(user.user_id))
        return self._mint_pair(user, now=now)

    async def recovery(self, *, username: str) -> RecoveryAccepted:
        """Accept a "I have forgotten my password" request.

        **This does not send anything, and says so.** Anvex has no mail client yet — there
        is no module under ``app/clients/`` that speaks SMTP or SES, and inventing a
        pretend one here would be worse than the gap. What this method genuinely does is:
        look the account up, record the request in the structured log with the ``user_id``
        an operator can act on, and return. The reset-link half of the story arrives with
        the mail client; the ``TODO`` below is the whole of the missing work.

        What it does do properly is **refuse to be an account-existence oracle.** The old
        endpoint answered 404 with ``"User not found with username: <x>"``, which turned
        password recovery into a free username-enumeration API. The response here is
        byte-for-byte identical whether or not the account exists — same status, same body
        — and only the server-side log distinguishes them.
        """
        now = datetime.now(UTC)
        user = await self.users.get_by_username(self.session, username)

        if user is None:
            # Logged at info, not warning: a typo is the common case, and this is the only
            # place the two branches differ at all.
            logger.info("auth.recovery_requested_for_unknown_account", requested_at=now.isoformat())
        else:
            # TODO(ANV-mail): dispatch a signed, single-use reset link through a mail
            # client in `app/clients/` once one exists. Until then this is a no-op with a
            # log line — nothing is delivered to the user.
            logger.info(
                "auth.recovery_requested",
                user_id=str(user.user_id),
                requested_at=now.isoformat(),
                delivered=False,
            )

        return RecoveryAccepted()

    async def authenticate(self, token: str | None) -> User:
        """Resolve a bearer **access** token to the account that owns it.

        The engine behind ``app/deps/auth.py``'s ``get_current_user``. It lives here rather
        than in the dependency because it is logic — decode, then re-read — and a
        dependency only wires things together (``CLAUDE.md`` §3). Keeping it here also
        means a Celery task or a WebSocket handler can authenticate without FastAPI.

        The account is re-read on every request rather than trusted from the claims, so a
        deleted user's outstanding access tokens stop working immediately.

        :raises TokenError: missing, malformed, expired, or a refresh token.
        :raises UnauthorizedError: the account no longer exists.
        """
        now = datetime.now(UTC)
        payload = decode_access_token(
            token,
            now=now,
            secret=self._secret,
            algorithm=self.settings.jwt_algorithm,
        )
        return await self._load_subject(payload.sub)

    # -----------------------------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------------------------

    @property
    def _secret(self) -> str:
        """The JWT signing key, unwrapped. ``SecretStr`` stops at this layer."""
        return self.settings.jwt_secret_key.get_secret_value()

    async def _load_subject(self, user_id: uuid.UUID) -> User:
        """Re-read the account a verified token names, or refuse the token."""
        user = await self.users.get_by_id(self.session, user_id)
        if user is None:
            logger.warning("auth.token_subject_missing", user_id=str(user_id))
            raise UnauthorizedError(UNKNOWN_ACCOUNT_MESSAGE)
        return user

    def _mint_pair(self, user: User, *, now: datetime) -> TokenPair:
        """Mint both tokens from the single ``now`` the caller already read.

        ``now`` is a parameter and not a fresh reading on purpose: the domain is pure and
        the service is the one place allowed to look at a clock, so passing it through is
        what keeps expiry testable without a ``sleep``.
        """
        return create_token_pair(
            subject=user.user_id,
            now=now,
            access_lifetime=timedelta(minutes=self.settings.jwt_access_token_expire_minutes),
            refresh_lifetime=timedelta(minutes=self.settings.jwt_refresh_token_expire_minutes),
            secret=self._secret,
            algorithm=self.settings.jwt_algorithm,
        )


__all__ = [
    "INVALID_CREDENTIALS_MESSAGE",
    "UNKNOWN_ACCOUNT_MESSAGE",
    "AuthService",
]
