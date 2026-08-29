"""Faker-backed model builders.

ANV-6 ships the infrastructure only — :mod:`tests.factories.base` — because
``app/models/`` is empty until ANV-7. **ANV-7 adds one module per model group here**
(``user.py``, ``stock.py``, ``watchlist.py``, …), each defining a ``@register``-decorated
:class:`~tests.factories.base.Factory` subclass, and re-exports them from this file so a
test writes ``from tests.factories import UserFactory``.

Read the module docstring in ``base.py`` before adding one: it carries the pattern and the
two rules (unique columns come from ``self.sequence()``, and a factory flushes but never
commits).
"""

from __future__ import annotations

from tests.factories.base import (
    DEFAULT_SEED,
    Factory,
    factory_for,
    fake,
    next_in_sequence,
    register,
    registered_factories,
    reset_randomness,
)

__all__ = [
    "DEFAULT_SEED",
    "Factory",
    "factory_for",
    "fake",
    "next_in_sequence",
    "register",
    "registered_factories",
    "reset_randomness",
]
