"""Builder for :class:`app.models.User`."""

from __future__ import annotations

from typing import Any

from app.models import User
from tests.factories.base import Factory, register

#: A stand-in for a bcrypt digest. Hashing is ANV-10's job and costs ~100 ms a call, which
#: is far too much to pay in every fixture that happens to need a user.
PLACEHOLDER_PASSWORD_HASH = "$2b$12$notarealbcrypthashnotarealbcrypthashnotarealbcrypth"


@register
class UserFactory(Factory[User]):
    """A registered account.

    ``username`` and ``email`` are both unique columns, so both come from the sequence —
    ``fake.user_name()`` repeats within a single test because the seed is reset per test.
    """

    model = User

    def defaults(self) -> dict[str, Any]:
        n = self.sequence()
        return {
            "username": f"user{n:04d}",
            "email": f"user{n:04d}@example.com",
            "password": PLACEHOLDER_PASSWORD_HASH,
        }


__all__ = ["PLACEHOLDER_PASSWORD_HASH", "UserFactory"]
