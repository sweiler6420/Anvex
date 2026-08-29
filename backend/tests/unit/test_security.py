"""Unit tests for ``app/utils/security.py``.

Pure tier: no fixtures, no I/O, no app. bcrypt is deliberately slow, so this module keeps
the number of real hash calls small and reuses a handful of module-level digests.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from app.utils import security
from app.utils.security import (
    BCRYPT_MAX_PASSWORD_BYTES,
    PasswordTooLongError,
    exceeds_bcrypt_limit,
    hash_password,
    password_byte_length,
    verify_password,
)

PASSWORD = "correct horse battery staple"
HASHED = hash_password(PASSWORD)

#: A three-byte UTF-8 character, so character count and byte count disagree threefold.
WIDE = "漢"


class TestLayering:
    """``app/utils/`` holds framework-agnostic helpers with no Anvex meaning (§3)."""

    def test_module_imports_nothing_from_the_application(self) -> None:
        tree = ast.parse(Path(security.__file__).read_text(encoding="utf-8"))
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                roots.add(node.module.split(".")[0])

        assert "app" not in roots, "a util that imports app/ has Anvex meaning and is domain"
        assert roots - sys.stdlib_module_names == {"bcrypt"}


class TestHashing:
    def test_hash_verifies(self) -> None:
        assert verify_password(PASSWORD, HASHED) is True

    def test_hash_is_not_the_password(self) -> None:
        assert PASSWORD not in HASHED
        assert HASHED.startswith("$2")

    def test_the_same_password_hashes_to_different_values(self) -> None:
        """Salting. Two identical passwords must not produce identical rows."""
        again = hash_password(PASSWORD)
        assert again != HASHED
        assert verify_password(PASSWORD, again) is True

    @pytest.mark.parametrize(
        "wrong",
        [
            "correct horse battery stapl",
            "correct horse battery staple ",
            "Correct horse battery staple",
            "",
        ],
    )
    def test_a_wrong_password_fails(self, wrong: str) -> None:
        assert verify_password(wrong, HASHED) is False

    def test_an_empty_password_can_be_hashed_and_only_matches_itself(self) -> None:
        """Length rules are the schema's job; this module only owns the bcrypt boundary."""
        hashed_empty = hash_password("")
        assert verify_password("", hashed_empty) is True
        assert verify_password("x", hashed_empty) is False


class TestBrokenStoredHash:
    """A corrupted stored hash must fail the login, not 500 the request."""

    @pytest.mark.parametrize(
        ("label", "stored"),
        [
            ("none", None),
            ("empty", ""),
            ("whitespace", "   "),
            ("plaintext", "correct horse battery staple"),
            ("not-a-hash", "not-a-bcrypt-hash"),
            ("wrong-scheme", "$5$rounds=535000$abcdefgh$0123456789"),
            ("md5-hex", "5f4dcc3b5aa765d61d8327deb882cf99"),
            ("truncated-bcrypt", HASHED[:20]),
            ("bcrypt-prefix-only", "$2b$12$"),
            ("bad-salt-length", "$2b$12$tooshort"),
            ("bad-cost", "$2b$99$" + HASHED[7:]),
            # Mid-digest, not the final character: bcrypt's last character carries padding
            # bits, and passlib warns about those separately from a simple mismatch.
            ("mangled-digest", HASHED[:40] + ("A" if HASHED[40] != "A" else "B") + HASHED[41:]),
        ],
    )
    def test_verify_returns_false_instead_of_raising(self, label: str, stored: str | None) -> None:
        assert verify_password(PASSWORD, stored) is False


class TestByteBoundary:
    """bcrypt hashes 72 **bytes**; past that it silently ignores the rest.

    The decision this module makes: ``hash_password`` rejects rather than truncating, so a
    long passphrase can never quietly become equivalent to its own 72-byte prefix.
    ``verify_password`` returns ``False`` instead, because its input is attacker-supplied
    and a login attempt must not raise.
    """

    def test_the_limit_is_the_documented_one(self) -> None:
        assert BCRYPT_MAX_PASSWORD_BYTES == 72

    def test_exactly_the_limit_is_accepted(self) -> None:
        at_limit = "a" * BCRYPT_MAX_PASSWORD_BYTES
        assert exceeds_bcrypt_limit(at_limit) is False
        assert verify_password(at_limit, hash_password(at_limit)) is True

    def test_one_byte_past_the_limit_is_rejected(self) -> None:
        over = "a" * (BCRYPT_MAX_PASSWORD_BYTES + 1)
        assert exceeds_bcrypt_limit(over) is True
        with pytest.raises(PasswordTooLongError):
            hash_password(over)

    def test_the_error_is_a_value_error(self) -> None:
        """So a caller that only knows ``ValueError`` still handles it."""
        assert issubclass(PasswordTooLongError, ValueError)
        with pytest.raises(ValueError, match="72"):
            hash_password("a" * 200)

    def test_the_boundary_is_bytes_not_characters(self) -> None:
        """25 characters, 75 bytes: it passes ANV-8's 72-*character* schema cap and would
        still overflow bcrypt, which is why this module cannot delegate the check."""
        password = WIDE * 25
        assert len(password) == 25
        assert password_byte_length(password) == 75
        assert exceeds_bcrypt_limit(password) is True
        with pytest.raises(PasswordTooLongError):
            hash_password(password)

    def test_a_wide_password_inside_the_limit_still_works(self) -> None:
        password = WIDE * 24  # 72 bytes exactly
        assert password_byte_length(password) == BCRYPT_MAX_PASSWORD_BYTES
        assert verify_password(password, hash_password(password)) is True

    def test_ascii_byte_length_matches_character_length(self) -> None:
        assert password_byte_length(PASSWORD) == len(PASSWORD)

    def test_verify_rejects_an_over_long_candidate_without_raising(self) -> None:
        """The truncation hazard, from the other side: were the extra bytes dropped, a
        200-character guess sharing the first 72 bytes would authenticate."""
        prefix = "a" * BCRYPT_MAX_PASSWORD_BYTES
        stored = hash_password(prefix)
        assert verify_password(prefix + "b" * 128, stored) is False


class TestLegacyPasslibHash:
    """ANV-42 swapped passlib for the ``bcrypt`` package. Stored hashes must survive.

    Both paths emit standard ``$2b$`` bcrypt, so this *should* be a formality — which is
    exactly why it is asserted rather than assumed. The digest below was generated by the
    ``CryptContext(schemes=["bcrypt"])`` code ANV-42 deleted, for :data:`PASSWORD`.
    """

    #: Real output of the removed path: ``CryptContext(schemes=["bcrypt"],
    #: deprecated="auto").hash(PASSWORD)`` under passlib 1.7.4 + bcrypt 4.0.1, the exact
    #: pair ANV-42 removed. Cost 12, the same factor the direct implementation now sets
    #: explicitly.
    LEGACY_HASH = "$2b$12$uml6ERvROSHaL1OV01a/Ieod8ta0Yrf7H.rcDkiG9GiPDKWTThKi."

    def test_a_hash_written_by_passlib_still_verifies(self) -> None:
        assert verify_password(PASSWORD, self.LEGACY_HASH) is True

    def test_a_wrong_password_against_a_passlib_hash_still_fails(self) -> None:
        assert verify_password("Correct horse battery staple", self.LEGACY_HASH) is False
