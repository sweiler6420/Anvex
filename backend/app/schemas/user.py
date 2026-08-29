"""Account contracts: what a client may send about a user, and what it gets back.

**The one rule this module exists to enforce:** ``users.password`` holds a bcrypt digest
and keeps that legacy column name, and it appears in exactly two places — the body of a
registration and the body of a password change, both inputs. No output schema here has a
``password`` field, and ``tests/unit/test_schemas.py`` walks every model in
``app.schemas`` to prove that no future one grows a leaky field by accident.

Length ceilings mirror ``app/models/user.py`` by importing its constants rather than
restating the numbers, so an oversized username is a 422 at the edge instead of a
``StringDataRightTruncation`` from Postgres — and the two cannot drift apart.

Password *strength* (mixed case, a digit, a symbol) is a business rule, so it belongs in
``app/domain/`` where ANV-12 can test it exhaustively. What lives here is the length
envelope only.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, EmailStr, Field

from app.models.user import EMAIL_MAX_LENGTH, USERNAME_MAX_LENGTH

#: The previous web app refused anything shorter and every account in the old database
#: satisfies it, so raising this would lock existing users out of their own settings page.
USERNAME_MIN_LENGTH = 7

#: Same origin as the username floor: the old sign-up form's rule.
PASSWORD_MIN_LENGTH = 7

#: bcrypt hashes at most 72 **bytes** and silently ignores the rest, which would make two
#: different long passwords interchangeable. Reject beyond the ceiling instead.
PASSWORD_MAX_LENGTH = 72

#: A unique display name, bounded by ``users.username``'s ``VARCHAR(50)``.
Username = Annotated[str, Field(min_length=USERNAME_MIN_LENGTH, max_length=USERNAME_MAX_LENGTH)]

#: RFC 5321's maximum forward path, and the width of ``users.email``. ``EmailStr`` supplies
#: the format check; the cap stops a megabyte of "address" reaching the database.
Email = Annotated[EmailStr, Field(max_length=EMAIL_MAX_LENGTH)]

#: A **plaintext** password on its way in. Never a field on an output schema.
Password = Annotated[str, Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)]


class UserCreate(BaseModel):
    """``POST /v1/users`` — register an account.

    ``password`` is the plaintext the caller chose; the service hashes it and stores only
    the digest. It is never logged and never echoed.
    """

    username: Username
    email: Email
    password: Password = Field(
        description="Plaintext, hashed server-side. Never returned by any endpoint.",
    )


class UserUpdate(BaseModel):
    """``PATCH /v1/users/me`` — change the profile fields a user owns.

    Every field is optional and ``None`` means "leave it alone": neither column is
    nullable, so ``None`` can never mean "clear it". Password changes go through
    :class:`PasswordChange` instead, because they need the current password.
    """

    username: Username | None = None
    email: Email | None = None


class PasswordChange(BaseModel):
    """``POST /v1/users/me/password`` — rotate the password on a signed-in account.

    Separate from :class:`UserUpdate` on purpose. A password change re-authenticates
    (``current_password``) and a profile edit does not; folding them together would mean
    either an optional proof of identity or a pointless one on every rename.

    ``current_password`` is only bounded, never floored: it is checked against the stored
    digest, and applying today's minimum length to it would reject a legitimate holder of
    an older, shorter password instead of simply telling them it is wrong.
    """

    current_password: str = Field(
        min_length=1,
        max_length=PASSWORD_MAX_LENGTH,
        description="Proves the session belongs to whoever knows the password.",
    )
    new_password: Password


class UserOut(BaseModel):
    """The public shape of an account. **Never carries the password digest.**"""

    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    username: str
    email: EmailStr
    #: ``TIMESTAMPTZ``. Typed as aware so a naive value is a validation error here rather
    #: than an ambiguous timestamp for a client six time zones away.
    created_at: AwareDatetime


__all__ = [
    "PASSWORD_MAX_LENGTH",
    "PASSWORD_MIN_LENGTH",
    "USERNAME_MIN_LENGTH",
    "Email",
    "Password",
    "PasswordChange",
    "UserCreate",
    "UserOut",
    "UserUpdate",
    "Username",
]
