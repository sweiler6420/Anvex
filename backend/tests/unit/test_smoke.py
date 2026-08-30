"""The end-to-end smoke driver, checked without booting anything (ANV-41).

``backend/scripts/smoke.py`` is the one program in this repository that runs the real
stack, so almost none of it is testable here — and the parts that *are* testable are
exactly the parts that would silently rot:

1. **The checklist and the code agree.** ``docs/smoke.md``'s table is what a reader trusts
   when they are deciding whether a green run means anything. It is compared against
   ``STEPS`` in both directions, ids and order, so a step added to one and not the other
   fails the backend suite rather than becoming a lie in a document.
2. **The live vendor call is opt-in and off by default.** AlphaVantage's free tier is about
   25 calls a day and the key is the owner's. That default is asserted rather than trusted,
   and so is the rule that keeps a *configured* key from being spent by a plain run.
3. **No credential is ever echoed.** A smoke script prints generously by design; the tokens
   and the password it holds must never be among the things it prints.
4. **The failure reports are usable.** Every ``SmokeFailure`` raise site is checked, by
   parsing the module, to supply both ``expected`` and ``observed`` — because
   ``AssertionError`` in a smoke log at two in the morning costs an hour.

Everything here is fixtureless and offline: no Docker, no database, no network. The parts
that need those are the smoke run itself, which is the point of it.
"""

from __future__ import annotations

import ast
import datetime as dt
import re
from pathlib import Path
from typing import Final

import httpx
import pytest
from pydantic import SecretStr

from app.clients.alphavantage import AlphaVantageClient, IntradayInterval
from app.domain import auth as auth_errors
from app.domain import errors
from app.domain.errors import AnvexError
from app.domain.ingest import SESSION_CLOSE, SESSION_OPEN, Month
from app.domain.password import failed_rules
from app.middleware.errors import _HTTP_STATUS_CODES as error_status_codes
from app.schemas.user import UserCreate
from app.settings import REPO_ROOT, get_settings
from scripts import smoke

CHECKLIST: Final[Path] = REPO_ROOT / "docs" / "smoke.md"
SOURCE: Final[Path] = REPO_ROOT / "backend" / "scripts" / "smoke.py"

#: A row of the checklist's step table: `| 3 | \`compose-up\` | … | … |`.
STEP_ROW: Final[re.Pattern[str]] = re.compile(r"^\|\s*(\d+)\s*\|\s*`([a-z-]+)`\s*\|", re.M)

#: Every long flag `build_parser` declares, as it appears on a command line.
PARSER_FLAGS: Final[tuple[str, ...]] = (
    "--clean",
    "--yes",
    "--live-vendor",
    "--skip-frontend",
    "--steps",
)

#: Values that must never reach the output. Not "should not" — the module prints step
#: detail lines liberally, and the whole point is that these are not in them.
SECRETS_IN_SCOPE: Final[tuple[str, ...]] = ("access_token", "refresh_token", "PASSWORD")


def checklist() -> str:
    return CHECKLIST.read_text(encoding="utf-8")


def documented_steps() -> list[tuple[int, str]]:
    return [(int(number), name) for number, name in STEP_ROW.findall(checklist())]


def source() -> str:
    return SOURCE.read_text(encoding="utf-8")


def module_tree() -> ast.Module:
    return ast.parse(source())


def printed_expressions() -> list[str]:
    """The source of everything the driver prints or returns as a step's detail line.

    Two shapes, and between them they are the whole output surface: an argument to
    ``out``/``note``, and the value a ``step_*`` function returns. Deliberately *not* every
    f-string in the module — ``Context.bearer`` interpolates the access token into an
    ``Authorization`` header, which is the correct thing to do with it and is not output.
    """
    text = source()
    tree = ast.parse(text)
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") in {"out", "note"}:
            found += [ast.get_source_segment(text, argument) or "" for argument in node.args]
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("step_"):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Return) and inner.value is not None:
                found.append(ast.get_source_segment(text, inner.value) or "")
    return found


class TestTheChecklistAndTheCodeAgree:
    """`docs/smoke.md` is the artefact a reader trusts; this is what keeps it true."""

    def test_the_checklist_lists_every_step_in_order(self) -> None:
        assert [name for _, name in documented_steps()] == [step.id for step in smoke.STEPS]

    def test_the_checklist_numbers_them_from_one(self) -> None:
        """Numbering is what makes `FAILED at step 6/20` findable in the document."""
        assert [number for number, _ in documented_steps()] == list(range(1, len(smoke.STEPS) + 1))

    def test_the_step_ids_are_unique(self) -> None:
        ids = [step.id for step in smoke.STEPS]
        assert len(set(ids)) == len(ids)

    @pytest.mark.parametrize("step", smoke.STEPS, ids=lambda step: step.id)
    def test_every_step_has_a_title(self, step: smoke.Step) -> None:
        assert step.title.strip()

    def test_only_the_frontend_legs_are_optional(self) -> None:
        """`--skip-frontend` is the one thing that may be dropped, and it drops the tail.

        A smoke test with a menu is a smoke test nobody can quote a passing run of, so the
        optional set is asserted to be exactly the three container-and-browser steps at the
        end rather than merely "some steps".
        """
        optional = [step.id for step in smoke.STEPS if step.frontend]
        assert optional == ["frontend-up", "frontend-build", "cold-load"]
        assert optional == [step.id for step in smoke.STEPS[-3:]]

    def test_skipping_the_frontend_drops_exactly_those(self) -> None:
        kept = [step.id for step in smoke.selected(smoke.Options(skip_frontend=True))]
        assert kept == [step.id for step in smoke.STEPS if not step.frontend]

    def test_nothing_else_is_skippable(self) -> None:
        kept = [step.id for step in smoke.selected(smoke.Options())]
        assert kept == [step.id for step in smoke.STEPS]

    @pytest.mark.parametrize("flag", PARSER_FLAGS)
    def test_the_checklist_documents_every_flag(self, flag: str) -> None:
        assert flag in checklist(), f"`docs/smoke.md` never mentions {flag}"

    def test_the_checklist_invents_no_flag(self) -> None:
        """The other direction: a flag in the document that the program does not accept."""
        mentioned = set(re.findall(r"--[a-z][a-z-]+", checklist()))
        assert mentioned <= set(PARSER_FLAGS) | {"--volumes"}


class TestTheVendorLegIsOptIn:
    """The quota rule, asserted rather than trusted. ~25 calls a day, and not ours."""

    def test_a_plain_run_does_not_ask_for_a_live_call(self) -> None:
        assert smoke.build_parser().parse_args([]).live_vendor is False

    def test_the_option_defaults_to_off(self) -> None:
        assert smoke.Options().live_vendor is False

    def test_the_flag_turns_it_on(self) -> None:
        assert smoke.build_parser().parse_args(["--live-vendor"]).live_vendor is True

    def test_the_live_retry_policy_allows_exactly_one_attempt(self) -> None:
        """The base's default would turn one vendor 5xx into three requests."""
        assert smoke.LIVE_RETRY.attempts == 1
        assert smoke.LIVE_RETRY.rate_limited_attempts == 1

    def test_a_configured_key_is_not_spent_by_a_plain_run(self) -> None:
        """The case that would cost real money on somebody else's machine.

        A key is present and `--live-vendor` was not passed, so the ingest **task** — the
        one that runs inside a worker holding the real key — must not be published at all.
        `publish_task` is replaced with something that fails the test if it is reached.
        """
        context = smoke.Context(
            options=smoke.Options(),
            settings=get_settings(),
            api="http://localhost:8000",
            web="http://localhost:5173",
            vendor_key_present=True,
        )
        original = smoke.publish_task
        smoke.publish_task = _forbidden  # type: ignore[assignment]
        try:
            detail = smoke.step_ingest_task(context)
        finally:
            smoke.publish_task = original  # type: ignore[assignment]
        assert "skipped" in detail
        assert any("not published" in note for note in context.notes)

    def test_with_no_key_the_task_is_published_and_expected_to_refuse(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Zero cost, and still worth publishing: it proves the seam up to the vendor."""
        monkeypatch.setattr(
            smoke,
            "publish_task",
            lambda *_, **__: {
                "id": "abc",
                "state": "FAILURE",
                "result": {"exc_type": "ExternalServiceError", "exc_message": ["blank"]},
            },
        )
        context = smoke.Context(
            options=smoke.Options(),
            settings=get_settings(),
            api="",
            web="",
            vendor_key_present=False,
        )
        assert "not_configured" in smoke.step_ingest_task(context)

    def test_a_success_with_no_key_is_a_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A call really did leave the machine with nothing configured to authenticate it."""
        monkeypatch.setattr(
            smoke,
            "publish_task",
            lambda *_, **__: {"id": "abc", "state": "SUCCESS", "result": {"written": 3}},
        )
        context = smoke.Context(
            options=smoke.Options(),
            settings=get_settings(),
            api="",
            web="",
            vendor_key_present=False,
        )
        with pytest.raises(smoke.SmokeFailure) as raised:
            smoke.step_ingest_task(context)
        assert "socket" in raised.value.expected

    def test_asking_for_a_live_call_without_a_key_fails_before_anything_starts(self) -> None:
        """Better than discovering it eight steps in, having already built two images."""
        settings = get_settings().model_copy(update={"alphavantage_api_key": SecretStr("")})
        context = smoke.Context(
            options=smoke.Options(live_vendor=True), settings=settings, api="", web=""
        )
        with pytest.raises(smoke.SmokeFailure) as raised:
            smoke.resolve_vendor_leg(context)
        assert "--live-vendor" in raised.value.expected

    def test_a_blank_key_resolves_to_the_stub(self) -> None:
        settings = get_settings().model_copy(update={"alphavantage_api_key": SecretStr("")})
        context = smoke.Context(options=smoke.Options(), settings=settings, api="", web="")
        assert smoke.resolve_vendor_leg(context) == "not configured"
        assert context.vendor_leg == "stubbed"
        assert context.vendor_key_present is False

    def test_a_present_key_is_reported_without_being_read(self) -> None:
        settings = get_settings().model_copy(update={"alphavantage_api_key": SecretStr("abc")})
        context = smoke.Context(options=smoke.Options(), settings=settings, api="", web="")
        assert smoke.resolve_vendor_leg(context) == "configured"
        assert context.vendor_leg == "stubbed", "a key being present is not a request to spend it"


class TestTheStubIsAFaithfulVendor:
    """A stub that answers anything proves only that the parser works."""

    def test_the_payload_parses_as_a_real_intraday_response(self) -> None:
        client = AlphaVantageClient(get_settings())
        series = client._parse_intraday(
            smoke.stub_payload(
                symbol=smoke.SYMBOL,
                first_bar=smoke.STUB_FIRST_BAR,
                count=smoke.STUB_CANDLE_COUNT,
            ),
            symbol=smoke.SYMBOL,
            interval=IntradayInterval.FIVE_MINUTES,
        )
        assert series.symbol == smoke.SYMBOL
        assert series.timezone == smoke.STUB_TIMEZONE
        assert len(series.candles) == smoke.STUB_CANDLE_COUNT

    def test_every_stub_candle_is_inside_the_trading_session(self) -> None:
        """Otherwise the ingest filters them all out and `written=0` looks like a bug."""
        payload = smoke.stub_payload(
            symbol=smoke.SYMBOL, first_bar=smoke.STUB_FIRST_BAR, count=smoke.STUB_CANDLE_COUNT
        )
        client = AlphaVantageClient(get_settings())
        series = client._parse_intraday(
            payload, symbol=smoke.SYMBOL, interval=IntradayInterval.FIVE_MINUTES
        )
        for candle in series.candles:
            assert SESSION_OPEN < candle.time <= SESSION_CLOSE

    def test_the_stub_candles_fall_in_the_month_that_is_ingested(self) -> None:
        assert smoke.STUB_FIRST_BAR.startswith(smoke.MONTH)
        assert str(Month.parse(smoke.MONTH)) == smoke.MONTH

    def test_the_stub_answers_the_request_the_client_really_builds(self) -> None:
        transport = smoke.stub_transport(
            symbol=smoke.SYMBOL, month=smoke.MONTH, count=smoke.STUB_CANDLE_COUNT
        )
        request = httpx.Request(
            "GET",
            httpx.URL(
                "https://www.alphavantage.co/query",
                params={
                    "function": "TIME_SERIES_INTRADAY",
                    "symbol": smoke.SYMBOL,
                    "interval": "5min",
                    "outputsize": "full",
                    "month": smoke.MONTH,
                    "apikey": smoke.STUB_API_KEY,
                },
            ),
        )
        assert transport.handler(request).status_code == 200

    @pytest.mark.parametrize(
        "wrong",
        [{"symbol": "NOPE"}, {"month": "1999-01"}, {"interval": "1min"}, {"apikey": "other"}],
        ids=["symbol", "month", "interval", "apikey"],
    )
    def test_the_stub_refuses_a_request_that_is_not_the_one_it_expects(
        self, wrong: dict[str, str]
    ) -> None:
        transport = smoke.stub_transport(
            symbol=smoke.SYMBOL, month=smoke.MONTH, count=smoke.STUB_CANDLE_COUNT
        )
        params = {
            "function": "TIME_SERIES_INTRADAY",
            "symbol": smoke.SYMBOL,
            "interval": "5min",
            "outputsize": "full",
            "month": smoke.MONTH,
            "apikey": smoke.STUB_API_KEY,
            **wrong,
        }
        request = httpx.Request(
            "GET", httpx.URL("https://www.alphavantage.co/query", params=params)
        )
        with pytest.raises(smoke.SmokeFailure):
            transport.handler(request)


class TestNothingSecretIsPrinted:
    """A smoke script prints generously. These must not be among the things it prints."""

    @pytest.mark.parametrize("secret", SECRETS_IN_SCOPE)
    def test_nothing_the_driver_prints_mentions_a_credential(self, secret: str) -> None:
        """Read off the source, because the run that would prove it is the real one.

        The output surface is everything handed to `out`/`note` plus everything a `step_*`
        function returns — the runner prints that as the step's detail line. None of it may
        name the access token, the refresh token or the password.
        """
        offenders = [text for text in printed_expressions() if secret in text]
        assert not offenders, f"{secret} reaches the output: {offenders}"

    def test_the_stub_key_is_obviously_not_a_real_one(self) -> None:
        """It is a literal in a public repository, so it has to read as one on sight."""
        assert "not-a-real-key" in smoke.STUB_API_KEY

    def test_the_module_never_unwraps_a_secret(self) -> None:
        """`.get_secret_value()` appears nowhere: the plaintext key is never in scope here.

        `app/clients/base.py` unwraps it inside one stack frame while building a request,
        which is the only place in the application allowed to. A smoke driver has no reason
        to, and the way to keep it that way is to assert it cannot.
        """
        assert "get_secret_value" not in source()

    def test_the_only_secret_this_module_constructs_is_the_stub(self) -> None:
        text = source()
        assert text.count("SecretStr(") == 1
        assert "SecretStr(STUB_API_KEY)" in text

    def test_the_key_is_reported_as_a_boolean(self) -> None:
        """`configured` / `not configured` — never a value, never a prefix, never a length."""
        segment = ast.get_source_segment(source(), _function(module_tree(), "resolve_vendor_leg"))
        assert segment is not None
        assert "is_configured" in segment
        assert "not configured" in segment


class TestTheFailureReportsAreUsable:
    """`FAILED at step 6/20 … expected … observed … hint` or it is not worth having."""

    def test_every_failure_names_what_it_expected_and_what_it_saw(self) -> None:
        missing: list[int] = []
        for node in ast.walk(module_tree()):
            if not isinstance(node, ast.Raise) or node.exc is None:
                continue
            call = node.exc
            if not isinstance(call, ast.Call) or getattr(call.func, "id", "") != "SmokeFailure":
                continue
            supplied = {keyword.arg for keyword in call.keywords}
            if not {"expected", "observed"} <= supplied:
                missing.append(node.lineno)
        assert not missing, f"SmokeFailure raised without expected/observed at lines {missing}"

    def test_the_error_envelope_is_read_by_code_and_not_by_message(self) -> None:
        response = httpx.Response(
            409,
            json={
                "error": {
                    "code": "conflict",
                    "message": "That username is taken.",
                    "details": {"field": "username"},
                    "request_id": "r1",
                }
            },
        )
        assert smoke.envelope(response) == ("conflict", "That username is taken.")

    def test_a_body_that_is_not_an_envelope_still_reports_something(self) -> None:
        code, message = smoke.envelope(httpx.Response(502, text="upstream said no"))
        assert code == ""
        assert "upstream" in message

    def test_a_wrong_code_fails_even_when_the_status_is_right(self) -> None:
        response = httpx.Response(409, json={"error": {"code": "not_found", "message": "x"}})
        with pytest.raises(smoke.SmokeFailure) as raised:
            smoke.expect_error(response, 409, "conflict", what="registering twice")
        assert "conflict" in raised.value.expected

    def test_a_bare_array_is_not_a_page(self) -> None:
        """Every list endpoint is `Page[T]`; a bare array would mean the route changed."""
        with pytest.raises(smoke.SmokeFailure) as raised:
            smoke.page_items([{"stock_id": "1"}], what="GET /v1/stocks")
        assert "Page[T]" in raised.value.expected

    def test_a_page_yields_its_items(self) -> None:
        page = {"items": [1, 2], "total": 2, "limit": 50, "offset": 0, "has_more": False}
        assert smoke.page_items(page, what="GET /v1/stocks") == [1, 2]

    def test_every_error_code_it_expects_is_one_the_application_can_produce(self) -> None:
        """Found by running it: the first spelling expected `invalid_credentials`.

        `app/domain/errors.py` has seven error classes and between them only seven codes —
        a wrong password and a replayed refresh token are both `unauthorized`. A smoke test
        asserting a code nothing raises fails on a perfectly healthy stack, which is the
        worst kind of failure there is. So the codes are compared against the classes.
        """
        known = _error_codes()
        text = source()
        expected = {
            call.args[2].value
            for call in ast.walk(ast.parse(text))
            if isinstance(call, ast.Call)
            and getattr(call.func, "id", "") == "expect_error"
            and len(call.args) >= 3
            and isinstance(call.args[2], ast.Constant)
        }
        assert expected, "no expect_error call sites were found — has the helper been renamed?"
        assert expected <= known, f"smoke expects codes nothing raises: {expected - known}"


class TestTheMachineSpecificRules:
    """The two that have already cost a ticket elsewhere in this repository."""

    def test_every_compose_exec_disables_the_tty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without `-T` these hang forever in any non-interactive shell — i.e. always.

        `scripts/_common` carries this rule for the shell halves and
        `test_repo_scripts.py` enforces it there; this is the same rule for the one place
        that builds a `compose exec` in Python instead.
        """
        seen: list[list[str]] = []
        monkeypatch.setattr(
            smoke, "run", lambda argv, **_: seen.append(list(argv)) or smoke.Ran(0, "", "")
        )
        smoke.compose_exec("web", "sh", "-c", "true", timeout=1.0, env={"A": "b"})
        argv = seen[0]
        assert argv[:2] == ["docker", "compose"]
        assert argv[argv.index("exec") + 1] == "-T"

    def test_the_registration_body_is_one_the_schema_accepts(self) -> None:
        """Offline, against the real `UserCreate` — the whole body, not just the password.

        Found by running it: the first spelling used `@anvex.invalid`, and
        `email-validator` refuses `.invalid` as a special-use name. That is a 422 eight
        steps into a run, with a hint about the password policy pointing at the wrong thing.
        `example.com` is IANA-reserved for exactly this and is not on the special-use list.
        """
        body = smoke.account("smoke0123456789")
        created = UserCreate.model_validate(body)
        assert created.username == "smoke0123456789"
        assert str(created.email).endswith(smoke.EMAIL_DOMAIN)

    def test_the_password_satisfies_the_policy_it_will_be_registered_against(self) -> None:
        """ANV-43's rules are real. A smoke run that used `password` would 422 at step 9.

        Compared against the domain rules themselves rather than against a copy, so a
        tightened policy fails here — offline, in a second — instead of eight steps into a
        run that has already built two images.
        """
        assert failed_rules(smoke.PASSWORD) == ()

    def test_the_month_is_a_closed_one(self) -> None:
        """`--live-vendor` asks for an explicit month, so it must be one that has ended."""
        asked = Month.parse(smoke.MONTH)
        current = Month.of(dt.datetime.now(tz=dt.UTC).date())
        assert asked.ordinal < current.ordinal


class TestTheDocumentIsHonest:
    """The paragraphs a reader uses to decide what a green run means."""

    @pytest.mark.parametrize(
        "claim",
        [
            "does **not** prove",
            "AWS",
            "MinIO",
            "mail",
            "/portfolio",
            "jsdom",
            "development database",
        ],
    )
    def test_the_checklist_says_what_is_not_proved(self, claim: str) -> None:
        assert claim in checklist()

    def test_the_checklist_states_the_stub_and_the_live_call_separately(self) -> None:
        text = checklist()
        assert "Stubbed" in text
        assert "One real call" in text
        assert "25 calls a day" in text

    def test_the_scheduler_is_never_started(self) -> None:
        """`beat`'s hourly entry publishes a real `ingest_all` fan-out.

        On a machine with a key, that spends quota an hour after a smoke run nobody is
        watching any more — so the smoke starts the `worker` and never the scheduler, and
        the checklist says so rather than leaving it to be noticed.
        """
        assert '"beat"' not in source(), "the smoke driver names `beat` as a compose service"
        assert "beat" in checklist()
        assert "not** started" in checklist() or "deliberately **not** started" in checklist()


def _forbidden(*_: object, **__: object) -> dict[str, object]:
    raise AssertionError("a task was published when the run had not asked to spend quota")


def _error_codes() -> set[str]:
    """Every `code` the domain can put in an error envelope, discovered rather than listed.

    Three sources, and the second is the one that caught this out: `app/domain/errors.py`
    has the seven general codes, `app/domain/auth.py` adds `invalid_token`, `token_expired`
    and `wrong_token_type` on its `TokenError` subclasses, and the middleware's own
    status-to-code map covers what a bare `HTTPException` becomes (`service_unavailable`
    from `/health/ready`, say). A set built from only the first would have "proved" that
    `wrong_token_type` does not exist.
    """
    found: set[str] = set(error_status_codes.values())
    for module in (errors, auth_errors):
        for value in vars(module).values():
            if isinstance(value, type) and issubclass(value, AnvexError):
                code = getattr(value, "code", None)
                if isinstance(code, str) and code:
                    found.add(code)
    return found


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} is not a top-level function in scripts/smoke.py")
