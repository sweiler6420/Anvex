"""Password hashing primitives — bcrypt, and nothing that knows what Anvex is.

``CLAUDE.md`` §3 draws the line here: a helper that mentions an Anvex concept belongs in
``app/domain/``. This module takes a string and gives back a hash, so it lives in
``app/utils/``. It imports no framework, reads no settings, and touches no database; the
service layer decides *whose* password this is and where the hash is stored.

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

from passlib.context import CryptContext

#: bcrypt's hard limit. Anything past this byte is not part of the credential.
BCRYPT_MAX_PASSWORD_BYTES: Final[int] = 72

#: ``deprecated="auto"`` costs nothing today and is what lets a second scheme be added
#: later: existing bcrypt hashes keep verifying while new ones use the new default.
_password_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class PasswordTooLongError(ValueError):
    """The password is longer than bcrypt can actually hash.

    A ``ValueError`` and not a domain error on purpose: ``app/utils/`` has no Anvex
    meaning and must not import ``app.domain``. The service layer translates it — and at
    the HTTP edge ANV-8's schema has usually rejected the value long before.
    """


def password_byte_length(password: str) -> int:
    """UTF-8 length of ``password`` — the unit bcrypt actually counts in."""
    return len(password.encode("utf-8"))


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
    return _password_context.hash(password)


def verify_password(password: str, hashed_password: str | None) -> bool:
    """Return whether ``password`` matches ``hashed_password``.

    Never raises. ``None``, an empty string, a hash from another scheme, or a bcrypt
    string that has been truncated in the database all return ``False``: a corrupted
    stored hash should fail one login, not 500 the request. An over-long candidate is
    rejected without consulting bcrypt at all — :func:`hash_password` refuses to create
    such a hash, so no over-long password can ever be the right answer.

    The comparison itself is passlib's, which is constant-time with respect to the
    digest; the early returns above depend only on the *stored* value and the candidate's
    length, never on how much of a real hash matched.
    """
    if not hashed_password:
        return False
    if exceeds_bcrypt_limit(password):
        return False
    try:
        return _password_context.verify(password, hashed_password)
    except (ValueError, TypeError):
        # passlib raises `UnknownHashError` (a `ValueError`) for an unrecognised string
        # and a plain `ValueError` for a structurally broken bcrypt hash. Deliberately
        # not a bare `except Exception`: a missing bcrypt backend is a deployment fault
        # and must stay loud rather than turning every login into "wrong password".
        return False


__all__ = [
    "BCRYPT_MAX_PASSWORD_BYTES",
    "PasswordTooLongError",
    "exceeds_bcrypt_limit",
    "hash_password",
    "password_byte_length",
    "verify_password",
]
