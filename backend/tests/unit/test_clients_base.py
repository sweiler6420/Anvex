"""Unit tests for ``app/clients/base.py`` — the layer's rules and its pure arithmetic.

Two halves, and the first is the important one.

``TestTheLayerStaysInItsLane`` parses **every** module in ``app/clients/`` and fails if one
of them reaches for a database, another Anvex layer, or a blocking sleep. ANV-17 only ships
the base class, so today it is guarding one module; its real job starts with ANV-18, ANV-19
and ANV-20, when three vendors' worth of code will be written against a contract that
otherwise exists only in a docstring. ``tests/unit/test_data_loader.py`` makes the same
argument for ``app/data/``: a purity convention that lives only in prose gets broken.

The second half is :class:`~app.clients.base.RetryPolicy`, :func:`redact_url` and
:func:`parse_retry_after` — pure functions of their arguments, so they belong here rather
than behind a mocked socket. The behaviour they drive is tested in
``tests/integration/test_client_base.py``.

No fixtures, no I/O: reading this repository's own source is not the kind of I/O the ``db``
marker cares about, so the module runs with Docker stopped.
"""

from __future__ import annotations

import ast
import asyncio
from dataclasses import FrozenInstanceError
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from app.clients import base as base_module
from app.clients.base import (
    CONNECT_TIMEOUT_SECONDS,
    DEFAULT_HEADERS,
    DEFAULT_RETRY,
    DEFAULT_TIMEOUT,
    READ_TIMEOUT_SECONDS,
    REDACTED,
    RETRYABLE,
    BaseHTTPClient,
    Failure,
    RetryPolicy,
    parse_retry_after,
    redact_url,
    scrub,
)
from app.domain.errors import AnvexError, ExternalServiceError

CLIENTS_DIR = Path(base_module.__file__).resolve().parent

#: Import prefixes no module in ``app/clients/`` may name, and why. The message is half the
#: point of the test — a future ticket that trips it should learn the rule from the failure
#: rather than from archaeology.
FORBIDDEN_IMPORTS: dict[str, str] = {
    "sqlalchemy": "a client makes no queries; `CLAUDE.md` §3 puts every `select(` in app/repos/",
    "app.repos": "a client does not read or write Anvex data — it fetches a vendor's",
    "app.db": "a client owns no session and no engine",
    "app.models": "a vendor payload is not an ORM row; parse into the client's own model",
    "app.services": "dependencies flow downward: services call clients, never the reverse",
    "app.schemas": "app/schemas/ is the API's public shape; a vendor does not share it",
    "app.domain.auth": "a client knows no Anvex rule but 'the upstream failed'",
    "app.jobs": "a Celery task calls a service, which calls a client, not the other way",
    "app.api": "a client has no idea it is inside a web application",
    "requests": "`CLAUDE.md` §2: async everywhere, so httpx.AsyncClient and never `requests`",
}

#: The complete allow-list of ``app.`` imports for the package. Anything else is a layering
#: violation even if it is not spelled out in :data:`FORBIDDEN_IMPORTS`.
ALLOWED_APP_IMPORTS: frozenset[str] = frozenset(
    {
        # The shared base and any future intra-package helper.
        "app.clients",
        "app.clients.base",
        # The one Anvex vocabulary a client is allowed: "the upstream failed" (→ 502).
        "app.domain.errors",
        # For the `Settings` type a subclass is constructed from. Reading the environment
        # still happens only in app/settings.py.
        "app.settings",
    }
)


def client_sources() -> dict[str, str]:
    """Every module in the package, ``__init__.py`` included."""
    return {
        path.name: path.read_text(encoding="utf-8") for path in sorted(CLIENTS_DIR.glob("*.py"))
    }


def imported_modules(source: str) -> set[str]:
    """Every module name a source file imports, however it spells the import."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


def imported_names(source: str) -> set[str]:
    """``module.name`` for every ``from module import name``, for finer-grained bans."""
    return {
        f"{node.module}.{alias.name}"
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module
        for alias in node.names
    }


def called_attributes(source: str) -> set[str]:
    """``"time.sleep"``-style dotted names for every call in the file."""
    calls: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            value = node.func.value
            if isinstance(value, ast.Name):
                calls.add(f"{value.id}.{node.func.attr}")
    return calls


# ---------------------------------------------------------------------------------------
# the layer's own rules
# ---------------------------------------------------------------------------------------


class TestTheLayerStaysInItsLane:
    """``CLAUDE.md`` §3: primitives in, vendor data out, zero Anvex knowledge.

    Read out of the source rather than trusted to a docstring. This is the test that keeps
    ANV-18/19/20 honest, so it is deliberately strict and its failures say what the rule is.
    """

    @pytest.fixture
    def sources(self) -> dict[str, str]:
        return client_sources()

    def test_the_sweep_actually_sees_the_package(self, sources: dict[str, str]) -> None:
        """A sweep over an empty set passes vacuously, which would be worse than no test."""
        assert "base.py" in sources
        assert "__init__.py" in sources
        assert all(source.strip() for source in sources.values())

    @pytest.mark.parametrize(("forbidden", "why"), sorted(FORBIDDEN_IMPORTS.items()))
    def test_no_client_imports_a_layer_it_may_not_reach(
        self, sources: dict[str, str], forbidden: str, why: str
    ) -> None:
        for name, source in sources.items():
            offending = {
                module
                for module in imported_modules(source)
                if module == forbidden or module.startswith(f"{forbidden}.")
            }
            assert not offending, (
                f"app/clients/{name} imports {sorted(offending)}. "
                f"A client may not import `{forbidden}`: {why}."
            )

    def test_the_only_app_imports_are_the_allowed_ones(self, sources: dict[str, str]) -> None:
        """The positive half: an allow-list catches a layer nobody thought to forbid."""
        for name, source in sources.items():
            app_imports = {m for m in imported_modules(source) if m.startswith("app.")}
            assert app_imports <= ALLOWED_APP_IMPORTS, (
                f"app/clients/{name} imports {sorted(app_imports - ALLOWED_APP_IMPORTS)}. "
                "A client knows one vendor and nothing about Anvex; the only Anvex names in "
                f"reach are {sorted(ALLOWED_APP_IMPORTS)}."
            )

    def test_the_error_vocabulary_is_exactly_external_service_error(
        self, sources: dict[str, str]
    ) -> None:
        """Also a positive control: proves the sweep can see an import it expects."""
        from_errors = {
            name.removeprefix("app.domain.errors.")
            for source in sources.values()
            for name in imported_names(source)
            if name.startswith("app.domain.errors.")
        }

        assert from_errors == {"ExternalServiceError"}

    def test_nothing_blocks_the_event_loop_on_a_sleep(self, sources: dict[str, str]) -> None:
        """`time.sleep` in an async retry loop stops the whole worker, not just this call."""
        for name, source in sources.items():
            assert "time.sleep" not in called_attributes(source), (
                f"app/clients/{name} calls time.sleep. A retry wait is awaited "
                "(`asyncio.sleep`), never slept through — the event loop is shared."
            )
            assert "time.sleep" not in imported_names(source), f"app/clients/{name}"

    def test_no_client_prints(self, sources: dict[str, str]) -> None:
        """``CLAUDE.md`` §4: logging is structured, and there is no bare ``print``."""
        for name, source in sources.items():
            calls = {
                node.func.id
                for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            }
            assert "print" not in calls, f"app/clients/{name}"

    def test_the_sweep_would_catch_a_violation(self) -> None:
        """The enforcement itself, tested. A checker that cannot fail proves nothing."""
        violation = "from app.repos.stock import stock_repo\nimport sqlalchemy\n"

        modules = imported_modules(violation)

        assert {m for m in modules if m.startswith("app.")} - ALLOWED_APP_IMPORTS
        assert any(m == "sqlalchemy" or m.startswith("sqlalchemy.") for m in modules)


# ---------------------------------------------------------------------------------------
# redaction
# ---------------------------------------------------------------------------------------


class TestRedactUrl:
    def test_a_credential_named_parameter_is_blanked(self) -> None:
        redacted = redact_url("https://vendor.example/query?function=QUOTE&apikey=s3cret")

        assert "s3cret" not in redacted
        assert f"apikey={REDACTED}" in redacted

    def test_the_diagnostic_parameters_survive(self) -> None:
        """A log line that says nothing is as useless as one that says too much."""
        redacted = redact_url("https://vendor.example/query?function=QUOTE&symbol=AAPL&apikey=k")

        assert "function=QUOTE" in redacted
        assert "symbol=AAPL" in redacted

    def test_a_secret_under_an_innocent_name_is_still_blanked(self) -> None:
        """NewsAPI-style naming is not hypothetical: the value is checked, not just the name."""
        redacted = redact_url("https://vendor.example/v2/all?u=abc123&q=tesla", secrets=["abc123"])

        assert "abc123" not in redacted
        assert f"u={REDACTED}" in redacted
        assert "q=tesla" in redacted

    def test_a_url_without_a_query_is_returned_intact(self) -> None:
        assert redact_url("https://vendor.example/v1/quote") == "https://vendor.example/v1/quote"

    def test_an_httpx_url_is_accepted(self) -> None:
        url = httpx.URL("https://vendor.example/query", params={"token": "abc"})

        assert "abc" not in redact_url(url)

    def test_an_empty_secret_never_matches_everything(self) -> None:
        """A client with an unset key must not turn every value into ``***``."""
        redacted = redact_url("https://vendor.example/q?symbol=AAPL", secrets=[""])

        assert "symbol=AAPL" in redacted

    @pytest.mark.parametrize(
        "name", ["apikey", "API_KEY", "Token", "authorization", "password", "signature"]
    )
    def test_the_sensitive_names_are_matched_case_insensitively(self, name: str) -> None:
        assert "hunter2" not in redact_url(f"https://vendor.example/q?{name}=hunter2")


class TestScrub:
    def test_every_occurrence_of_a_secret_is_replaced(self) -> None:
        cleaned = scrub("used k1, then k1 again", ["k1"])

        assert cleaned == f"used {REDACTED}, then {REDACTED} again"

    def test_an_empty_secret_is_ignored(self) -> None:
        assert scrub("anything at all", ["", "  "]) == "anything at all"


# ---------------------------------------------------------------------------------------
# the retry policy
# ---------------------------------------------------------------------------------------


class TestRetryPolicy:
    @pytest.mark.parametrize(
        ("failure", "expected"),
        [
            (Failure.TRANSPORT, 3),
            (Failure.SERVER_ERROR, 3),
            (Failure.RATE_LIMITED, 2),
            (Failure.CLIENT_ERROR, 1),
            (Failure.MALFORMED, 1),
            (Failure.PROTOCOL, 1),
            (Failure.REDIRECT, 1),
        ],
    )
    def test_each_failure_gets_the_budget_it_deserves(
        self, failure: Failure, expected: int
    ) -> None:
        """One attempt means "never retried" — the 4xx rule, expressed as arithmetic."""
        assert DEFAULT_RETRY.attempts_for(failure) == expected

    def test_only_transport_server_and_rate_limit_failures_are_retryable(self) -> None:
        worth_retrying = {Failure.TRANSPORT, Failure.SERVER_ERROR, Failure.RATE_LIMITED}

        assert worth_retrying == RETRYABLE

    def test_backoff_grows_geometrically(self) -> None:
        policy = RetryPolicy(initial_backoff_seconds=0.2, backoff_multiplier=2.0)

        waits = [policy.backoff_for(attempt) for attempt in (1, 2, 3)]

        assert waits == pytest.approx([0.2, 0.4, 0.8])

    def test_backoff_is_capped(self) -> None:
        policy = RetryPolicy(initial_backoff_seconds=1.0, max_backoff_seconds=2.0)

        assert policy.backoff_for(10) == pytest.approx(2.0)

    @pytest.mark.parametrize("jitter", [0.0, 0.25, 0.5, 0.999])
    def test_jitter_only_ever_shortens_the_wait(self, jitter: float) -> None:
        """Full-length is the ceiling: jitter spreads a fan-out, it never extends a wait."""
        policy = RetryPolicy(initial_backoff_seconds=1.0, jitter_ratio=0.25)

        wait = policy.backoff_for(1, jitter=jitter)

        assert 0.75 <= wait <= 1.0

    def test_a_retry_after_within_the_cap_is_honoured(self) -> None:
        assert DEFAULT_RETRY.wait_for(1, retry_after=1.5) == pytest.approx(1.5)

    def test_a_retry_after_beyond_the_cap_means_stop(self) -> None:
        """A vendor asking for a minute is asking for longer than a request may be held."""
        assert DEFAULT_RETRY.wait_for(1, retry_after=60.0) is None

    def test_without_a_retry_after_the_ordinary_backoff_applies(self) -> None:
        assert DEFAULT_RETRY.wait_for(1, jitter=0.0) == pytest.approx(
            DEFAULT_RETRY.initial_backoff_seconds
        )

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"attempts": 0},
            {"rate_limited_attempts": 0},
            {"initial_backoff_seconds": -1},
            {"max_backoff_seconds": -1},
            {"backoff_multiplier": 0.5},
            {"jitter_ratio": 1.0},
            {"jitter_ratio": -0.1},
            {"retry_after_cap_seconds": -1},
            {"total_budget_seconds": 0},
        ],
    )
    def test_a_nonsense_policy_is_refused_at_construction(self, kwargs: dict[str, float]) -> None:
        with pytest.raises(ValueError):
            RetryPolicy(**kwargs)  # type: ignore[arg-type]

    def test_a_policy_is_immutable(self) -> None:
        """So one client's tuning cannot leak into another's through a shared default."""
        with pytest.raises(FrozenInstanceError):
            DEFAULT_RETRY.attempts = 99  # type: ignore[misc]


class TestParseRetryAfter:
    @pytest.mark.parametrize(
        ("header", "expected"),
        [("3", 3.0), (" 0.5 ", 0.5), ("0", 0.0), ("-5", 0.0)],
    )
    def test_delta_seconds_are_understood(self, header: str, expected: float) -> None:
        assert parse_retry_after(header) == pytest.approx(expected)

    @pytest.mark.parametrize(
        "header", [None, "", "soon", "Wed, 21 Oct 2015 07:28:00 GMT"], ids=lambda h: repr(h)
    )
    def test_anything_else_is_treated_as_absent(self, header: str | None) -> None:
        """Including the HTTP-date form: parsing it needs a clock, and the capped backoff
        is a safe answer without one."""
        assert parse_retry_after(header) is None


# ---------------------------------------------------------------------------------------
# construction and defaults
# ---------------------------------------------------------------------------------------


class TestTimeouts:
    def test_all_four_timeouts_are_set_explicitly(self) -> None:
        """A bare number sets all four at once and hides which one was meant."""
        assert DEFAULT_TIMEOUT.connect == CONNECT_TIMEOUT_SECONDS
        assert DEFAULT_TIMEOUT.read == READ_TIMEOUT_SECONDS
        assert DEFAULT_TIMEOUT.write is not None
        assert DEFAULT_TIMEOUT.pool is not None

    def test_connecting_is_given_less_patience_than_reading(self) -> None:
        """A handshake is fast or it is never; a vendor is allowed to think about a query."""
        assert CONNECT_TIMEOUT_SECONDS < READ_TIMEOUT_SECONDS


class TestConstruction:
    def test_a_subclass_without_a_vendor_cannot_be_built(self) -> None:
        class Nameless(BaseHTTPClient):
            base_url = "https://vendor.example"

        with pytest.raises(TypeError, match="vendor"):
            Nameless()

    def test_a_subclass_without_a_base_url_cannot_be_built(self) -> None:
        class Homeless(BaseHTTPClient):
            vendor = "homeless"

        with pytest.raises(TypeError, match="base_url"):
            Homeless()

    def test_a_base_url_may_be_supplied_per_instance(self) -> None:
        class Vendor(BaseHTTPClient):
            vendor = "vendor"

        client = Vendor(base_url="https://other.example")

        assert str(client.http.base_url).startswith("https://other.example")

    def test_the_default_sleeper_is_the_non_blocking_one(self) -> None:
        """The whole retry design rests on this: a wait yields the loop, never holds it."""

        class Vendor(BaseHTTPClient):
            vendor = "vendor"
            base_url = "https://vendor.example"

        assert Vendor()._sleep is asyncio.sleep

    def test_default_headers_identify_anvex_and_ask_for_json(self) -> None:
        assert DEFAULT_HEADERS["Accept"] == "application/json"
        assert DEFAULT_HEADERS["User-Agent"]

    def test_redirects_are_not_followed(self) -> None:
        """Following one would resend the credential-bearing URL to a host the vendor chose."""

        class Vendor(BaseHTTPClient):
            vendor = "vendor"
            base_url = "https://vendor.example"

        assert Vendor().http.follow_redirects is False


class TestAuthUnwrapping:
    def test_a_secret_str_is_unwrapped_and_registered_as_a_secret(self) -> None:
        plain, secrets = BaseHTTPClient._unwrap({"apikey": SecretStr("k"), "mode": "fast"})

        assert plain == {"apikey": "k", "mode": "fast"}
        assert secrets == ("k",)

    def test_a_plain_value_is_not_treated_as_a_secret(self) -> None:
        """Otherwise ``mode=fast`` would be redacted out of every log line."""
        _, secrets = BaseHTTPClient._unwrap({"mode": "fast"})

        assert secrets == ()


class TestTheErrorContract:
    def test_every_client_failure_is_one_exception_type(self) -> None:
        """Mapped to 502 by ``app/middleware/errors.py``; ``CLAUDE.md`` §4's table."""
        assert issubclass(ExternalServiceError, AnvexError)
        assert ExternalServiceError.code == "external_service_error"

    @pytest.mark.parametrize("failure", list(Failure))
    def test_every_failure_kind_has_a_message_naming_only_the_vendor(
        self, failure: Failure
    ) -> None:
        class Vendor(BaseHTTPClient):
            vendor = "vendor"
            base_url = "https://vendor.example"

        error = Vendor()._error(failure, attempts=1)

        assert "vendor" in error.message
        assert error.details["reason"] == failure.value
        assert error.details["service"] == "vendor"
        assert error.details["attempts"] == 1

    def test_a_subclass_may_raise_a_failure_that_has_no_attempt_count(self) -> None:
        """ANV-18: AlphaVantage answers a throttled request with ``200`` and a ``"Note"``,
        so the *parser* raises — by which point the retry loop has already succeeded and
        there is no attempt count belonging to that failure. The key is omitted rather than
        fabricated, and the subclass still gets the base's message rather than writing its
        own, so a rate limit reads identically whether it arrived as a 429 or as a 200.
        """

        class Vendor(BaseHTTPClient):
            vendor = "vendor"
            base_url = "https://vendor.example"

        error = Vendor()._error(Failure.RATE_LIMITED)

        assert "attempts" not in error.details
        assert error.details == {"service": "vendor", "reason": "rate_limited"}
        assert error.message == "The upstream service 'vendor' is rate limiting Anvex."
