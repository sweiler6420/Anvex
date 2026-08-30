"""Unit tests for ``app/domain/password.py`` — the password strength policy (ANV-43).

The policy is pure, so ``CLAUDE.md`` §3's "test it exhaustively" applies literally: every
rule has a test for the thing that satisfies it, the thing that does not, and the boundary
in between, and the Unicode cases are tested by name because they are the entire reason the
rules are written with ``unicodedata`` instead of ``[A-Z]``/``[0-9]``.

Four properties are being pinned, and they are different properties:

1. **Each rule decides exactly what ANV-30's regex decides.** ``\\p{Lu}`` is not
   :meth:`str.isupper` and ``\\p{Nd}`` is not :meth:`str.isdigit`; the tests that matter
   most here are the ones where the convenient Python method gives the *wrong* answer
   (``Ⅰ``, ``²``, ``ǅ``), because those are where a plausible implementation drifts.
2. **A failure names every rule it broke, in policy order.** Not the first one, and not a
   boolean — the client renders per-rule messages, and a server that answers "invalid"
   forces it back to one opaque banner.
3. **The server and the client agree.** :class:`TestClientAgreement` reads
   ``frontend/src/features/auth/components/SignUpPage.jsx`` and compares the two rule sets,
   in the spirit of ANV-28's theme-script matrix: the point of a drift test is that the
   *other* copy is read rather than remembered.
4. **The module stays pure.** No I/O, no clock, no settings — it is domain.

What is *not* here: that the API returns a 422 naming the rules (``tests/api/test_users.py``)
and that ``UserService`` raises before it touches the database
(``tests/unit/test_services_user.py``).
"""

from __future__ import annotations

import ast
import re
import unicodedata
from pathlib import Path
from typing import Final

import pytest

from app.domain import password as domain_password
from app.domain.password import (
    FAILED_RULES_DETAIL,
    PASSWORD_RULE_IDS,
    PASSWORD_RULES,
    PasswordRule,
    describe_failures,
    failed_rules,
)
from app.schemas.user import PASSWORD_MIN_LENGTH
from app.settings import REPO_ROOT

#: Satisfies all four rules, so a test can break exactly one of them and attribute the
#: failure. 10 characters, an uppercase ``P``, a digit and a symbol.
GOOD_PASSWORD: Final[str] = "Password1!"

#: The password the ticket was written about: seven lowercase letters, which is everything
#: the API used to require.
WEAK_PASSWORD: Final[str] = "aaaaaaa"


def rule(rule_id: str) -> PasswordRule:
    """The one rule with this id. Raises if it has been renamed, which is the point."""
    return next(candidate for candidate in PASSWORD_RULES if candidate.id == rule_id)


def failed_ids(password: str) -> tuple[str, ...]:
    return tuple(failed.id for failed in failed_rules(password))


# ---------------------------------------------------------------------------------------
# The rule set itself
# ---------------------------------------------------------------------------------------


class TestTheRuleSet:
    def test_there_are_exactly_the_four_rules_the_sign_up_page_lists(self) -> None:
        assert PASSWORD_RULE_IDS == ("length", "uppercase", "number", "symbol")

    def test_the_ids_tuple_is_derived_from_the_rules_rather_than_retyped(self) -> None:
        assert tuple(each.id for each in PASSWORD_RULES) == PASSWORD_RULE_IDS

    def test_every_rule_can_be_displayed_and_named_in_a_sentence(self) -> None:
        """A rule with no phrasing is a rule that can only ever produce "invalid"."""
        for each in PASSWORD_RULES:
            assert each.label, each.id
            assert each.missing, each.id

    def test_the_ids_are_unique(self) -> None:
        assert len(set(PASSWORD_RULE_IDS)) == len(PASSWORD_RULE_IDS)

    def test_a_rule_is_frozen(self) -> None:
        """The policy is a constant; a caller must not be able to edit it in place."""
        with pytest.raises(AttributeError):
            PASSWORD_RULES[0].id = "something-else"  # type: ignore[misc]

    def test_the_details_key_is_spelled_once(self) -> None:
        assert FAILED_RULES_DETAIL == "failed_rules"


# ---------------------------------------------------------------------------------------
# Rule 1 — length
# ---------------------------------------------------------------------------------------


class TestLengthRule:
    def test_the_floor_is_the_schemas_floor_rather_than_a_second_number(self) -> None:
        assert rule("length").label == f"At least {PASSWORD_MIN_LENGTH} characters"

    @pytest.mark.parametrize("length", range(PASSWORD_MIN_LENGTH))
    def test_anything_shorter_than_the_floor_fails(self, length: int) -> None:
        assert not rule("length").met("a" * length)

    def test_exactly_the_floor_passes(self) -> None:
        assert rule("length").met("a" * PASSWORD_MIN_LENGTH)

    def test_longer_than_the_floor_passes(self) -> None:
        assert rule("length").met("a" * 200)

    def test_an_empty_password_fails_it(self) -> None:
        assert not rule("length").met("")

    def test_the_boundary_through_the_whole_policy(self) -> None:
        """One character short of the floor, with every other rule satisfied."""
        short = "Ab1!xy"
        assert len(short) == PASSWORD_MIN_LENGTH - 1
        assert failed_ids(short) == ("length",)
        assert failed_ids(f"{short}z") == ()

    def test_there_is_no_ceiling_in_the_policy(self) -> None:
        """bcrypt's 72-*byte* limit is the hashing primitive's, not a strength rule."""
        assert failed_ids(GOOD_PASSWORD * 50) == ()


# ---------------------------------------------------------------------------------------
# Rule 2 — uppercase, `\p{Lu}`
# ---------------------------------------------------------------------------------------


class TestUppercaseRule:
    @pytest.mark.parametrize("password", ["password1!", "1234567!", "-------", ""])
    def test_a_password_with_no_capital_fails(self, password: str) -> None:
        assert not rule("uppercase").met(password)

    @pytest.mark.parametrize("password", ["Password1!", "PASSWORD", "aaaaaaZ"])
    def test_an_ascii_capital_passes(self, password: str) -> None:
        assert rule("uppercase").met(password)

    @pytest.mark.parametrize("character", ["Ä", "É", "Ñ", "Ç", "Ω", "Д", "Ǆ"])
    def test_a_non_ascii_capital_counts(self, character: str) -> None:
        """``validator``'s ``/^[A-Z]$/`` told these users they had typed no capital."""
        assert unicodedata.category(character) == "Lu"
        assert rule("uppercase").met(f"{character}ndern1!")

    @pytest.mark.parametrize("character", ["ä", "é", "ñ", "ω", "д"])
    def test_a_non_ascii_lowercase_letter_does_not(self, character: str) -> None:
        assert not rule("uppercase").met(f"{character}ndern1!")

    def test_a_titlecase_letter_is_not_an_uppercase_letter(self) -> None:
        """``ǅ`` is ``Lt``. ``\\p{Lu}`` excludes it, so this rule must too."""
        assert unicodedata.category("ǅ") == "Lt"
        assert not rule("uppercase").met("ǅabcde1!")

    def test_a_roman_numeral_is_not_an_uppercase_letter(self) -> None:
        """The case that rules out :meth:`str.isupper`, which answers ``True`` here.

        ``Ⅰ`` U+2160 is ``Nl`` with the Other_Uppercase property, so a policy written on
        ``str.isupper()`` would accept a password JavaScript's ``\\p{Lu}`` refuses — and the
        two sides would disagree with nothing on screen to explain it.
        """
        assert "Ⅰ".isupper()
        assert unicodedata.category("Ⅰ") == "Nl"
        assert not rule("uppercase").met("Ⅰabcde1!")

    def test_an_astral_capital_counts(self) -> None:
        """U+1D400 is ``Lu`` and lives outside the BMP; JavaScript's ``u`` flag sees it."""
        assert unicodedata.category("𝐀") == "Lu"
        assert rule("uppercase").met("𝐀bcdef1!")


# ---------------------------------------------------------------------------------------
# Rule 3 — number, `\p{Nd}`
# ---------------------------------------------------------------------------------------


class TestNumberRule:
    @pytest.mark.parametrize("password", ["Password!", "ABCDEFG", "!!!!!!!", ""])
    def test_a_password_with_no_digit_fails(self, password: str) -> None:
        assert not rule("number").met(password)

    @pytest.mark.parametrize("digit", "0123456789")
    def test_every_ascii_digit_counts(self, digit: str) -> None:
        assert rule("number").met(f"Passwor{digit}")

    @pytest.mark.parametrize("character", ["٣", "۵", "٩", "०", "๓"])
    def test_a_decimal_digit_in_another_script_counts(self, character: str) -> None:
        assert unicodedata.category(character) == "Nd"
        assert rule("number").met(f"Password{character}")

    def test_a_superscript_digit_does_not(self) -> None:
        """``²`` is ``No``, and ``\\p{Nd}`` is decimal digits only.

        Also the case that rules out :meth:`str.isdigit`, which answers ``True`` here.
        """
        assert "²".isdigit()
        assert unicodedata.category("²") == "No"
        assert not rule("number").met("Password²")

    def test_a_roman_numeral_does_not(self) -> None:
        """``Ⅴ`` is ``Nl``. :meth:`str.isnumeric` would take it; ``\\p{Nd}`` does not."""
        assert unicodedata.category("Ⅴ") == "Nl"
        assert not rule("number").met("PasswordⅤ")

    def test_an_astral_digit_counts(self) -> None:
        assert unicodedata.category("𝟎") == "Nd"
        assert rule("number").met("Password𝟎")


# ---------------------------------------------------------------------------------------
# Rule 4 — symbol, "neither a letter nor a number"
# ---------------------------------------------------------------------------------------


class TestSymbolRule:
    @pytest.mark.parametrize("password", ["Password1", "abcdefg", "1234567", ""])
    def test_letters_and_digits_alone_fail(self, password: str) -> None:
        assert not rule("symbol").met(password)

    @pytest.mark.parametrize("character", ["!", "-", "_", ".", "@", "#", "$"])
    def test_ascii_punctuation_counts(self, character: str) -> None:
        assert rule("symbol").met(f"Password1{character}")

    @pytest.mark.parametrize("character", ["€", "§", "£", "±", "«", "。", "😀"])
    def test_a_non_ascii_symbol_counts(self, character: str) -> None:
        """``validator``'s fixed punctuation set refused every one of these."""
        assert rule("symbol").met(f"Password1{character}")

    def test_a_space_counts(self) -> None:
        """``Zs`` is neither a letter nor a number, so a passphrase qualifies on its own."""
        assert rule("symbol").met("Correct horse1")

    def test_a_non_ascii_letter_is_not_a_symbol(self) -> None:
        """The rule is "neither a letter nor a number" — in every script, not just Latin."""
        assert not rule("symbol").met("Ändern12")
        assert not rule("symbol").met("Пароль12")
        assert not rule("symbol").met("漢字1234A")

    def test_a_decimal_digit_in_another_script_is_not_a_symbol(self) -> None:
        assert not rule("symbol").met("Password٣")

    def test_a_non_decimal_number_is_not_a_symbol_either(self) -> None:
        """``[^\\p{L}\\p{N}]`` excludes the *whole* of ``N``, not only ``Nd``.

        So ``Ⅴ`` satisfies neither the number rule nor the symbol rule. That asymmetry is
        ANV-30's, deliberately mirrored: it is the conservative reading of both.
        """
        assert not rule("symbol").met("PasswordⅤ")
        assert not rule("number").met("PasswordⅤ")

    def test_an_unassigned_code_point_counts_as_a_symbol(self) -> None:
        """``Cn`` is neither ``L`` nor ``N``, which is also what JavaScript answers."""
        unassigned = "͸"
        assert unicodedata.category(unassigned) == "Cn"
        assert rule("symbol").met(f"Password1{unassigned}")


# ---------------------------------------------------------------------------------------
# failed_rules
# ---------------------------------------------------------------------------------------


class TestFailedRules:
    def test_a_good_password_fails_nothing(self) -> None:
        assert failed_rules(GOOD_PASSWORD) == ()

    def test_the_password_this_ticket_exists_for(self) -> None:
        """``aaaaaaa`` — long enough for the old API and nothing else."""
        assert failed_ids(WEAK_PASSWORD) == ("uppercase", "number", "symbol")

    def test_an_empty_password_fails_all_four(self) -> None:
        assert failed_ids("") == PASSWORD_RULE_IDS

    def test_the_order_is_the_policy_order_not_the_discovery_order(self) -> None:
        """So a message reads the same way every time, and a client can render in order."""
        assert failed_ids("a") == ("length", "uppercase", "number", "symbol")

    def test_it_reports_every_broken_rule_rather_than_the_first(self) -> None:
        assert len(failed_rules("aaaaaaa")) == 3

    @pytest.mark.parametrize(
        ("password", "expected"),
        [
            ("Password1!", ()),
            ("password1!", ("uppercase",)),
            ("Password!!", ("number",)),
            ("Password11", ("symbol",)),
            ("Pa1!", ("length",)),
            ("password11", ("uppercase", "symbol")),
            ("passworda", ("uppercase", "number", "symbol")),
            ("Ädern1!", ()),
            ("ÄNDERN1€", ()),
            ("Passwor٣!", ()),
            ("𝐀bcdef1!", ()),
        ],
    )
    def test_the_policy_end_to_end(self, password: str, expected: tuple[str, ...]) -> None:
        assert failed_ids(password) == expected

    def test_it_returns_rules_rather_than_ids(self) -> None:
        """The caller needs the phrasing too; ids alone would put it in two places."""
        assert all(isinstance(each, PasswordRule) for each in failed_rules(""))

    def test_it_is_pure(self) -> None:
        """Twice in a row, and the policy is unchanged afterwards."""
        before = PASSWORD_RULES
        assert failed_ids(WEAK_PASSWORD) == failed_ids(WEAK_PASSWORD)
        assert PASSWORD_RULES is before


# ---------------------------------------------------------------------------------------
# describe_failures
# ---------------------------------------------------------------------------------------


class TestDescribeFailures:
    def test_one_rule(self) -> None:
        assert describe_failures(failed_rules("password1!")) == (
            "Password needs an uppercase letter."
        )

    def test_two_rules_are_joined_with_and(self) -> None:
        assert describe_failures(failed_rules("password11")) == (
            "Password needs an uppercase letter and a symbol."
        )

    def test_three_rules_take_commas_and_a_final_and(self) -> None:
        assert describe_failures(failed_rules(WEAK_PASSWORD)) == (
            "Password needs an uppercase letter, a number and a symbol."
        )

    def test_all_four(self) -> None:
        assert describe_failures(failed_rules("")) == (
            f"Password needs {PASSWORD_MIN_LENGTH} characters, an uppercase letter, "
            "a number and a symbol."
        )

    def test_nothing_to_describe_is_a_caller_bug_not_a_reassuring_sentence(self) -> None:
        with pytest.raises(ValueError, match="at least one failed rule"):
            describe_failures(())

    def test_it_never_contains_the_password(self) -> None:
        """The sentence is about the rules; the submitted value is not part of it."""
        assert "hunter" not in describe_failures(failed_rules("hunter"))


# ---------------------------------------------------------------------------------------
# Agreement with ANV-30's client rules
# ---------------------------------------------------------------------------------------

SIGN_UP_PAGE: Final[Path] = (
    REPO_ROOT / "frontend" / "src" / "features" / "auth" / "components" / "SignUpPage.jsx"
)

#: ANV-30's predicate sources, as this module was written to mirror them. Python cannot
#: execute JavaScript, so this half of the drift test is a **tripwire** rather than a proof:
#: it fails when the client's definition of a rule changes, which is precisely the moment
#: somebody has to come and check that ``app/domain/password.py`` still says the same thing.
#: The ids, labels, phrasings and the minimum length below are compared for real.
MIRRORED_CLIENT_PREDICATES: Final[dict[str, str]] = {
    "length": "(password) => password.length >= PASSWORD_MIN_LENGTH",
    "uppercase": "(password) => /\\p{Lu}/u.test(password)",
    "number": "(password) => /\\p{Nd}/u.test(password)",
    "symbol": "(password) => /[^\\p{L}\\p{N}]/u.test(password)",
}

_RULE_BLOCK = re.compile(r"const PASSWORD_RULES = Object\.freeze\(\[(.*?)\n\]\)", re.DOTALL)
_ENTRY = re.compile(r"\{(.*?)\n  \}", re.DOTALL)
_MIN_LENGTH = re.compile(r"^const PASSWORD_MIN_LENGTH = (\d+)$", re.MULTILINE)


def _field(entry: str, key: str) -> str:
    """``label: `At least ${PASSWORD_MIN_LENGTH} characters`,`` → the interpolated value."""
    match = re.search(rf"{key}: (['`])(.*?)\1,", entry)
    assert match is not None, f"{key} missing from a client rule: {entry!r}"
    return match.group(2).replace("${PASSWORD_MIN_LENGTH}", str(_client_min_length()))


def _client_source() -> str:
    assert SIGN_UP_PAGE.is_file(), (
        f"{SIGN_UP_PAGE} is missing. The backend suite reads ANV-30's rules from the "
        "frontend source on purpose — a drift test that skips when the other copy is out "
        "of reach proves nothing."
    )
    return SIGN_UP_PAGE.read_text(encoding="utf-8")


def _client_min_length() -> int:
    match = _MIN_LENGTH.search(_client_source())
    assert match is not None, "the client no longer declares PASSWORD_MIN_LENGTH"
    return int(match.group(1))


def _client_rules() -> list[dict[str, str]]:
    """ANV-30's ``PASSWORD_RULES`` array, parsed out of the page it is written on."""
    block = _RULE_BLOCK.search(_client_source())
    assert block is not None, "the client no longer declares a frozen PASSWORD_RULES array"
    return [
        {
            "id": _field(entry, "id"),
            "label": _field(entry, "label"),
            "missing": _field(entry, "missing"),
            "met": re.sub(r",$", "", entry.strip().splitlines()[-1].strip()).removeprefix("met: "),
        }
        for entry in _ENTRY.findall(block.group(1))
    ]


class TestClientAgreement:
    """The drift test, in the spirit of ANV-28's theme-script matrix.

    ANV-30's four rules were the *only* place the policy existed, so ANV-43 did not add a
    second opinion — it made the existing one binding. That is only true while the two sides
    say the same thing, and the way to keep it true is to read the other copy rather than to
    remember it: this class parses
    ``frontend/src/features/auth/components/SignUpPage.jsx`` itself.

    It deliberately **fails** rather than skips when that file is absent. A drift test that
    skips is a drift test that passes in CI while the two halves diverge.
    """

    def test_the_client_rules_can_be_read_at_all(self) -> None:
        """If this fails, the parser is stale — fix it, do not delete the class."""
        assert len(_client_rules()) == 4

    def test_neither_side_has_a_rule_the_other_does_not(self) -> None:
        assert tuple(each["id"] for each in _client_rules()) == PASSWORD_RULE_IDS

    def test_the_rules_are_in_the_same_order(self) -> None:
        """The order the form lists them in is the order a failure message names them."""
        assert [each["id"] for each in _client_rules()] == [each.id for each in PASSWORD_RULES]

    def test_every_rule_is_labelled_identically(self) -> None:
        assert [each["label"] for each in _client_rules()] == [
            each.label for each in PASSWORD_RULES
        ]

    def test_every_rule_is_named_identically_inside_a_sentence(self) -> None:
        """So the API's refusal reads as the sentence the form would have produced."""
        assert [each["missing"] for each in _client_rules()] == [
            each.missing for each in PASSWORD_RULES
        ]

    def test_the_minimum_length_is_one_number_on_both_sides(self) -> None:
        assert _client_min_length() == PASSWORD_MIN_LENGTH

    def test_the_client_predicates_are_still_the_ones_this_module_mirrors(self) -> None:
        assert {each["id"]: each["met"] for each in _client_rules()} == MIRRORED_CLIENT_PREDICATES

    @pytest.mark.parametrize(
        "password",
        [
            GOOD_PASSWORD,
            WEAK_PASSWORD,
            "Ändern1!",
            "ÄNDERUNG1€",
            "PASSWORD1!",
            "Passwor٣!",
            "password1!",
            "Password11",
            "Correct horse1",
        ],
    )
    def test_the_cases_anv_30_argued_about_are_judged_the_same_way(self, password: str) -> None:
        """The passwords ANV-30's own tests name, run through the server's policy.

        ``ÄNDERUNG1€`` is the one that matters: ``validator`` refused it for having neither
        a capital letter nor a symbol, which is the defect ANV-30 fixed. A server that
        reintroduced ``[A-Z]`` would fail here.
        """
        expected = tuple(
            each["id"] for each in _client_rules() if each["id"] in _client_verdict(password)
        )
        assert failed_ids(password) == expected

    def test_the_one_known_divergence_is_astral_length_and_it_is_documented(self) -> None:
        """JavaScript's ``.length`` counts UTF-16 code units; Python's counts code points.

        A password of four astral characters is 8 units to the client and 4 characters to
        the server, so the client would accept what the server refuses. The server keeps
        code points, because that is what :data:`app.schemas.user.Password`'s ``min_length``
        already counts and two server-side definitions of "seven characters" would be worse
        than one client-side disagreement about a password nobody types. The refusal still
        names the rule, so the form has something to show.
        """
        astral = "𝐀𝐁𝐂𝟏!"
        assert len(astral) == 5
        assert len(astral.encode("utf-16-le")) // 2 == 9
        assert failed_ids(astral) == ("length",)


def _client_verdict(password: str) -> set[str]:
    """ANV-30's rules re-implemented from :data:`MIRRORED_CLIENT_PREDICATES`, by hand.

    Written out independently of ``app/domain/password.py`` rather than imported from it —
    an oracle that calls the implementation it is checking proves nothing. It exists so the
    parametrised case above compares two spellings of the policy rather than one.
    """
    broken: set[str] = set()
    if len(password) < _client_min_length():
        broken.add("length")
    if not any(unicodedata.category(character) == "Lu" for character in password):
        broken.add("uppercase")
    if not any(unicodedata.category(character) == "Nd" for character in password):
        broken.add("number")
    if all(unicodedata.category(character)[0] in "LN" for character in password):
        broken.add("symbol")
    return broken


# ---------------------------------------------------------------------------------------
# Layering
# ---------------------------------------------------------------------------------------


class TestPurity:
    """``CLAUDE.md`` §3, checked rather than trusted — prose conventions get broken."""

    @pytest.fixture
    def source(self) -> str:
        return Path(domain_password.__file__).read_text(encoding="utf-8")

    def test_it_imports_only_the_standard_library_and_the_schema_holding_the_floor(
        self, source: str
    ) -> None:
        modules: set[str] = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                modules.add(node.module)

        assert modules == {
            "__future__",
            "collections.abc",
            "dataclasses",
            "typing",
            "unicodedata",
            "app.schemas.user",
        }

    @pytest.mark.parametrize(
        "forbidden",
        ["select(", "session", "get_settings", "httpx", ".now(", "fastapi", "logger", "hash_"],
    )
    def test_it_performs_no_io_reads_no_clock_and_logs_nothing(
        self, source: str, forbidden: str
    ) -> None:
        """A policy that logged the candidate would log a password."""
        assert forbidden not in source

    def test_it_does_not_import_the_hashing_primitives(self) -> None:
        """Strength and hashing are different questions; only the service knows both."""
        assert not hasattr(domain_password, "hash_password")
