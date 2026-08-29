"""Authentication contracts: the token pair, its claims, and the two bodies that ask for one.

``TokenPair`` is the shape the existing frontend already parses — it reads
``response.access_token`` and ``response.refresh_token`` out of the login and refresh
responses and puts the refresh token in ``localStorage`` — so its three keys are fixed.
The value of ``token_type`` is not: nothing in the web app reads it (the interceptor
hardcodes ``Bearer``), so it is spelled the way RFC 6750 spells it.

``TokenPayload`` is the *decoded* JWT, never a request or response body. It is the
contract between ANV-10, which mints tokens, and ``app/deps/`` , which turns the bearer
header back into a user. Two deliberate changes from the old API:

* the subject is the standard ``sub`` claim rather than a bespoke ``user_id``, so anything
  that speaks JWT can read it;
* every token names its own ``type``. The old ``/v1/refresh`` called the same
  ``verify_access_token`` on whatever it was handed, so an *access* token was accepted as
  a refresh token — a stolen short-lived token could be traded for a long-lived one
  forever. ANV-10 must check this claim, not merely the signature.

Login itself has no schema here: ``CLAUDE.md`` §4 fixes ``OAuth2PasswordBearer``, whose
token endpoint takes ``application/x-www-form-urlencoded`` credentials via FastAPI's
``OAuth2PasswordRequestForm``. Modelling that body as JSON would break the standard flow
and Swagger's Authorize button with it.
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.models.user import USERNAME_MAX_LENGTH

#: Which half of the pair a token is. Carried as a claim so one cannot stand in for the
#: other; see the module docstring.
TokenType = Literal["access", "refresh"]

#: The scheme name in the ``Authorization`` header the client must build from this pair.
TOKEN_TYPE = "bearer"

#: The single sentence ``POST /v1/auth/recovery`` returns, for every request. Deliberately
#: phrased in the conditional — it makes no claim about whether an account was found, and
#: no claim that anything has actually been sent.
RECOVERY_MESSAGE = "If an account matches that username, a password reset will be arranged for it."


class TokenPair(BaseModel):
    """The body of a successful ``/v1/login`` or ``/v1/refresh``.

    Both tokens are returned on refresh, not just the access token: rotating the refresh
    token on every use is what makes a stolen one expire in practice rather than in theory.
    """

    access_token: str = Field(description="Short-lived. Sent as `Authorization: Bearer <token>`.")
    refresh_token: str = Field(description="Long-lived. Exchanged at `/v1/refresh`.")
    token_type: Literal["bearer"] = Field(
        default=TOKEN_TYPE,
        description="Always `bearer`. Present because OAuth2 requires it.",
    )


class TokenPayload(BaseModel):
    """A decoded, signature-verified JWT.

    Not an HTTP body — this is what ``jose.jwt.decode`` produced, validated into types
    Python can use. ``exp`` and ``iat`` arrive as Unix timestamps and land as timezone-aware
    UTC datetimes; ``sub`` is encoded as a string (JWT requires it) and comes back a
    :class:`~uuid.UUID`.
    """

    model_config = ConfigDict(from_attributes=True)

    sub: uuid.UUID = Field(description="The `user_id` the token was issued for.")
    exp: AwareDatetime = Field(description="Expiry. Enforced by the decoder, not by us.")
    iat: AwareDatetime = Field(description="Issued-at.")
    type: TokenType = Field(description="Which half of the pair this is.")


class RefreshRequest(BaseModel):
    """The body of ``POST /v1/refresh``.

    A JSON body, deliberately: the old endpoint took the token as a **query string**
    parameter, which put a long-lived credential into every proxy log and browser history
    entry between the client and us.
    """

    refresh_token: str = Field(min_length=1, description="The refresh token to exchange.")


class RecoveryRequest(BaseModel):
    """The body of ``POST /v1/recovery`` — "I have forgotten my password".

    Keyed on the username because that is what the existing recovery form submits. No
    floor on the length: the response is identical whether or not the account exists, so
    validating the *shape* of the identifier any harder than the column allows would only
    tell an attacker which guesses were worth making.
    """

    username: str = Field(
        min_length=1,
        max_length=USERNAME_MAX_LENGTH,
        description="The account's username.",
    )


class RecoveryAccepted(BaseModel):
    """The body of ``POST /v1/auth/recovery`` — **always this, whoever asked**.

    A fixed response with no caller-controlled fields, because the one thing this endpoint
    must never do is tell an anonymous caller whether a username exists. The old API
    answered 404 ``"User not found with username: <x>"``, which made password recovery a
    free enumeration API; here the existing and non-existing cases are byte-identical and
    only the server-side log tells them apart.

    ``accepted``, not ``sent``: nothing is delivered today (see
    :meth:`app.services.auth.AuthService.recovery` — Anvex has no mail client yet) and the
    wording must not promise an email that is not coming. It stays accurate once one lands,
    since delivery is asynchronous either way.
    """

    status: Literal["accepted"] = Field(
        default="accepted",
        description="Always `accepted`. Never reveals whether the account exists.",
    )
    message: str = Field(
        default=RECOVERY_MESSAGE,
        description="Fixed text, identical for every request.",
    )


__all__ = [
    "RECOVERY_MESSAGE",
    "TOKEN_TYPE",
    "RecoveryAccepted",
    "RecoveryRequest",
    "RefreshRequest",
    "TokenPair",
    "TokenPayload",
    "TokenType",
]
