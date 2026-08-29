"""The factory infrastructure behaves — the fast tier's sample of the harness.

Pure and fixture-free, as ``CLAUDE.md`` §6 requires of ``tests/unit/``: nothing here touches
a database, and ``Factory.build`` is deliberately a pure function so it can be tested this
way. ANV-7's concrete factories inherit every behaviour asserted below, so they do not need
to re-test it — they only need to test their own ``defaults()``.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from tests.factories import Factory, factory_for, fake, register, reset_randomness


@dataclass
class Widget:
    """Stand-in for a real ORM model, which arrives in ANV-7."""

    ticker: str
    name: str


@register
class WidgetFactory(Factory[Widget]):
    model = Widget

    def defaults(self) -> dict[str, object]:
        return {"ticker": f"TCK{self.sequence():04d}", "name": fake.company()}


class TestBuild:
    def test_build_returns_an_unsaved_instance_with_generated_defaults(self) -> None:
        widget = WidgetFactory().build()
        assert isinstance(widget, Widget)
        assert widget.ticker.startswith("TCK")
        assert widget.name

    def test_overrides_win_over_defaults(self) -> None:
        """The whole point: pin the one field the test is about, ignore the rest."""
        widget = WidgetFactory().build(ticker="AAPL")
        assert widget.ticker == "AAPL"
        assert widget.name  # still generated

    def test_build_many_gives_distinct_sequence_values(self) -> None:
        """Unique columns must come from `sequence()`, never from faker — this is why."""
        widgets = WidgetFactory().build_many(5)
        assert len({widget.ticker for widget in widgets}) == 5

    def test_faker_alone_would_repeat_within_a_test(self) -> None:
        """Documents the trap the sequence exists to avoid.

        Seeding is reset per test, so two draws from a *fresh* seed are identical. A
        factory that used `fake.email()` for a unique column would trip its constraint the
        second time it was called in the same test.
        """
        reset_randomness()
        first = fake.company()
        reset_randomness()
        assert fake.company() == first


class TestDeterminism:
    def test_the_same_seed_reproduces_the_same_data(self) -> None:
        reset_randomness()
        first = WidgetFactory().build()
        reset_randomness()
        second = WidgetFactory().build()
        assert (first.ticker, first.name) == (second.ticker, second.name)

    def test_resetting_restarts_the_sequence(self) -> None:
        reset_randomness()
        assert WidgetFactory().build().ticker == "TCK0001"
        assert WidgetFactory().build().ticker == "TCK0002"
        reset_randomness()
        assert WidgetFactory().build().ticker == "TCK0001"

    def test_a_different_seed_produces_different_data(self) -> None:
        reset_randomness(1)
        first = WidgetFactory().build().name
        reset_randomness(2)
        assert WidgetFactory().build().name != first


class TestRegistry:
    def test_a_registered_model_resolves_to_a_fresh_factory(self) -> None:
        resolved = factory_for(Widget)
        assert isinstance(resolved, WidgetFactory)
        assert resolved is not factory_for(Widget)

    def test_an_unregistered_model_says_what_to_do(self) -> None:
        class Unregistered:
            pass

        with pytest.raises(LookupError, match="no factory registered"):
            factory_for(Unregistered)

    def test_a_second_factory_for_one_model_is_rejected(self) -> None:
        """Silent shadowing would make `factory_for` depend on import order."""
        with pytest.raises(ValueError, match="already has a factory"):

            @register
            class RivalWidgetFactory(Factory[Widget]):
                model = Widget

                def defaults(self) -> dict[str, object]:
                    return {"ticker": "X", "name": "y"}

    def test_registering_without_a_model_is_rejected(self) -> None:
        with pytest.raises(TypeError, match="must set `model`"):

            @register
            class ModellessFactory(Factory[Widget]):
                def defaults(self) -> dict[str, object]:
                    return {}


def test_defaults_is_abstract() -> None:
    """A subclass that forgets `defaults()` fails at construction, not at first use."""

    class Incomplete(Factory[Widget]):
        model = Widget

    with pytest.raises(TypeError):
        Incomplete()  # type: ignore[abstract]
