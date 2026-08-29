"""Unit tests for the domain error hierarchy.

Pure: nothing here builds an app, opens a connection or needs a fixture. The status
mapping is asserted against ``app.middleware.errors`` because that is where the contract
lives — ``app/domain/`` is not allowed to know what a 404 is.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from app.domain import errors
from app.domain.errors import (
    AnvexError,
    ConflictError,
    ExternalServiceError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)
from app.middleware.errors import ERROR_STATUS_CODES, status_for

ALL_ERRORS = [
    NotFoundError,
    ConflictError,
    ValidationError,
    UnauthorizedError,
    ForbiddenError,
    ExternalServiceError,
]


class TestPurity:
    """``app/domain/`` must not depend on the web framework (``CLAUDE.md`` §3)."""

    def test_module_imports_only_the_standard_library(self) -> None:
        tree = ast.parse(Path(errors.__file__).read_text(encoding="utf-8"))
        imported_roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                imported_roots.add(node.module.split(".")[0])

        assert imported_roots, "expected at least the __future__ import"
        non_stdlib = imported_roots - sys.stdlib_module_names
        assert non_stdlib == set(), f"domain/errors.py must stay pure, but imports {non_stdlib}"

    @pytest.mark.parametrize("error_class", ALL_ERRORS)
    def test_constructing_an_error_does_not_touch_fastapi(self, error_class: type) -> None:
        """Constructing must work with no framework involved at all."""
        instance = error_class()
        assert isinstance(instance, AnvexError)
        module_names = {type(instance).__module__, error_class.__module__}
        assert module_names == {"app.domain.errors"}


class TestHierarchy:
    @pytest.mark.parametrize("error_class", ALL_ERRORS)
    def test_every_error_derives_from_the_base(self, error_class: type) -> None:
        assert issubclass(error_class, AnvexError)
        assert issubclass(error_class, Exception)

    @pytest.mark.parametrize("error_class", ALL_ERRORS)
    def test_every_error_has_a_distinct_code_and_a_default_message(self, error_class: type) -> None:
        assert error_class.code != AnvexError.code
        assert error_class().message

    def test_codes_are_unique(self) -> None:
        codes = [error_class.code for error_class in ALL_ERRORS]
        assert len(codes) == len(set(codes))

    def test_catching_the_base_catches_every_subclass(self) -> None:
        for error_class in ALL_ERRORS:
            with pytest.raises(AnvexError):
                raise error_class()

    def test_details_default_to_an_empty_dict_and_are_copied(self) -> None:
        supplied = {"field": "ticker"}
        error = AnvexError("boom", details=supplied)
        assert AnvexError("boom").details == {}
        supplied["field"] = "mutated"
        assert error.details == {"field": "ticker"}, "details must not alias the caller's dict"


class TestStatusMapping:
    """The published contract. Changing a value here changes the public API."""

    @pytest.mark.parametrize(
        ("error_class", "expected_status"),
        [
            (AnvexError, 500),
            (ValidationError, 422),
            (UnauthorizedError, 401),
            (ForbiddenError, 403),
            (NotFoundError, 404),
            (ConflictError, 409),
            (ExternalServiceError, 502),
        ],
    )
    def test_status_for(self, error_class: type[AnvexError], expected_status: int) -> None:
        assert status_for(error_class) == expected_status
        assert status_for(error_class()) == expected_status

    def test_every_error_is_mapped(self) -> None:
        assert set(ERROR_STATUS_CODES) == {AnvexError, *ALL_ERRORS}

    def test_an_unmapped_subclass_inherits_its_parent_status(self) -> None:
        class MissingStockError(NotFoundError):
            code = "missing_stock"

        assert status_for(MissingStockError) == 404

    def test_an_unmapped_direct_subclass_falls_back_to_500(self) -> None:
        class WeirdError(AnvexError):
            code = "weird"

        assert status_for(WeirdError()) == 500


class TestConstructors:
    def test_not_found_builds_a_sentence_from_resource_and_identifier(self) -> None:
        error = NotFoundError("stock", "AAPL")
        assert error.message == "stock 'AAPL' was not found."
        assert error.details == {"resource": "stock", "identifier": "AAPL"}
        assert error.code == "not_found"

    def test_not_found_without_an_identifier(self) -> None:
        assert NotFoundError("watchlist").message == "watchlist was not found."

    def test_not_found_with_no_arguments_uses_the_default_message(self) -> None:
        error = NotFoundError()
        assert error.message == NotFoundError.default_message
        assert error.details == {}

    def test_identifier_is_stringified_for_json_safety(self) -> None:
        error = NotFoundError("user", 42)
        assert error.details["identifier"] == "42"

    def test_conflict_reads_as_a_duplicate(self) -> None:
        error = ConflictError("user", "a@b.com")
        assert error.message == "user 'a@b.com' already exists."
        assert error.details == {"resource": "user", "identifier": "a@b.com"}

    def test_an_explicit_message_wins_over_the_generated_one(self) -> None:
        error = ConflictError("user", "a@b.com", message="That username is taken.")
        assert error.message == "That username is taken."
        assert error.details["resource"] == "user"

    def test_validation_error_records_the_field(self) -> None:
        error = ValidationError("End date precedes start date.", field="end_date")
        assert error.details == {"field": "end_date"}
        assert error.field == "end_date"

    def test_validation_error_merges_extra_details(self) -> None:
        error = ValidationError("bad", field="size", details={"max": 100})
        assert error.details == {"field": "size", "max": 100}

    @pytest.mark.parametrize("error_class", [UnauthorizedError, ForbiddenError])
    def test_auth_errors_take_a_message_only(self, error_class: type[AnvexError]) -> None:
        assert error_class("Nope.").message == "Nope."
        assert error_class().message == error_class.default_message

    def test_external_service_error_names_the_service(self) -> None:
        error = ExternalServiceError("alphavantage")
        assert error.message == "The upstream service 'alphavantage' failed."
        assert error.details == {"service": "alphavantage"}
        assert error.service == "alphavantage"

    def test_external_service_error_accepts_an_explicit_message(self) -> None:
        error = ExternalServiceError("newsapi", "Rate limit exhausted.")
        assert error.message == "Rate limit exhausted."
        assert error.details == {"service": "newsapi"}

    def test_str_is_the_message_so_logs_read_sensibly(self) -> None:
        assert str(NotFoundError("stock", "AAPL")) == "stock 'AAPL' was not found."

    def test_repr_shows_the_details(self) -> None:
        assert "resource" in repr(NotFoundError("stock", "AAPL"))
