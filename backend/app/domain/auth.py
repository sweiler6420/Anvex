"""JWT claim construction and verification — the pure half of authentication.

Everything here is a function of its arguments. There is no clock read, no settings
lookup, no framework import and no I/O (``CLAUDE.md`` §3), which is what makes token
expiry testable without a ``sleep`` and what keeps this layer reusable from a Celery task
that has no request to hang settings off.

**The clock is a parameter.** Every function that needs the time takes ``now`` as a
required keyword-only, timezone-aware :class:`~datetime.datetime`. ANV-11's service is
where the real clock is read, exactly once per operation, and passed down. A naive
datetime is rejected rather than assumed to be UTC: ``.timestamp()`` would interpret it
in the server's local zone and mint a token that expires at the wrong moment.

**Expiry is checked here, not by the decoder.** :func:`decode_token` disables the JWT
library's own ``exp`` check and compares against the injected ``now`` instead. Otherwise
the library would silently consult the wall clock and this module would only be pure on
paper.

**The security fix this module exists to make.** The old API called one
``verify_access_token`` on whatever it was handed, so ``/v1/refresh`` happily accepted an
*access* token and traded it for a fresh long-lived pair: a leaked short-lived token could
be renewed forever. Every token now carries a ``type`` claim, and — more importantly —
**there is no function here that decodes a token without being told which type it must
be**. :func:`decode_token`'s ``expected_type`` is keyword-only with no default, so
forgetting it is a :class:`TypeError` at the call site rather than a hole in production;
:func:`decode_access_token` and :func:`decode_refresh_token` pin it in their names. The
rule is enforced by the shape of the API, not by a comment asking callers to remember.

Failures are classified into the three things a caller can act on differently — expired,
invalid, wrong type — as subclasses of :class:`~app.domain.errors.UnauthorizedError`, so
they all map to 401 and ``except TokenError`` catches the lot.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from jose import jwt
from jose.exceptions import JOSEError
from pydantic import ValidationError as PydanticValidationError

from app.domain.errors import UnauthorizedError
from app.schemas.auth import TokenPair, TokenPayload, TokenType

#: The two values of the ``type`` claim. Spelled once, here.
ACCESS_TOKEN_TYPE: Final[TokenType] = "access"
REFRESH_TOKEN_TYPE: Final[TokenType] = "refresh"

#: The claim carrying which half of the pair a token is. A JWT *registered* claim it is
#: not, which is precisely why it has to be checked explicitly.
TOKEN_TYPE_CLAIM: Final[str] = "type"


class TokenError(UnauthorizedError):
    """Base for every way a token can fail to authenticate its bearer.

    A subclass of :class:`~app.domain.errors.UnauthorizedError`, so the middleware maps it
    to 401 without a new entry in its table, and ``except TokenError`` distinguishes "the
    token is bad" from "the password is bad" without naming three classes.
    """

    code = "invalid_token"
    default_message = "The token could not be validated."


class InvalidTokenError(TokenError):
    """Malformed, truncated, tampered with, or signed with the wrong key or algorithm.

    One error for all of those on purpose: which check failed is useful to an attacker and
    useless to a client, whose only sane response is to authenticate again.
    """

    code = "invalid_token"
    default_message = "The token could not be validated."


class ExpiredTokenError(TokenError):
    """The token was valid but its ``exp`` has passed, as judged by the injected clock.

    Separated from :class:`InvalidTokenError` because it is the one token failure a client
    should *handle* rather than report: the frontend answers a 401 carrying this code by
    spending its refresh token, not by dumping the user at the login screen.
    """

    code = "token_expired"
    default_message = "The token has expired."


class WrongTokenTypeError(TokenError):
    """A well-formed, in-date token of the wrong kind — the old ``/v1/refresh`` bug.

    Carries both types in ``details`` because the two ways to reach it are a client bug
    and an attack, and telling them apart from one log line is worth more than the very
    little this reveals to whoever supplied the token and already knows what it was.
    """

    code = "wrong_token_type"
    default_message = "The token is not valid for this operation."

    def __init__(self, *, expected: TokenType, actual: str) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(details={"expected_type": expected, "actual_type": actual})


def _require_aware(moment: datetime, *, name: str) -> datetime:
    """Return ``moment`` as UTC, refusing a naive datetime.

    A naive value is ambiguous, and ``.timestamp()`` resolves the ambiguity using the
    server's local zone — so identical code would mint different expiries on a developer
    laptop and in a container. Better a loud ``ValueError`` at the boundary.
    """
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise ValueError(f"{name} must be timezone-aware; got the naive datetime {moment!r}.")
    return moment.astimezone(UTC)


def build_claims(
    *,
    subject: uuid.UUID,
    token_type: TokenType,
    now: datetime,
    lifetime: timedelta,
) -> dict[str, Any]:
    """Build the claim set for one token. Pure: same inputs, same output, always.

    ``sub`` is stringified because JWT requires a string subject; ``iat`` and ``exp`` are
    integer Unix timestamps derived from ``now`` and ``lifetime`` and from nothing else.
    """
    if lifetime <= timedelta(0):
        raise ValueError(f"lifetime must be positive; got {lifetime!r}.")
    issued_at = _require_aware(now, name="now")
    return {
        "sub": str(subject),
        TOKEN_TYPE_CLAIM: token_type,
        "iat": int(issued_at.timestamp()),
        "exp": int((issued_at + lifetime).timestamp()),
    }


def encode_token(claims: dict[str, Any], *, secret: str, algorithm: str) -> str:
    """Sign ``claims`` with an injected secret and algorithm."""
    return jwt.encode(claims, secret, algorithm=algorithm)


def create_token(
    *,
    subject: uuid.UUID,
    token_type: TokenType,
    now: datetime,
    lifetime: timedelta,
    secret: str,
    algorithm: str,
) -> str:
    """Mint one signed token.

    ``token_type`` is required: a token minted without one cannot be checked for one.
    """
    claims = build_claims(subject=subject, token_type=token_type, now=now, lifetime=lifetime)
    return encode_token(claims, secret=secret, algorithm=algorithm)


def create_token_pair(
    *,
    subject: uuid.UUID,
    now: datetime,
    access_lifetime: timedelta,
    refresh_lifetime: timedelta,
    secret: str,
    algorithm: str,
) -> TokenPair:
    """Mint both halves from a single ``now``.

    One clock reading for the pair, so the two ``iat`` claims agree exactly. Both are
    minted on refresh as well as on login — rotating the refresh token is what makes a
    stolen one expire in practice (see :class:`~app.schemas.auth.TokenPair`).
    """
    return TokenPair(
        access_token=create_token(
            subject=subject,
            token_type=ACCESS_TOKEN_TYPE,
            now=now,
            lifetime=access_lifetime,
            secret=secret,
            algorithm=algorithm,
        ),
        refresh_token=create_token(
            subject=subject,
            token_type=REFRESH_TOKEN_TYPE,
            now=now,
            lifetime=refresh_lifetime,
            secret=secret,
            algorithm=algorithm,
        ),
    )


def decode_token(
    token: str | None,
    *,
    expected_type: TokenType,
    now: datetime,
    secret: str,
    algorithm: str,
) -> TokenPayload:
    """Verify ``token`` and return its claims, or raise a :class:`TokenError`.

    ``expected_type`` has no default: there is deliberately no way to ask this module "is
    this token valid?" without also stating what it is supposed to be.

    Checks run signature first, then shape, then expiry, then type — so a forged token
    never gets as far as having its claims believed.

    :raises InvalidTokenError: not a string, empty, malformed, tampered with, signed with
        another key or algorithm, or missing/mistyped claims.
    :raises ExpiredTokenError: ``exp`` is at or before ``now``.
    :raises WrongTokenTypeError: valid and in date, but the wrong half of the pair.
    """
    moment = _require_aware(now, name="now")
    if not isinstance(token, str) or not token:
        raise InvalidTokenError()

    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=[algorithm],
            # Expiry is our job, against the injected clock. Leaving this on would have
            # the library read the wall clock for us and make the module pure on paper
            # only — and would reject a token minted at a caller-supplied future time.
            options={"verify_exp": False},
        )
    except (JOSEError, ValueError, TypeError, KeyError, AttributeError) as exc:
        # `jose` raises `JOSEError` for anything it recognises as a bad token, but its
        # base64/JSON layer leaks the occasional `ValueError` on truly malformed input.
        # A bad token is a 401 in every one of those cases, never a 500.
        raise InvalidTokenError() from exc

    try:
        payload = TokenPayload.model_validate(claims)
    except PydanticValidationError as exc:
        # A correctly signed token missing `type`, or whose `sub` is not a UUID, is as
        # unusable as an unsigned one.
        raise InvalidTokenError() from exc

    if payload.exp <= moment:
        raise ExpiredTokenError()
    if payload.type != expected_type:
        raise WrongTokenTypeError(expected=expected_type, actual=payload.type)
    return payload


def decode_access_token(
    token: str | None, *, now: datetime, secret: str, algorithm: str
) -> TokenPayload:
    """Decode a token that must be an **access** token."""
    return decode_token(
        token, expected_type=ACCESS_TOKEN_TYPE, now=now, secret=secret, algorithm=algorithm
    )


def decode_refresh_token(
    token: str | None, *, now: datetime, secret: str, algorithm: str
) -> TokenPayload:
    """Decode a token that must be a **refresh** token."""
    return decode_token(
        token, expected_type=REFRESH_TOKEN_TYPE, now=now, secret=secret, algorithm=algorithm
    )


__all__ = [
    "ACCESS_TOKEN_TYPE",
    "REFRESH_TOKEN_TYPE",
    "TOKEN_TYPE_CLAIM",
    "ExpiredTokenError",
    "InvalidTokenError",
    "TokenError",
    "WrongTokenTypeError",
    "build_claims",
    "create_token",
    "create_token_pair",
    "decode_access_token",
    "decode_refresh_token",
    "decode_token",
    "encode_token",
]
