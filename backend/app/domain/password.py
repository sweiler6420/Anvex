"""The password strength policy: which of Anvex's rules a candidate password fails.

``app/schemas/user.py`` has said since ANV-8 that password *strength* "belongs in
``app/domain/``", and until ANV-43 no such rule was ever written — ANV-10 built the hashing
primitives, ANV-12 wired registration, and the policy fell through the gap between them. So
the API enforced a **length envelope and nothing else**, and the four rules ANV-30 renders
on the sign-up page were not a mirror of a server rule: they were the only place the policy
existed. Anything that is not our browser form — ``curl``, the ``/docs`` page that ships in
every environment, a future mobile client, a replayed request — could register with
``aaaaaaa``. A validation rule that exists only in the client is a suggestion.

**This module is the policy.** It is ``domain/`` because it is an Anvex rule that would
still be true on paper: plain string in, plain data out, no clock, no database, no request.

**It returns the rules that failed, never a boolean.** That is the same decision ANV-30
made on the client and for the same reason: ``validator.isStrongPassword`` could only ever
produce "Password Must Obey Rules", so a form built on it could not tell anybody *which*
requirement they had missed. :func:`failed_rules` hands the caller the unmet rules in policy
order, :class:`~app.services.user.UserService` puts their ids in ``details[FAILED_RULES_DETAIL]``,
and a client can then light up the individual lines it already renders instead of one opaque
banner.

Definitions are Unicode, matching ANV-30's ``\\p{Lu}`` / ``\\p{Nd}`` / "neither a letter nor
a number" exactly. `validator`'s ASCII-only ``upperCaseRegex`` (``/^[A-Z]$/``) and its fixed
punctuation ``symbolRegex`` were two of the defects ANV-30 fixed — they told someone that
``ÄNDERUNG1!`` had no capital letter and that ``€`` was not a symbol — and a server that
disagrees with the client about whether ``É`` is a letter is worse than no server rule at
all. ``unicodedata.category`` is the same General_Category table a JavaScript ``\\p{…}``
class is compiled from, so the two agree by construction rather than by coincidence;
``tests/unit/test_domain_password.py`` reads ANV-30's source and asserts it.

**Existing accounts are not affected, and nothing here may ever run at login.** The policy
applies when a password is *chosen* — registration today, a password change when
``PasswordChange`` grows an endpoint. Nothing re-validates an existing password on the way
in, and nothing should: every account in the legacy database predates this rule, so a
login-time strength check would lock out exactly the people who did nothing wrong, with a
"your password is invalid" message that is indistinguishable from "your password is wrong".
:class:`~app.services.auth.AuthService` verifies a digest and asks no further questions.
If a future ticket wants old passwords upgraded, the shape is a *prompt* after a successful
sign-in, never a refusal during one.

**The length rule is here as well as in the schema, and that is not redundant.** The 7-to-72
envelope on :data:`app.schemas.user.Password` is the HTTP edge refusing a malformed body
before a service is reached; this rule is the policy being *complete*, for every caller that
does not arrive through that schema, and so the four rules on the sign-up page have four
counterparts here rather than three. The floor is imported from
:data:`~app.schemas.user.PASSWORD_MIN_LENGTH` rather than retyped, so there is one 7.

The **ceiling** deliberately stays out of the policy: bcrypt's 72-**byte** limit is a
property of the hashing primitive rather than an Anvex rule, it is counted in bytes while
every rule here counts characters, and ``UserService._hash`` already translates
:class:`~app.utils.security.PasswordTooLongError` into the same 422.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final

from app.schemas.user import PASSWORD_MIN_LENGTH

#: The key :class:`~app.services.user.UserService` puts the failed rule ids under in a
#: :class:`~app.domain.errors.ValidationError`'s ``details``. Spelled once, here, because the
#: client branches on it: ``details["field"] == "password"`` says *which control*, and this
#: says *which of its rules*.
FAILED_RULES_DETAIL: Final[str] = "failed_rules"


@dataclass(frozen=True, slots=True)
class PasswordRule:
    """One requirement, its two human phrasings, and the predicate that decides it.

    ``label`` and ``missing`` are ANV-30's two spellings of the same requirement kept
    together: ``label`` is the line on the sign-up page ("At least 1 uppercase letter") and
    ``missing`` is how the rule is named inside a sentence when it fails ("an uppercase
    letter"). Both are carried here so the server's refusal reads as the same sentence the
    client would have produced, and so the drift test can compare all three fields against
    the client's array rather than only the ids.
    """

    #: Stable, machine-readable, and **identical to the client's**. This is what travels in
    #: ``details``; a client matches it against its own rule list.
    id: str
    #: The requirement stated positively, as the form displays it.
    label: str
    #: The requirement named as a noun phrase, for "Password needs …".
    missing: str
    #: ``True`` when ``password`` satisfies this rule. Pure, total, and never raises.
    met: Callable[[str], bool]


def _category(character: str) -> str:
    """``character``'s Unicode General_Category, e.g. ``"Lu"``.

    Unassigned code points answer ``"Cn"``, which is neither a letter nor a number and so
    counts as a symbol — the same answer JavaScript's ``[^\\p{L}\\p{N}]`` gives them.
    """
    return unicodedata.category(character)


def _has_uppercase_letter(password: str) -> bool:
    """ANV-30's ``/\\p{Lu}/u``: a General_Category=Uppercase_Letter code point.

    Not :meth:`str.isupper`, which is a different question — it is true for a *titlecase*
    letter and for the Other_Uppercase property, so it would accept characters ``\\p{Lu}``
    refuses and the two sides would quietly disagree on ``ǅ``.
    """
    return any(_category(character) == "Lu" for character in password)


def _has_number(password: str) -> bool:
    """ANV-30's ``/\\p{Nd}/u``: a decimal digit in *any* script, ``٣`` as much as ``3``.

    Not :meth:`str.isdigit` and not :meth:`str.isnumeric`, which also accept ``²`` (No) and
    ``Ⅴ`` (Nl) — categories JavaScript's ``\\p{Nd}`` excludes.
    """
    return any(_category(character) == "Nd" for character in password)


def _has_symbol(password: str) -> bool:
    """ANV-30's ``/[^\\p{L}\\p{N}]/u``: anything that is **neither a letter nor a number**.

    Deliberately broader than a punctuation list. ``€``, ``§``, a space and an emoji all
    count, because a form that refuses a password on the grounds that the symbol in it is
    not on our list is hostile — and because the alternative is a fixed ASCII set, which is
    the defect ANV-30 found in ``validator``.

    Note the deliberate asymmetry with :func:`_has_number`: the number rule is ``Nd`` only,
    while "not a number" here means the whole of ``N``. That is ANV-30's shape, not an
    oversight — ``Ⅴ`` satisfies neither rule, which is the conservative reading of both.
    """
    return any(_category(character)[0] not in {"L", "N"} for character in password)


#: The policy: four rules, in the order the sign-up page lists them and the order a failure
#: message names them. This tuple is the single definition — :func:`failed_rules`,
#: :func:`describe_failures` and the drift test all read it, so a rule cannot be enforced
#: without being nameable or named without being enforced.
PASSWORD_RULES: Final[tuple[PasswordRule, ...]] = (
    PasswordRule(
        id="length",
        label=f"At least {PASSWORD_MIN_LENGTH} characters",
        missing=f"{PASSWORD_MIN_LENGTH} characters",
        met=lambda password: len(password) >= PASSWORD_MIN_LENGTH,
    ),
    PasswordRule(
        id="uppercase",
        label="At least 1 uppercase letter",
        missing="an uppercase letter",
        met=_has_uppercase_letter,
    ),
    PasswordRule(
        id="number",
        label="At least 1 number",
        missing="a number",
        met=_has_number,
    ),
    PasswordRule(
        id="symbol",
        label="At least 1 symbol",
        missing="a symbol",
        met=_has_symbol,
    ),
)

#: The rule ids, in policy order. What a client is entitled to see in ``details``.
PASSWORD_RULE_IDS: Final[tuple[str, ...]] = tuple(rule.id for rule in PASSWORD_RULES)


def failed_rules(password: str) -> tuple[PasswordRule, ...]:
    """The rules ``password`` does **not** satisfy, in policy order.

    An empty tuple means the password is acceptable — which is the only reason a caller
    should ever treat this as a boolean. Every rule is evaluated, so the caller gets the
    whole list of what is wrong rather than the first thing that was: a form that fixes one
    complaint at a time is the failure mode ANV-30 removed from the client, and a server
    that hands back one rule would put it straight back.
    """
    return tuple(rule for rule in PASSWORD_RULES if not rule.met(password))


def describe_failures(rules: Sequence[PasswordRule]) -> str:
    """The sentence for a refusal: ``Password needs an uppercase letter and a number.``

    The same sentence ANV-30's ``passwordProblem`` builds, from the same ``missing``
    phrases, so an API consumer and the sign-up form say the same thing about the same
    password. It names every unmet rule; ``details[FAILED_RULES_DETAIL]`` carries the
    machine-readable half, and a client branches on that, never on this.

    An empty ``rules`` is a caller bug — there is no sentence for "nothing is wrong" — so it
    raises rather than inventing a reassuring message that would be shown as an error.
    """
    if not rules:
        raise ValueError("describe_failures() needs at least one failed rule")
    return f"Password needs {_join_phrases([rule.missing for rule in rules])}."


def _join_phrases(phrases: Sequence[str]) -> str:
    """``["a", "b", "c"]`` → ``"a, b and c"``. ANV-30's ``joinPhrases``, ported."""
    if len(phrases) < 2:
        return "".join(phrases)
    return f"{', '.join(phrases[:-1])} and {phrases[-1]}"


__all__ = [
    "FAILED_RULES_DETAIL",
    "PASSWORD_RULES",
    "PASSWORD_RULE_IDS",
    "PasswordRule",
    "describe_failures",
    "failed_rules",
]
