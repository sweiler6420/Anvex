"""Factory infrastructure: the base class, the registry, and deterministic seeding.

``app/models/`` is empty until ANV-7, so this module ships the *pattern* rather than any
concrete factory. It is deliberately about 100 lines of behaviour — a full factory library
(`factory_boy`) buys very little on top of SQLAlchemy 2.0 + faker and costs a dependency
whose async support is an afterthought.

**The pattern.** One :class:`Factory` subclass per model, next to it in this package::

    # tests/factories/user.py
    from app.models import User
    from tests.factories.base import Factory, fake, register


    @register
    class UserFactory(Factory[User]):
        model = User

        def defaults(self) -> dict[str, object]:
            n = self.sequence()          # unique per instance, reset every test
            return {
                "email": f"user{n}@example.com",
                "display_name": fake.name(),
                "hashed_password": "not-a-real-hash",
            }

then, in a test::

    user = await UserFactory().create(db_session)          # added + flushed, not committed
    draft = UserFactory().build(email="pinned@example.com")  # in memory only

**Two rules that matter.**

*Unique columns come from* :meth:`Factory.sequence`, *never from faker.* Seeding is reset
before every test (see :func:`reset_randomness`), so ``fake.email()`` returns the *same*
address twice within one test and a unique constraint fires. The sequence is what keeps
sibling rows distinct.

*A factory flushes, it never commits.* The transaction boundary belongs to the service
layer (``CLAUDE.md`` §3), and the harness's rollback fixture owns it in tests. ``flush()``
is enough to populate server defaults and make the row visible to subsequent queries on
the same session.
"""

from __future__ import annotations

import itertools
import random
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from faker import Faker
from sqlalchemy.ext.asyncio import AsyncSession

#: Fixed seed, so a failing test reproduces from its name alone. Any value would do; what
#: matters is that it never changes silently — bumping it can only change generated data.
DEFAULT_SEED = 20260828

#: The shared faker. Import this rather than constructing your own, so
#: :func:`reset_randomness` actually governs the values your factory produces.
fake: Faker = Faker()

_sequences: dict[str, itertools.count[int]] = {}
_registry: dict[type[Any], type[Factory[Any]]] = {}


def reset_randomness(seed: int = DEFAULT_SEED) -> None:
    """Re-seed faker and :mod:`random`, and restart every sequence at 1.

    Called by an autouse fixture before each test, which is what makes generated data a
    function of the test rather than of how many tests ran before it. Without the reset,
    a factory's output depends on test *order* and a failure reproduces only under the
    full suite.
    """
    Faker.seed(seed)
    fake.seed_instance(seed)
    random.seed(seed)
    _sequences.clear()


def next_in_sequence(name: str) -> int:
    """Return the next integer in the named sequence, starting at 1."""
    counter = _sequences.get(name)
    if counter is None:
        counter = itertools.count(1)
        _sequences[name] = counter
    return next(counter)


class Factory[ModelT](ABC):
    """Base class for model builders.

    Subclasses set :attr:`model` and implement :meth:`defaults`; everything else is
    inherited. Instances are cheap and stateless — construct one per use.
    """

    #: The SQLAlchemy model this factory builds. Set on the subclass.
    model: ClassVar[type[Any]]

    @abstractmethod
    def defaults(self) -> dict[str, Any]:
        """Return the keyword arguments for a fresh instance.

        Called once per built object, so it may (and should) draw fresh values from
        :data:`fake` and :meth:`sequence`.
        """

    def sequence(self) -> int:
        """Next integer in this factory's own sequence — use it for unique columns."""
        return next_in_sequence(type(self).__name__)

    def build(self, **overrides: Any) -> ModelT:
        """Construct an unsaved instance. Pure: no session, no I/O.

        Explicit ``overrides`` always win over :meth:`defaults`, which is what lets a test
        pin the one field it is actually about and ignore the rest.
        """
        return self.model(**{**self.defaults(), **overrides})

    def build_many(self, count: int, **overrides: Any) -> list[ModelT]:
        """``count`` unsaved instances, each with its own sequence values."""
        return [self.build(**overrides) for _ in range(count)]

    async def create(self, session: AsyncSession, **overrides: Any) -> ModelT:
        """Build, add and **flush** — never commit (see the module docstring)."""
        instance = self.build(**overrides)
        session.add(instance)
        await session.flush()
        return instance

    async def create_many(
        self, session: AsyncSession, count: int, **overrides: Any
    ) -> list[ModelT]:
        """``count`` persisted instances, flushed once at the end."""
        instances = self.build_many(count, **overrides)
        session.add_all(instances)
        await session.flush()
        return instances


def register(factory_cls: type[Factory[Any]]) -> type[Factory[Any]]:
    """Class decorator recording ``factory_cls`` as the factory for its model.

    Lets a helper reach a factory by model type without importing every module, and makes
    a duplicate registration a loud failure instead of a silent last-one-wins.
    """
    model = getattr(factory_cls, "model", None)
    if model is None:
        raise TypeError(f"{factory_cls.__name__} must set `model` before it can be registered.")
    existing = _registry.get(model)
    if existing is not None and existing is not factory_cls:
        raise ValueError(
            f"{model.__name__} already has a factory ({existing.__name__}); "
            f"{factory_cls.__name__} would shadow it."
        )
    _registry[model] = factory_cls
    return factory_cls


def factory_for[ModelT](model: type[ModelT]) -> Factory[ModelT]:
    """Return a new factory instance for ``model``, or raise if none is registered."""
    try:
        factory_cls = _registry[model]
    except KeyError:
        raise LookupError(
            f"no factory registered for {getattr(model, '__name__', model)!r}. "
            "Add one in tests/factories/ and decorate it with @register."
        ) from None
    return factory_cls()


def registered_factories() -> dict[type[Any], type[Factory[Any]]]:
    """A copy of the registry — for introspection and tests, not for mutation."""
    return dict(_registry)


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
