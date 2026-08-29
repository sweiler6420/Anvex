"""Password hashing primitives — bcrypt, and nothing that knows what Anvex is.

``CLAUDE.md`` §3 draws the line here: a helper that mentions an Anvex concept belongs in
``app/domain/``. This module takes a string and gives back a hash, so it lives in
``app/utils/``. It imports no framework, reads no settings, and touches no database; the
service layer decides *whose* password this is and where the hash is stored.

**Why the ``bcrypt`` package directly (ANV-42).** This used to go through
``passlib.context.CryptContext``. passlib 1.7.4 is its final release (October 2020) and
cannot work with a current bcrypt: it probes its backend at first use by hashing a
>72-byte secret, which bcrypt 5.0 turned from a silent truncation into a ``ValueError``,
and it reads ``bcrypt.__about__``, which 4.1 removed. Keeping passlib meant pinning
``bcrypt>=4.0,<4.1`` — freezing a security library on a 2022 release. Calling ``bcrypt``
directly is about fifteen lines and lets it track its maintained line. The stored format
is unchanged standard ``$2b$`` bcrypt, so every hash written by the passlib path still
verifies here (``tests/unit/test_security.py`` pins a real one).

**The 72-byte boundary.** bcrypt hashes at most 72 bytes of the secret and ignores the
rest, so ``"a" * 72`` and ``"a" * 200`` are the *same credential* — a user who sets a long
passphrase can sign in with any prefix of it, which is a silent downgrade nobody would
ever notice. This module therefore **rejects** an over-long password at
:func:`hash_password` rather than documenting the truncation and hoping.

Rejecting here rather than leaving it to the caller is not belt-and-braces. ANV-8 caps
``PASSWORD_MAX_LENGTH`` at 72 **characters**; bcrypt's limit is 72 **bytes**. A
25-character password of three-byte characters is 75 bytes: it passes the schema and
still overflows bcrypt. The only place that can enforce the real boundary is the place
that knows the encoding, which is here.

The two directions are deliberately asymmetric:

* :func:`hash_password` **raises**. It is a write we control, and the moment to fail is
  before a credential with a hidden equivalence class is persisted.
* :func:`verify_password` **returns ``False``**. Its inputs are attacker-controlled, and a
  login attempt must never become a 500 — not for an over-long candidate, and not for a
  stored hash that has been truncated, re-encoded or written by some other tool.
"""

from __future__ import annotations

from typing import Final

import bcrypt

#: bcrypt's hard limit. Anything past this byte is not part of the credential.
BCRYPT_MAX_PASSWORD_BYTES: Final[int] = 72

#: Cost factor (log2 of the rounds), chosen here rather than inherited from whatever
#: ``bcrypt.gensalt()`` happens to default to, so an upstream change cannot silently move
#: our work factor in either direction. 12 is what passlib's context used before ANV-42,
#: which keeps login latency and the security margin identical across the swap — roughly a
#: quarter of a second per hash on current hardware, slow enough to hurt an offline attack
#: and fast enough for a login request. Raising it is a deliberate future change: old
#: hashes carry their own cost in the string and keep verifying.
BCRYPT_COST_FACTOR: Final[int] = 12

#: bcrypt works in bytes; this module's public API is ``str``. Encode and decode at the
#: boundary and nowhere else, so the stored hash stays a ``str``.
_ENCODING: Final[str] = "utf-8"


class PasswordTooLongError(ValueError):
    """The password is longer than bcrypt can actually hash.

    A ``ValueError`` and not a domain error on purpose: ``app/utils/`` has no Anvex
    meaning and must not import ``app.domain``. The service layer translates it — and at
    the HTTP edge ANV-8's schema has usually rejected the value long before.

    Raised by :func:`hash_password` in place of the ``ValueError`` bcrypt 5.x raises for
    the same condition, so callers keep the one exception type they already handle.
    """


def password_byte_length(password: str) -> int:
    """UTF-8 length of ``password`` — the unit bcrypt actually counts in."""
    return len(password.encode(_ENCODING))


def exceeds_bcrypt_limit(password: str) -> bool:
    """Whether bcrypt would silently discard part of ``password``."""
    return password_byte_length(password) > BCRYPT_MAX_PASSWORD_BYTES


def hash_password(password: str) -> str:
    """Return a salted bcrypt hash of ``password``.

    Each call generates a fresh salt, so hashing the same password twice yields two
    different strings and both verify. Raises :class:`PasswordTooLongError` if the
    password exceeds :data:`BCRYPT_MAX_PASSWORD_BYTES`.
    """
    if exceeds_bcrypt_limit(password):
        raise PasswordTooLongError(
            f"password is {password_byte_length(password)} bytes; bcrypt hashes at most "
            f"{BCRYPT_MAX_PASSWORD_BYTES} and would silently ignore the rest."
        )
    salt = bcrypt.gensalt(rounds=BCRYPT_COST_FACTOR)
    return bcrypt.hashpw(password.encode(_ENCODING), salt).decode("ascii")


def verify_password(password: str, hashed_password: str | None) -> bool:
    """Return whether ``password`` matches ``hashed_password``.

    Never raises. ``None``, an empty string, a hash from another scheme, or a bcrypt
    string that has been truncated in the database all return ``False``: a corrupted
    stored hash should fail one login, not 500 the request. An over-long candidate is
    rejected without consulting bcrypt at all — :func:`hash_password` refuses to create
    such a hash, so no over-long password can ever be the right answer.

    The comparison itself is :func:`bcrypt.checkpw`, which is constant-time with respect
    to the digest; the early returns above depend only on the *stored* value and the
    candidate's length, never on how much of a real hash matched.
    """
    if not hashed_password:
        return False
    if exceeds_bcrypt_limit(password):
        return False
    try:
        return bcrypt.checkpw(password.encode(_ENCODING), hashed_password.encode(_ENCODING))
    except (ValueError, TypeError):
        # bcrypt raises `ValueError("Invalid salt")` for a string it cannot parse as a
        # bcrypt hash — an unknown scheme, a truncated digest, a bad cost — and a
        # `TypeError` for a non-`str` stored value that slipped past the annotation.
        # Deliberately not a bare `except Exception`: a broken bcrypt install is a
        # deployment fault and must stay loud rather than turning every login into
        # "wrong password".
        return False


__all__ = [
    "BCRYPT_COST_FACTOR",
    "BCRYPT_MAX_PASSWORD_BYTES",
    "PasswordTooLongError",
    "exceeds_bcrypt_limit",
    "hash_password",
    "password_byte_length",
    "verify_password",
]
