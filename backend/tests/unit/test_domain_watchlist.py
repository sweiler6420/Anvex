"""Exhaustive unit tests for the watchlist ordinal rules — the heart of ANV-15.

The unit tier (``CLAUDE.md`` §6): no fixtures, no I/O, no database, no clock. Every case
below is arithmetic on plain values, which is the whole reason the reorder rule was pulled
out of a request handler and into ``app/domain/watchlist.py`` — the endpoint it replaces
could only be exercised through HTTP against Postgres, so in practice it never was.

Four things are checked here, in this order:

* **Purity**, parsed out of the source rather than trusted to prose, as
  ``tests/unit/test_domain_auth.py`` and ``tests/unit/test_domain_stock_data.py`` do.
* **The append rule**, including the one input the ``(max_position or -1) + 1`` phrasing
  gets wrong.
* **Every enumerated case of a move** — up, down, no-op, to first, to last, a single-item
  list, an empty list, a stock that is not on the list, a destination out of range, and
  starting positions that are not contiguous, not zero-based, or not even distinct.
* **The invariant, over every move on every list up to seven stocks**: the result is a
  permutation of the same stock ids, positioned exactly ``0..n-1``, with the moved stock at
  the requested index and the others in their original relative order. That is a property,
  not an example, and it is what actually rules out an off-by-one — the enumerated cases
  above are there to make a failure legible when the property fails.
"""

from __future__ import annotations

import ast
import itertools
import uuid
from pathlib import Path

import pytest

from app.domain import watchlist as domain_watchlist
from app.domain.errors import ConflictError, NotFoundError, ValidationError
from app.domain.watchlist import (
    DESTINATION_FIELD,
    ENTRY_RESOURCE,
    ORIGIN,
    RESOURCE,
    canonical_order,
    dense_positions,
    insert,
    next_position,
    normalise,
    remove,
    reposition,
)
from app.middleware.errors import status_for

#: Deterministic, *ordered* ids: ``A < B < C < D`` by value, so a tie-break on ``stock_id``
#: is assertable rather than accidental. Real ids are random; these are not, on purpose.
A, B, C, D, E = (uuid.UUID(int=n) for n in range(1, 6))

#: An id that is never a member of any list built below.
ABSENT = uuid.UUID(int=99)


def source_tree() -> ast.Module:
    return ast.parse(Path(domain_watchlist.__file__).read_text(encoding="utf-8"))


def listing(*stock_ids: uuid.UUID) -> dict[uuid.UUID, int]:
    """A dense, zero-based watchlist holding ``stock_ids`` in that order."""
    return dense_positions(stock_ids)


def order(positions: dict[uuid.UUID, int]) -> tuple[uuid.UUID, ...]:
    """The stocks of a *result* map, in position order — what a reader compares against."""
    return canonical_order(positions)


# ---------------------------------------------------------------------------------------
# purity
# ---------------------------------------------------------------------------------------


class TestPurity:
    """``app/domain/`` is pure by rule, and a convention that lives only in prose gets
    broken — so this is parsed out of the source, exactly as the other domain suites do."""

    def test_it_imports_no_framework_no_orm_and_no_settings(self) -> None:
        modules: set[str] = set()
        for node in ast.walk(source_tree()):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                modules.add(node.module)
        roots = {module.split(".")[0] for module in modules}

        assert "fastapi" not in roots
        assert "starlette" not in roots
        assert "sqlalchemy" not in roots
        assert "app.settings" not in modules
        # Downward-only, and narrower than its neighbours: the error hierarchy is the only
        # Anvex module a watchlist ordinal rule needs.
        assert {module for module in modules if module.startswith("app")} == {"app.domain.errors"}

    def test_it_never_reads_a_clock(self) -> None:
        """Ordinals have no time dimension, and the module says so; this makes that true."""
        clock_calls = {"now", "utcnow", "today", "monotonic", "perf_counter", "time_ns"}
        offenders = [
            node.func.attr
            for node in ast.walk(source_tree())
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in clock_calls
        ]

        assert not offenders, f"domain/watchlist.py must stay clock-free: {offenders}"

    def test_it_writes_no_queries_and_reads_no_configuration(self) -> None:
        source = Path(domain_watchlist.__file__).read_text(encoding="utf-8")

        assert "select(" not in source
        assert "get_settings" not in source


# ---------------------------------------------------------------------------------------
# normalisation
# ---------------------------------------------------------------------------------------


class TestCanonicalOrder:
    def test_it_orders_by_position(self) -> None:
        assert canonical_order({C: 2, A: 0, B: 1}) == (A, B, C)

    def test_an_empty_watchlist_is_an_empty_order(self) -> None:
        assert canonical_order({}) == ()

    def test_it_reads_only_the_relative_order_not_the_values(self) -> None:
        """Positions 10/20/30 describe the same list as 0/1/2."""
        assert canonical_order({A: 10, B: 20, C: 30}) == canonical_order(listing(A, B, C))

    def test_a_tie_is_broken_by_stock_id(self) -> None:
        """``position`` carries no unique constraint (ANV-7), so ties are legal — and the
        repo's ``ORDER BY position, stock_id`` resolves them by id, so this must too."""
        assert canonical_order({C: 1, A: 1, B: 0}) == (B, A, C)

    def test_it_does_not_depend_on_dict_insertion_order(self) -> None:
        forwards = canonical_order({A: 1, B: 1})
        backwards = canonical_order({B: 1, A: 1})

        assert forwards == backwards == (A, B)


class TestNormalise:
    def test_a_canonical_watchlist_is_unchanged(self) -> None:
        already = listing(A, B, C)

        assert normalise(already) == already

    def test_it_closes_gaps(self) -> None:
        assert normalise({A: 0, B: 3, C: 7}) == listing(A, B, C)

    def test_it_pulls_a_non_zero_based_run_down_to_the_origin(self) -> None:
        assert normalise({A: 5, B: 6, C: 7}) == listing(A, B, C)

    def test_it_breaks_ties_apart(self) -> None:
        assert normalise({A: 0, B: 0, C: 0}) == listing(A, B, C)

    def test_an_empty_watchlist_normalises_to_an_empty_map(self) -> None:
        assert normalise({}) == {}


# ---------------------------------------------------------------------------------------
# the append rule
# ---------------------------------------------------------------------------------------


class TestNextPosition:
    def test_an_empty_watchlist_appends_at_the_origin(self) -> None:
        """``max_position`` answers ``None``, not ``-1`` — that translation lives here."""
        assert next_position(None) == ORIGIN

    def test_a_single_stock_at_zero_appends_at_one(self) -> None:
        """The case ``(max_position or -1) + 1`` gets wrong.

        ``0`` is falsy, so that expression yields ``(-1) + 1 == 0`` and the second stock
        lands on top of the first — a tie the schema permits, the ordering cannot resolve,
        and no test of the empty case would ever notice. ``CLAUDE.md`` and
        ``app/repos/watchlist.py`` both describe the rule in that phrasing; the module
        implements the ``is None`` form instead, and this is why.
        """
        assert next_position(0) == 1

    @pytest.mark.parametrize("highest", [1, 2, 7, 41, 1_000])
    def test_it_is_one_past_the_highest_ordinal_in_use(self, highest: int) -> None:
        assert next_position(highest) == highest + 1


# ---------------------------------------------------------------------------------------
# reposition — the enumerated cases
# ---------------------------------------------------------------------------------------


class TestRepositionMoves:
    """The moves a user can make, spelled out one per test so a failure names itself."""

    def test_moving_the_last_stock_to_the_second_slot(self) -> None:
        """The ticket's own worked example: 3 -> 1 in ``[A,B,C,D]`` yields ``A,D,B,C``."""
        moved = reposition(listing(A, B, C, D), stock_id=D, destination=1)

        assert order(moved) == (A, D, B, C)
        assert moved == {A: 0, D: 1, B: 2, C: 3}

    def test_moving_the_second_stock_to_the_last_slot(self) -> None:
        """The mirror example: 1 -> 3 in ``[A,B,C,D]`` yields ``A,C,D,B``."""
        moved = reposition(listing(A, B, C, D), stock_id=B, destination=3)

        assert order(moved) == (A, C, D, B)

    def test_moving_up_by_one(self) -> None:
        assert order(reposition(listing(A, B, C), stock_id=C, destination=1)) == (A, C, B)

    def test_moving_down_by_one(self) -> None:
        assert order(reposition(listing(A, B, C), stock_id=A, destination=1)) == (B, A, C)

    def test_moving_to_first(self) -> None:
        assert order(reposition(listing(A, B, C, D), stock_id=D, destination=0)) == (
            D,
            A,
            B,
            C,
        )

    def test_moving_to_last(self) -> None:
        assert order(reposition(listing(A, B, C, D), stock_id=A, destination=3)) == (
            B,
            C,
            D,
            A,
        )

    def test_moving_a_stock_to_where_it_already_is_changes_nothing(self) -> None:
        """A drag that ends where it started. The result is the *input*, not merely a list
        in the same order — the service hands it to ``set_positions``, which then reports
        zero rows changed rather than rewriting every row for nothing."""
        before = listing(A, B, C, D)

        assert reposition(before, stock_id=C, destination=2) == before

    def test_a_single_item_watchlist_can_only_move_to_zero(self) -> None:
        assert reposition(listing(A), stock_id=A, destination=0) == {A: 0}

    def test_the_result_can_be_fed_straight_back_in(self) -> None:
        """Input and output are the same shape, so moves compose."""
        once = reposition(listing(A, B, C, D), stock_id=D, destination=0)
        twice = reposition(once, stock_id=A, destination=0)

        assert order(twice) == (A, D, B, C)

    def test_it_does_not_mutate_the_mapping_it_was_given(self) -> None:
        before = listing(A, B, C)
        snapshot = dict(before)

        reposition(before, stock_id=A, destination=2)

        assert before == snapshot


class TestRepositionSurvivesUntidyPositions:
    """The assumption the old handler made and nothing enforces.

    It subscripted a *position-ordered list* by index, which is only the same thing while
    the ordinals happen to be exactly ``0..n-1``. ANV-7 deliberately left ``position``
    non-unique so a mid-swap state can be flushed in one statement, and nothing renumbers
    after a delete unless a service remembers to — so untidy ordinals are a state the
    database can genuinely be in, and every one of them must still move the right stock and
    come back dense.
    """

    def test_positions_with_gaps(self) -> None:
        moved = reposition({A: 0, B: 5, C: 9}, stock_id=C, destination=0)

        assert moved == {C: 0, A: 1, B: 2}

    def test_positions_that_do_not_start_at_zero(self) -> None:
        moved = reposition({A: 3, B: 4, C: 5}, stock_id=A, destination=2)

        assert moved == {B: 0, C: 1, A: 2}

    def test_positions_that_are_all_identical(self) -> None:
        """Legal, and resolved by the ``stock_id`` tie-break the repo's ordering uses."""
        moved = reposition({A: 0, B: 0, C: 0}, stock_id=A, destination=2)

        assert moved == {B: 0, C: 1, A: 2}

    def test_negative_positions(self) -> None:
        """Nothing in the schema forbids one; only the relative order is read."""
        moved = reposition({A: -10, B: -5, C: 0}, stock_id=B, destination=2)

        assert moved == {A: 0, C: 1, B: 2}

    def test_the_destination_range_is_indices_not_stored_positions(self) -> None:
        """``{A: 10, B: 20}`` is a two-stock list, so ``destination=10`` is out of range —
        the client speaks in indices, and the stored ordinals are the server's business."""
        with pytest.raises(ValidationError):
            reposition({A: 10, B: 20}, stock_id=A, destination=10)


class TestRepositionRefusals:
    def test_a_stock_that_is_not_on_the_watchlist_is_not_found(self) -> None:
        with pytest.raises(NotFoundError) as caught:
            reposition(listing(A, B, C), stock_id=ABSENT, destination=0)

        assert caught.value.details["resource"] == ENTRY_RESOURCE
        assert caught.value.details["identifier"] == str(ABSENT)
        assert status_for(caught.value) == 404

    def test_an_empty_watchlist_holds_no_stock_to_move(self) -> None:
        """The missing stock is reported before the destination, because it is the more
        specific fact — on an empty list *every* destination is out of range too."""
        with pytest.raises(NotFoundError):
            reposition({}, stock_id=A, destination=0)

    @pytest.mark.parametrize("destination", [3, 4, 100])
    def test_a_destination_past_the_end_is_refused(self, destination: int) -> None:
        with pytest.raises(ValidationError) as caught:
            reposition(listing(A, B, C), stock_id=A, destination=destination)

        assert caught.value.details["field"] == DESTINATION_FIELD
        assert caught.value.details["position"] == destination
        assert caught.value.details["max"] == 2
        assert status_for(caught.value) == 422

    @pytest.mark.parametrize("destination", [-1, -3, -100])
    def test_a_negative_destination_is_refused_rather_than_wrapping(self, destination: int) -> None:
        """The second half of the old handler's unvalidated-index bug, and the quieter one.

        Python subscripts from the end, so the old code answered ``destination=-1`` by
        moving the stock to the *back* of the list and reporting success. Refusing is the
        only answer that does not invent an intention the caller never expressed.
        """
        with pytest.raises(ValidationError):
            reposition(listing(A, B, C), stock_id=A, destination=destination)

    def test_the_index_that_is_valid_to_insert_at_is_not_valid_to_move_to(self) -> None:
        """``destination == n`` appends on an :func:`insert` and is out of range on a move:
        there is no slot after the last stock for a stock that is already in the list."""
        with pytest.raises(ValidationError):
            reposition(listing(A, B, C), stock_id=A, destination=3)

        assert order(insert(listing(A, B, C), stock_id=D, destination=3)) == (A, B, C, D)


class TestRepositionInvariant:
    """The property, over every move on every list up to seven stocks.

    Examples show a rule is right *here*; a property shows it is right everywhere the rule
    is defined. Together they are what replaces "traced by hand" as the evidence that the
    arithmetic has no off-by-one.
    """

    STOCKS = (A, B, C, D, E, uuid.UUID(int=6), uuid.UUID(int=7))

    @staticmethod
    def _cases() -> list[tuple[int, int, int]]:
        return [
            (size, origin, destination)
            for size in range(1, 8)
            for origin in range(size)
            for destination in range(size)
        ]

    @pytest.mark.parametrize(("size", "origin", "destination"), _cases())
    def test_every_valid_move_holds_the_invariant(
        self, size: int, origin: int, destination: int
    ) -> None:
        stocks = self.STOCKS[:size]
        before = listing(*stocks)
        subject = stocks[origin]

        after = reposition(before, stock_id=subject, destination=destination)

        # 1. the same stocks, none added, none lost
        assert set(after) == set(before)
        # 2. positions exactly 0..n-1 — dense, zero-based, no ties
        assert sorted(after.values()) == list(range(size))
        # 3. the moved stock is where it was asked to go
        assert after[subject] == destination
        # 4. everything else keeps its relative order
        expected = [stock for stock in stocks if stock != subject]
        assert [stock for stock in order(after) if stock != subject] == expected

    @pytest.mark.parametrize(("size", "origin", "destination"), _cases())
    def test_every_move_is_reversible(self, size: int, origin: int, destination: int) -> None:
        """Moving a stock away and back restores the original list exactly — which would
        not hold if either direction shifted one row too many or too few."""
        stocks = self.STOCKS[:size]
        before = listing(*stocks)
        subject = stocks[origin]

        there = reposition(before, stock_id=subject, destination=destination)
        back = reposition(there, stock_id=subject, destination=origin)

        assert back == before


# ---------------------------------------------------------------------------------------
# insert
# ---------------------------------------------------------------------------------------


class TestInsert:
    def test_omitting_the_destination_appends(self) -> None:
        """A change from the old endpoint, which unconditionally *prepended* and pushed the
        user's arrangement down by one every time they watched anything new."""
        assert order(insert(listing(A, B, C), stock_id=D)) == (A, B, C, D)

    def test_inserting_into_an_empty_watchlist(self) -> None:
        assert insert({}, stock_id=A) == {A: 0}

    def test_inserting_at_the_front(self) -> None:
        assert order(insert(listing(A, B, C), stock_id=D, destination=0)) == (D, A, B, C)

    def test_inserting_in_the_middle_shifts_everything_after_it_down(self) -> None:
        assert insert(listing(A, B, C), stock_id=D, destination=1) == {
            A: 0,
            D: 1,
            B: 2,
            C: 3,
        }

    def test_the_valid_range_is_one_wider_than_a_moves(self) -> None:
        """``0..n`` inclusive: there *is* a slot after the last stock to insert into."""
        assert order(insert(listing(A, B), stock_id=C, destination=2)) == (A, B, C)

        with pytest.raises(ValidationError):
            insert(listing(A, B), stock_id=C, destination=3)

    def test_a_negative_destination_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            insert(listing(A, B), stock_id=C, destination=-1)

    def test_a_stock_already_on_the_watchlist_is_a_conflict(self) -> None:
        with pytest.raises(ConflictError) as caught:
            insert(listing(A, B), stock_id=A)

        assert caught.value.details["resource"] == ENTRY_RESOURCE
        assert status_for(caught.value) == 409

    def test_the_duplicate_is_reported_before_the_destination(self) -> None:
        """An out-of-range position on a stock that is already there is still a conflict —
        the caller's real problem is the duplicate, and reporting the position instead would
        send them to fix the wrong thing."""
        with pytest.raises(ConflictError):
            insert(listing(A, B), stock_id=A, destination=99)

    def test_it_renumbers_untidy_positions_on_the_way_through(self) -> None:
        assert insert({A: 4, B: 9}, stock_id=C, destination=1) == {A: 0, C: 1, B: 2}


# ---------------------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------------------


class TestRemove:
    def test_it_closes_the_gap_it_leaves(self) -> None:
        """Without the renumbering a five-stock list keeps ``0,1,3,4`` and every later
        insert-at-index reasons about a list whose ordinals no longer match its indices."""
        assert remove(listing(A, B, C, D), stock_id=C) == {A: 0, B: 1, D: 2}

    def test_removing_the_first_stock(self) -> None:
        assert remove(listing(A, B, C), stock_id=A) == {B: 0, C: 1}

    def test_removing_the_last_stock(self) -> None:
        assert remove(listing(A, B, C), stock_id=C) == {A: 0, B: 1}

    def test_removing_the_only_stock_empties_the_watchlist(self) -> None:
        assert remove(listing(A), stock_id=A) == {}

    def test_a_stock_that_is_not_on_the_watchlist_is_not_found(self) -> None:
        with pytest.raises(NotFoundError) as caught:
            remove(listing(A, B), stock_id=ABSENT)

        assert caught.value.details["resource"] == ENTRY_RESOURCE

    def test_removing_from_an_empty_watchlist_is_not_found(self) -> None:
        with pytest.raises(NotFoundError):
            remove({}, stock_id=A)

    def test_it_normalises_what_is_left(self) -> None:
        assert remove({A: 3, B: 8, C: 9}, stock_id=B) == {A: 0, C: 1}

    @pytest.mark.parametrize("size", range(1, 6))
    def test_removing_every_stock_in_turn_holds_the_invariant(self, size: int) -> None:
        stocks = (A, B, C, D, E)[:size]
        before = listing(*stocks)

        for dropped in stocks:
            after = remove(before, stock_id=dropped)

            assert set(after) == set(before) - {dropped}
            assert sorted(after.values()) == list(range(size - 1))
            assert order(after) == tuple(s for s in stocks if s != dropped)


# ---------------------------------------------------------------------------------------
# the module's own vocabulary
# ---------------------------------------------------------------------------------------


class TestResourceNames:
    def test_the_two_nouns_are_distinct(self) -> None:
        """ "No such watchlist" and "that stock is not on it" are different facts, and a
        client that has already been told the list exists loses nothing by being told
        which."""
        assert RESOURCE != ENTRY_RESOURCE

    def test_the_watchlist_noun_matches_the_service(self) -> None:
        from app.services.watchlist import RESOURCE as service_resource

        assert service_resource is RESOURCE

    def test_the_destination_field_is_the_name_the_schemas_use(self) -> None:
        """``details["field"]`` has to name a field the client actually rendered."""
        from app.schemas.watchlist import WatchlistEntryCreate, WatchlistEntryUpdate

        assert DESTINATION_FIELD in WatchlistEntryUpdate.model_fields
        assert DESTINATION_FIELD in WatchlistEntryCreate.model_fields

    def test_every_public_name_is_exported(self) -> None:
        exported = set(domain_watchlist.__all__)
        public = {
            name
            for name in vars(domain_watchlist)
            if not name.startswith("_")
            and getattr(vars(domain_watchlist)[name], "__module__", None)
            == domain_watchlist.__name__
        }

        assert public <= exported


# ---------------------------------------------------------------------------------------
# a worked scenario, end to end through the pure rules alone
# ---------------------------------------------------------------------------------------


class TestAWholeSessionOfEdits:
    def test_building_and_rearranging_a_watchlist(self) -> None:
        """Append four, drag one to the front, drop one, insert one in the middle.

        Every step goes through the same ``{stock_id: position}`` shape, and the invariant
        holds at every one of them — which is what lets the service apply each result with a
        single ``set_positions`` and never reason about ordinals itself.
        """
        positions: dict[uuid.UUID, int] = {}
        for stock in (A, B, C, D):
            positions = insert(positions, stock_id=stock)
        assert order(positions) == (A, B, C, D)

        positions = reposition(positions, stock_id=D, destination=0)
        assert order(positions) == (D, A, B, C)

        positions = remove(positions, stock_id=B)
        assert order(positions) == (D, A, C)

        positions = insert(positions, stock_id=E, destination=1)
        assert positions == {D: 0, E: 1, A: 2, C: 3}
        assert sorted(positions.values()) == list(range(len(positions)))


def test_every_operation_is_a_permutation_of_dense_positions() -> None:
    """One sweep over all three mutations at once, on every ordering of four stocks.

    Guards the module's single invariant — "afterwards the positions are exactly
    ``0..n-1``" — against a future function that forgets to end in ``dense_positions``.
    """
    for arrangement in itertools.permutations((A, B, C, D)):
        before = listing(*arrangement)

        results = [
            reposition(before, stock_id=arrangement[2], destination=0),
            insert(before, stock_id=E, destination=2),
            remove(before, stock_id=arrangement[1]),
            normalise(before),
        ]

        for after in results:
            assert sorted(after.values()) == list(range(len(after)))
            assert len(set(after)) == len(after)
