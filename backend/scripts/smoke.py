"""End-to-end smoke verification for the whole Anvex stack (ANV-41).

    uv run python -m scripts.smoke [--clean --yes] [--live-vendor] [--skip-frontend]

Run it through ``scripts/smoke.ps1`` / ``scripts/smoke.sh`` rather than directly — those
wrappers are what resolve ``uv`` and clear a stale ``VIRTUAL_ENV``, and they retarget the
host-side database tooling the way every other script does (``scripts/_common``).

Why this exists, and what it is *not*
-------------------------------------

Every other ticket in this build proved a component in isolation: 3,900-odd backend tests,
922 frontend tests, a green CI. **None of them proves the pieces work together**, because
each replaces its neighbours with a fixture. This program is the
one thing in the repository that runs the real containers, the real migrations, the real
Postgres, the real broker and the real built bundle, in the order a developer meets them.

It is deliberately **not** a test suite. There are no fixtures, nothing is rolled back, and
it writes to the development database on purpose — a smoke test that isolates itself from
the environment is testing the isolation.

The rule every step obeys
-------------------------

**A failure names the step, what was expected, and what was seen.** A smoke script whose
failure mode is a non-zero exit is nearly useless at two in the morning, so every check
raises :class:`SmokeFailure` with all three, plus a hint pointing at the thing that is
usually actually wrong. ``backend/docs/runbook.md``'s "it will not come up" table is where
those hints come from, and where a new one belongs.

The vendor leg, and the quota it spends
---------------------------------------

:data:`SYMBOL`'s ingest is the one step that could reach a third party. AlphaVantage's free
tier allows about **25 calls a day**, shared by whoever holds the key, so:

* **the live call is opt-in** — ``--live-vendor``, off by default, and
  ``tests/unit/test_smoke.py`` asserts that default rather than trusting it;
* even opted in it is **one symbol, one explicit month, one call**, with retries disabled
  (:data:`LIVE_RETRY`) so a vendor 5xx cannot turn one call into three;
* the default path stubs the vendor at the ``app/clients/base.py`` transport seam
  (:func:`stub_transport`) and says so in the output, because a stubbed leg reported as a
  live one is worse than no smoke test at all.

Nothing here prints, logs or stores a credential. The stub authenticates with
:data:`STUB_API_KEY`, a string invented in this file, and asserts the client sent it — so
the auth wiring is exercised without a real key existing anywhere in the run.

Where each step runs
--------------------

Three places, and the choice is not arbitrary:

* **On the host, over the published ports** — every HTTP assertion. That is where a
  developer's browser is, so it is where the API's CORS configuration and port publication
  are observable at all.
* **On the host, against the database** — ``alembic``, the seed, the fixture security and
  the stubbed ingest, through the ``POSTGRES_HOST``/``POSTGRES_PORT`` the wrapper exported.
  The same seam ``scripts/migrate`` and ``scripts/seed`` use, so this proves the documented
  developer path rather than a second one invented here.
* **Inside the ``api`` container** — publishing a Celery task and reading its result.
  ``CELERY_BROKER_URL`` names ``redis``, a compose service, and there is deliberately **no
  host translation for it** the way there is for Postgres: a producer belongs inside the
  network, which is also where the real one lives.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import subprocess
import sys
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

import httpx
from pydantic import SecretStr

from app.clients.alphavantage import (
    META_DATA_KEY,
    META_SYMBOL_KEY,
    META_TIMEZONE_KEY,
    QUERY_PATH,
    AlphaVantageClient,
    IntradayInterval,
    time_series_key,
)
from app.clients.base import RetryPolicy
from app.db.engine import dispose_engine
from app.db.session import get_session
from app.repos.stock import stock_repo
from app.services.ingest import IngestService
from app.settings import REPO_ROOT, Settings, get_settings

# ---------------------------------------------------------------------------------------
# Exit codes and constants
# ---------------------------------------------------------------------------------------

EXIT_OK: Final[int] = 0
#: A step failed. The output names which one.
EXIT_FAILED: Final[int] = 1
#: The run could not start — no Docker, no `.env`, a refused confirmation.
EXIT_PRECONDITION: Final[int] = 2

#: The account the run registers. Suffixed with a fresh id every time, because "register a
#: user" is only a real assertion the first time and this program is meant to be re-run.
USERNAME_PREFIX: Final[str] = "smoke"

#: Where the smoke account's address points. `example.com` is IANA-reserved for exactly this
#: and can never be delivered to — and, unlike the more obvious `.invalid`, it is not on
#: `email-validator`'s special-use list, so `EmailStr` accepts it. A smoke run that could not
#: register an account because of its own fake address would be a 422 with a very confusing
#: hint attached; `tests/unit/test_smoke.py` builds a real `UserCreate` to keep it from
#: coming back.
EMAIL_DOMAIN: Final[str] = "smoke.example.com"

#: ANV-43's password policy is real, and the suites standardised on this value. A smoke run
#: that used `password` would fail at registration with a 422 and teach nobody anything.
PASSWORD: Final[str] = "Correct-horse-battery1"

#: The security the ingest and the read-back use. A real, liquid symbol so `--live-vendor`
#: has something to fetch; AlphaVantage's own documentation uses it.
SYMBOL: Final[str] = "IBM"
COMPANY: Final[str] = "International Business Machines Corporation"
MARKET: Final[str] = "NYSE"

#: The month ingested. Fixed rather than derived from the clock so two runs on either side
#: of a month boundary fetch the same window, and so `--live-vendor` asks for a month that
#: has certainly closed.
MONTH: Final[str] = "2026-01"

#: The stub's candles, on the first trading day of :data:`MONTH` and inside the 08:00-17:00
#: exchange session `app/domain/ingest.py` filters on. Early in the month on purpose: a
#: later `--live-vendor` run for the same month has a watermark below almost all of the real
#: series, so it still writes something instead of reporting a confusing `written=0`.
STUB_CANDLE_COUNT: Final[int] = 6
STUB_FIRST_BAR: Final[str] = "2026-01-02 09:30:00"
STUB_TIMEZONE: Final[str] = "US/Eastern"

#: The key the stub authenticates with. Invented here, never real, and asserted by
#: :func:`stub_transport` — so the credential path is exercised in a run where no credential
#: exists. It is a literal in a public repository precisely because it can never be one.
STUB_API_KEY: Final[str] = "smoke-stub-not-a-real-key"

#: Retry disabled for the live call. The base's default would turn one vendor 5xx into three
#: requests against a 25-a-day quota.
LIVE_RETRY: Final[RetryPolicy] = RetryPolicy(attempts=1, rate_limited_attempts=1)

#: The origin the API is configured to allow, and the one the dev server serves from.
BROWSER_ORIGIN: Final[str] = "http://localhost:5173"

#: How long each waiting step will wait. `compose up` is the long one because a clone that
#: has never built the image is building it here.
COMPOSE_UP_TIMEOUT: Final[float] = 900.0
HEALTH_TIMEOUT: Final[float] = 180.0
CELERY_TIMEOUT: Final[float] = 120.0
FRONTEND_BUILD_TIMEOUT: Final[float] = 600.0
COLD_LOAD_TIMEOUT: Final[float] = 120.0
HTTP_TIMEOUT: Final[float] = 30.0

#: Services `up core` starts, in `docker-compose.yml`'s own names.
CORE_SERVICES: Final[tuple[str, ...]] = ("db", "redis", "minio", "minio-init", "api")

#: The ones that must report healthy before anything else is attempted. `minio-init` is a
#: one-shot that exits 0, and `api` has its own `/health` probe two steps later.
HEALTHY_SERVICES: Final[tuple[str, ...]] = ("db", "redis", "minio")

#: Where the cold-load harness lives inside the `web` container. `/tmp` so nothing is
#: written back through the bind mount into the source tree, and `.cjs` so Node ignores
#: `package.json`'s `"type": "module"` and reads it as CommonJS — which is what `jsdom` is.
COLD_LOAD_PATH: Final[str] = "/tmp/anvex-smoke-cold-load.cjs"

#: Where the smoke's bundle is built. **Container-local, and that is not a preference.**
#: `web` runs as uid 1000 and compose bind-mounts the host's `frontend/` over `/app`. On
#: Windows that mount ignores ownership; on Linux — a CI runner, where the checkout belongs
#: to uid 1001 — creating `/app/dist` is `EACCES`, and ANV-41's first CI run got to step 19
#: of 20 and died exactly there. Building into `/tmp` also means a smoke run never clobbers
#: the `dist/` a developer is looking at. Same argument as `cacheDir: /tmp/anvex-vite` in
#: `vite.config.js` and `beat --schedule /tmp/…` in `docker-compose.yml`: **nothing is
#: written back through the bind mount.**
SMOKE_DIST: Final[str] = "/tmp/anvex-smoke-dist"


# ---------------------------------------------------------------------------------------
# Failure reporting
# ---------------------------------------------------------------------------------------


class SmokeFailure(Exception):
    """One step's failure, in the three parts a reader at 2am needs.

    Never raised with a bare message: ``expected`` is what the step was asserting,
    ``observed`` is what it actually got, and ``hint`` names the thing that is usually
    really wrong. ``tests/unit/test_smoke.py`` asserts every raise site supplies all three,
    because "AssertionError" in a smoke log costs an hour.
    """

    def __init__(self, expected: str, observed: str, hint: str = "") -> None:
        super().__init__(expected)
        self.expected = expected
        self.observed = observed
        self.hint = hint


class Precondition(Exception):
    """The run cannot start. Distinct from a failure: nothing has been proved either way."""


# ---------------------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------------------
#
# `print` is this program's output, not logging — the same distinction
# `scripts/seed_politicians.py` draws. `CLAUDE.md` §4's "no bare print" is about `app/`.


def out(message: str = "") -> None:
    print(message, flush=True)


def note(message: str) -> None:
    out(f"   {message}")


# ---------------------------------------------------------------------------------------
# Running things
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Ran:
    """A finished subprocess, with both streams kept as text."""

    code: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        """Both streams, for a failure message. Trimmed — a 900-line build log helps nobody."""
        joined = f"{self.stdout}\n{self.stderr}".strip()
        lines = joined.splitlines()
        if len(lines) <= 25:
            return joined
        return "\n".join(["…", *lines[-25:]])


def run(
    argv: Sequence[str], *, timeout: float, cwd: Path | None = None, stdin: str | None = None
) -> Ran:
    """One subprocess, captured. Never raises on a non-zero exit — the caller decides.

    ``stdin`` is how a script gets *into* a container without being committed to the source
    tree: ``compose exec -T web sh -c 'cat > /tmp/x.cjs && node /tmp/x.cjs'`` with the file
    on standard input. `-T` is what makes that stream a pipe rather than a terminal.
    """
    try:
        done = subprocess.run(
            list(argv),
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(cwd or REPO_ROOT),
        )
    except subprocess.TimeoutExpired as expired:
        raise SmokeFailure(
            expected=f"`{' '.join(argv[:4])} …` to finish within {timeout:.0f}s",
            observed="it was still running when the deadline passed",
            hint="the first `docker compose up` on a fresh clone builds two images; "
            "re-run and it will be cached, or watch it with `scripts/logs`",
        ) from expired
    except FileNotFoundError as missing:
        raise Precondition(f"`{argv[0]}` is not on PATH: {missing}") from missing
    return Ran(done.returncode, done.stdout, done.stderr)


def compose(*arguments: str, timeout: float = 120.0, stdin: str | None = None) -> Ran:
    """`docker compose …` from the repository root."""
    return run(["docker", "compose", *arguments], timeout=timeout, stdin=stdin)


def compose_exec(
    service: str,
    *command: str,
    timeout: float,
    env: Mapping[str, str] | None = None,
    stdin: str | None = None,
) -> Ran:
    """`docker compose exec` into a running container.

    ``-T`` is **required**, not tidiness: without it the call hangs forever in any
    non-interactive shell, which is every shell this program will ever run in
    (``scripts/_common``'s ``run_web`` carries the same comment, and
    ``tests/unit/test_smoke.py`` asserts this function keeps the flag).
    """
    flags: list[str] = []
    for key, value in (env or {}).items():
        flags += ["-e", f"{key}={value}"]
    return compose(
        "--profile",
        "celery",
        "--profile",
        "frontend",
        "exec",
        "-T",
        *flags,
        service,
        *command,
        timeout=timeout,
        stdin=stdin,
    )


def in_a_loop[T](work: Callable[[], Awaitable[T]]) -> T:
    """Run one coroutine and **dispose the engine before the loop closes**.

    Found by running this program: two steps each called ``asyncio.run``, and the second
    died inside asyncpg with ``Event loop is closed``. ``app/db/engine.py`` keeps a
    module-level engine, so the pooled connections the first ``asyncio.run`` opened outlive
    the loop they were bound to and the next loop inherits corpses.

    This is exactly the rule ``app/jobs/base.py`` already follows for a Celery task, for the
    same reason and with the same fix: dispose *inside* the task's own loop. A host-side
    script that opens a session per step is in precisely that position.
    """

    async def main() -> T:
        try:
            return await work()
        finally:
            await dispose_engine()

    return asyncio.run(main())


def run_uv(*arguments: str, timeout: float) -> Ran:
    """A backend command through the same interpreter this program is running under.

    ``sys.executable`` rather than a second ``uv run``: uv already resolved the environment
    to start this process, and re-resolving it from inside would be a different answer than
    the one the wrapper chose.
    """
    return run([sys.executable, *arguments], timeout=timeout, cwd=REPO_ROOT / "backend")


# ---------------------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------------------


def envelope(response: httpx.Response) -> tuple[str, str]:
    """``(code, message)`` from Anvex's error envelope, or ``("", body)``.

    ``CLAUDE.md`` §4: every error is ``{"error": {code, message, details, request_id}}`` and
    **callers branch on ``code``**, never on the message. Every assertion below that expects
    a failure names the code for that reason.
    """
    try:
        payload = response.json()
    except ValueError:
        return "", response.text[:200]
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        return str(error.get("code", "")), str(error.get("message", ""))
    # Not an envelope, so it is a **success** body arriving where a failure was expected —
    # and on this API that is very often a `TokenPair`. Its keys are the whole diagnostic
    # value; its values are credentials. ANV-41 dumped one into a failure report exactly
    # once, which is why this reports the shape and never the content.
    if isinstance(payload, dict):
        return "", f"a JSON object with keys: {', '.join(sorted(payload))}"
    return "", f"a JSON {type(payload).__name__}"


def expect(
    response: httpx.Response,
    status: int,
    *,
    what: str,
    hint: str = "",
) -> Any:
    """Assert a status code and return the decoded body, or fail naming both."""
    if response.status_code != status:
        code, message = envelope(response)
        seen = f"{response.status_code}"
        if code:
            seen += f" {code}: {message}"
        elif message:
            seen += f" {message}"
        raise SmokeFailure(
            expected=f"{what} to answer {status}",
            observed=seen,
            hint=hint,
        )
    if response.status_code == 204 or not response.content:
        return None
    return response.json()


def expect_error(
    response: httpx.Response,
    status: int,
    code: str,
    *,
    what: str,
) -> None:
    """Assert a failure by **code**, not by message."""
    seen_code, message = envelope(response)
    if response.status_code != status or seen_code != code:
        raise SmokeFailure(
            expected=f"{what} to answer {status} with error code `{code}`",
            observed=f"{response.status_code} `{seen_code}`: {message}",
            hint="the error envelope is `{'error': {code, message, details, request_id}}` "
            "and every caller branches on `code` (CLAUDE.md §4)",
        )


def page_items(payload: Any, *, what: str) -> list[Any]:
    """The ``items`` of a ``Page[T]``, asserting the envelope is one."""
    if not isinstance(payload, dict) or "items" not in payload or "total" not in payload:
        raise SmokeFailure(
            expected=f"{what} to be a `Page[T]` — {{items, total, limit, offset, has_more}}",
            observed=f"{type(payload).__name__}: {json.dumps(payload)[:200]}",
            hint="every list endpoint is paginated; a bare array means the route changed",
        )
    return list(payload["items"])


# ---------------------------------------------------------------------------------------
# The vendor stub
# ---------------------------------------------------------------------------------------


def stub_payload(*, symbol: str, first_bar: str, count: int) -> dict[str, Any]:
    """An AlphaVantage intraday body, in the vendor's own wire vocabulary.

    Built from ``app/clients/alphavantage.py``'s own key constants rather than retyped
    strings, so a rename there breaks this loudly instead of turning the stub into a
    "malformed response" nobody can explain.
    """
    start = dt.datetime.strptime(first_bar, "%Y-%m-%d %H:%M:%S")
    series: dict[str, dict[str, str]] = {}
    for step in range(count):
        moment = start + dt.timedelta(minutes=5 * step)
        base = Decimal("186.0000") + Decimal(step) / 10
        series[moment.strftime("%Y-%m-%d %H:%M:%S")] = {
            "1. open": f"{base:.4f}",
            "2. high": f"{base + Decimal('0.5000'):.4f}",
            "3. low": f"{base - Decimal('0.2500'):.4f}",
            "4. close": f"{base + Decimal('0.1250'):.4f}",
            "5. volume": str(1000 + step),
        }
    return {
        META_DATA_KEY: {META_SYMBOL_KEY: symbol, META_TIMEZONE_KEY: STUB_TIMEZONE},
        time_series_key(IntradayInterval.FIVE_MINUTES): series,
    }


def stub_transport(*, symbol: str, month: str, count: int) -> httpx.MockTransport:
    """The vendor, replaced at ``BaseHTTPClient``'s one injection seam.

    It **asserts the request** as well as answering it: path, function, symbol, interval,
    month and the API key. That is what makes the stubbed leg worth running — a stub that
    answers anything proves only that the parser works, while this one proves the client
    built the exact call ``--live-vendor`` would have sent.
    """

    def handle(request: httpx.Request) -> httpx.Response:
        query = dict(request.url.params)
        wrong = {
            "path": (request.url.path, QUERY_PATH),
            "symbol": (query.get("symbol"), symbol),
            "month": (query.get("month"), month),
            "interval": (query.get("interval"), str(IntradayInterval.FIVE_MINUTES)),
            "apikey": (query.get("apikey"), STUB_API_KEY),
        }
        for name, (seen, wanted) in wrong.items():
            if seen != wanted:
                raise SmokeFailure(
                    expected=f"the AlphaVantage client to send {name}={wanted!r}",
                    observed=f"it sent {name}={seen!r}",
                    hint="app/clients/alphavantage.py builds this request; the stub in "
                    "backend/scripts/smoke.py asserts it",
                )
        return httpx.Response(
            200, json=stub_payload(symbol=symbol, first_bar=STUB_FIRST_BAR, count=count)
        )

    return httpx.MockTransport(handle)


# ---------------------------------------------------------------------------------------
# The in-container Celery producer
# ---------------------------------------------------------------------------------------

#: Published from inside the compose network — see the module docstring.
#:
#: It reads the **raw** result-backend entry rather than an ``AsyncResult.result``, because
#: Celery reconstructs a stored exception by calling its class with the stored message, and
#: ``ExternalServiceError(vendor, message, details=…)`` does not accept that call — so the
#: reconstruction silently degrades to a plain ``Exception`` and the failure's real type is
#: lost. The raw entry carries ``exc_type`` unambiguously, which is the thing worth
#: asserting.
PUBLISH_SOURCE: Final[str] = """
import json, sys
from app.jobs.celery_app import celery_app

name, kwargs, timeout = sys.argv[1], json.loads(sys.argv[2]), float(sys.argv[3])
async_result = celery_app.send_task(name, kwargs=kwargs)
try:
    async_result.get(timeout=timeout, propagate=False)
except Exception:
    pass

backend = celery_app.backend
report = {"id": async_result.id, "state": async_result.state, "result": None}
try:
    raw = backend.get(backend.get_key_for_task(async_result.id))
    stored = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
    report["state"] = stored.get("status", report["state"])
    report["result"] = stored.get("result")
except Exception as error:
    report["result"] = {"unreadable": repr(error)}
print("ANVEX-TASK " + json.dumps(report))
"""


def publish_task(name: str, kwargs: Mapping[str, Any], *, timeout: float) -> dict[str, Any]:
    """Send one task from inside the ``api`` container and wait for a terminal state."""
    done = compose_exec(
        "api",
        "python",
        "-c",
        PUBLISH_SOURCE,
        name,
        json.dumps(dict(kwargs)),
        str(timeout),
        timeout=timeout + 60.0,
    )
    for line in done.stdout.splitlines():
        if line.startswith("ANVEX-TASK "):
            return json.loads(line[len("ANVEX-TASK ") :])
    raise SmokeFailure(
        expected=f"the `api` container to publish `{name}` and read its result back",
        observed=f"exit {done.code}, no result line:\n{done.output}",
        hint="the producer runs inside the network because CELERY_BROKER_URL names the "
        "`redis` service; if the container is not up, `scripts/up core` first",
    )


# ---------------------------------------------------------------------------------------
# The cold-load harness (jsdom, inside the `web` container)
# ---------------------------------------------------------------------------------------
#
# Three jsdom facts, each of which cost a ticket to find (ANV-36):
#
#   * jsdom 25 defines neither `fetch` nor `Response`, and TanStack Router's redirect
#     machinery does `instanceof Response` — so **every** route load dies with
#     `ReferenceError: Response is not defined` unless Node's globals are handed to the
#     window. This is the single most load-bearing block below.
#   * jsdom has no `ResizeObserver`, so the research desktop measures 0x0 and renders
#     **empty**. Asserting a window would fail against a perfectly healthy app; the
#     securities panel is unmeasured and is what the assertion is on.
#   * the built bundle is evaluated rather than fetched. `resources: 'usable'` would make
#     jsdom load `/assets/index-*.js` over HTTP from the document's origin, which is the
#     API — which does not serve it.

COLD_LOAD_SOURCE: Final[str] = r"""
const fs = require('fs')
const path = require('path')
const { JSDOM, VirtualConsole } = require('jsdom')

const dist = process.env.ANVEX_SMOKE_DIST
const origin = process.env.ANVEX_SMOKE_ORIGIN
const route = process.env.ANVEX_SMOKE_ROUTE
const ticker = process.env.ANVEX_SMOKE_TICKER
const refresh = process.env.ANVEX_SMOKE_REFRESH
const deadlineMs = Number(process.env.ANVEX_SMOKE_TIMEOUT_MS || '60000')

const report = (ok, detail, extra) =>
  console.log('ANVEX-COLDLOAD ' + JSON.stringify({ ok, detail, ...(extra || {}) }))

const indexHtml = path.join(dist, 'index.html')
if (!fs.existsSync(indexHtml)) {
  report(false, 'no built bundle at ' + indexHtml)
  process.exit(0)
}
const bundles = fs
  .readdirSync(path.join(dist, 'assets'))
  .filter((name) => name.endsWith('.js'))
  .map((name) => path.join(dist, 'assets', name))
if (bundles.length !== 1) {
  report(false, 'expected exactly one entry bundle, found ' + bundles.length)
  process.exit(0)
}

const consoleErrors = []
const virtualConsole = new VirtualConsole()
virtualConsole.on('jsdomError', (e) => consoleErrors.push(String(e && e.message)))
virtualConsole.on('error', (...args) => consoleErrors.push(args.map(String).join(' ')))

const dom = new JSDOM(fs.readFileSync(indexHtml, 'utf8'), {
  url: origin + route,
  runScripts: 'outside-only',
  pretendToBeVisual: true,
  virtualConsole,
})
const { window } = dom

// The globals jsdom does not define. `Response` is the one that matters.
for (const name of ['fetch', 'Response', 'Request', 'Headers', 'FormData', 'AbortController',
                    'AbortSignal', 'ReadableStream', 'TextEncoder', 'TextDecoder']) {
  if (typeof window[name] === 'undefined' && typeof globalThis[name] !== 'undefined') {
    window[name] = globalThis[name]
  }
}
if (typeof window.crypto === 'undefined') window.crypto = require('node:crypto').webcrypto
if (typeof window.matchMedia === 'undefined') {
  window.matchMedia = (query) => ({
    matches: false, media: query, onchange: null,
    addListener() {}, removeListener() {}, addEventListener() {},
    removeEventListener() {}, dispatchEvent() { return false },
  })
}
// Deliberately inert, and deliberately never firing: a stub that reported a fabricated box
// would make the desktop render windows jsdom cannot lay out (ANV-33's rule).
for (const name of ['ResizeObserver', 'IntersectionObserver']) {
  if (typeof window[name] === 'undefined') {
    window[name] = class { observe() {} unobserve() {} disconnect() {} }
  }
}
window.scrollTo = () => {}

// The cold start: a refresh token from a previous session and nothing else. No access
// token, so the first protected call goes out bare, 401s, and the interceptor rotates.
window.localStorage.setItem('anvex.refresh_token', refresh)

try {
  window.eval(fs.readFileSync(bundles[0], 'utf8'))
} catch (error) {
  report(false, 'the bundle threw while evaluating: ' + String(error && error.message),
         { consoleErrors })
  process.exit(0)
}

const started = Date.now()
const tick = () => {
  const doc = window.document
  const routeEl = doc.querySelector('[data-testid="route-research"]')
  const panel = doc.querySelector('[data-testid="securities-panel"]')
  const count = doc.querySelector('[data-testid="securities-count"]')
  const failed = doc.querySelector('[data-testid="securities-error"]')
  const errorText = failed ? failed.textContent.trim() : ''

  if (errorText) {
    report(false, 'the securities panel reported an error: ' + errorText,
           { route: Boolean(routeEl), panel: Boolean(panel), consoleErrors })
    return
  }
  if (routeEl && panel && count) {
    const tickers = [...doc.querySelectorAll('[data-testid="securities-panel"] li')]
      .map((li) => li.textContent.trim())
    report(true, count.textContent.trim(), {
      path: window.location.pathname,
      refreshRotated: window.localStorage.getItem('anvex.refresh_token') !== refresh,
      sawTicker: tickers.some((text) => text.includes(ticker)),
      consoleErrors,
    })
    return
  }
  if (Date.now() - started > deadlineMs) {
    report(false, 'the securities list never arrived', {
      path: window.location.pathname,
      route: Boolean(routeEl),
      panel: Boolean(panel),
      body: doc.body ? doc.body.textContent.trim().slice(0, 300) : '',
      consoleErrors,
    })
    return
  }
  setTimeout(tick, 200)
}
setTimeout(tick, 200)
"""


# ---------------------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------------------


@dataclass
class Options:
    clean: bool = False
    yes: bool = False
    live_vendor: bool = False
    skip_frontend: bool = False


@dataclass
class Context:
    """What one step leaves for the next. Every value here was proved by a step above it."""

    options: Options
    settings: Settings
    api: str
    web: str
    username: str = ""
    access_token: str = ""
    refresh_token: str = ""
    stock_id: str = ""
    vendor_key_present: bool = False
    vendor_leg: str = "stubbed"
    notes: list[str] = field(default_factory=list)

    def bearer(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}


@dataclass(frozen=True, slots=True)
class Step:
    """One numbered check. ``run`` returns the one line printed on success."""

    id: str
    title: str
    run: Callable[[Context], str]
    #: Skipped by `--skip-frontend`. Nothing else is optional: a smoke test with a menu is
    #: a smoke test nobody can quote a passing run of.
    frontend: bool = False


# ----- the steps ------------------------------------------------------------------------


def resolve_vendor_leg(context: Context) -> str:
    """Decide, once, whether the vendor is stubbed or live — and refuse the impossible case.

    **The key is read as a boolean and never as a value.** ``is_configured`` is exactly that
    boolean, which is why it is public on the client: a caller may reasonably ask "is this
    feature available here" without provoking a failure, and this program may reasonably say
    so in its output without ever holding the plaintext.

    Asking for a live call with no key fails *here*, in the first step, rather than eight
    steps later on a run that has already built two images.
    """
    context.vendor_key_present = AlphaVantageClient(context.settings).is_configured
    if context.options.live_vendor and not context.vendor_key_present:
        raise SmokeFailure(
            expected="a vendor key to be configured, because --live-vendor was passed",
            observed="the key is blank",
            hint="drop --live-vendor to run the vendor leg against the stub instead",
        )
    context.vendor_leg = "live" if context.options.live_vendor else "stubbed"
    return "configured" if context.vendor_key_present else "not configured"


def step_preflight(context: Context) -> str:
    version = run(["docker", "version", "--format", "{{.Server.Version}}"], timeout=60.0)
    if version.code != 0:
        raise SmokeFailure(
            expected="the Docker daemon to answer `docker version`",
            observed=version.output or f"exit {version.code}",
            hint="Docker Desktop is stopped. Start it and re-run — every step below needs it",
        )
    if not (REPO_ROOT / ".env").is_file():
        raise SmokeFailure(
            expected="a `.env` at the repository root",
            observed="there is none",
            hint="`cp .env.example .env` — one file configures every service (CLAUDE.md §2)",
        )
    key = resolve_vendor_leg(context)
    return f"docker {version.stdout.strip()}, .env present, AlphaVantage key {key}"


def step_clean(context: Context) -> str:
    if not context.options.clean:
        return "kept the existing stack (pass --clean --yes to prove a boot from nothing)"
    down = compose(
        "--profile", "celery", "--profile", "frontend", "down", "--volumes", timeout=300.0
    )
    if down.code != 0:
        raise SmokeFailure(
            expected="`docker compose down --volumes` to remove the stack and its volumes",
            observed=down.output,
            hint="a container that will not stop is usually one whose image was removed "
            "underneath it; `docker compose rm -f` then retry",
        )
    return "stack and named volumes destroyed — this run boots from nothing"


def step_compose_up(context: Context) -> str:
    started = compose("up", "-d", *CORE_SERVICES, timeout=COMPOSE_UP_TIMEOUT)
    if started.code != 0:
        raise SmokeFailure(
            expected=f"`docker compose up -d {' '.join(CORE_SERVICES)}` to succeed",
            observed=started.output,
            hint="a port conflict is the usual cause — a natively installed Postgres owns "
            "5432, which is why compose publishes 5442 (see .env.example)",
        )
    deadline = time.monotonic() + HEALTH_TIMEOUT
    unhealthy: dict[str, str] = {}
    while time.monotonic() < deadline:
        unhealthy = compose_unhealthy()
        if not unhealthy:
            return f"{', '.join(CORE_SERVICES)} up; {', '.join(HEALTHY_SERVICES)} healthy"
        time.sleep(2.0)
    raise SmokeFailure(
        expected=f"{', '.join(HEALTHY_SERVICES)} to report healthy within {HEALTH_TIMEOUT:.0f}s",
        observed=", ".join(f"{name}={state}" for name, state in sorted(unhealthy.items())),
        hint="`scripts/logs db` — a database whose volume was written by a different "
        "Postgres major version refuses to start and says so in its first ten lines",
    )


def compose_unhealthy() -> dict[str, str]:
    """The core services that are not yet healthy, as ``{name: state}``. Empty means ready."""
    listed = compose("ps", "--format", "json", timeout=60.0)
    states: dict[str, str] = {}
    for line in listed.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        rows = row if isinstance(row, list) else [row]
        for entry in rows:
            states[str(entry.get("Service"))] = str(entry.get("Health") or entry.get("State"))
    return {
        name: states.get(name, "absent")
        for name in HEALTHY_SERVICES
        if states.get(name) != "healthy"
    }


def step_migrate(context: Context) -> str:
    done = run_uv("-m", "alembic", "upgrade", "head", timeout=300.0)
    if done.code != 0:
        raise SmokeFailure(
            expected="`alembic upgrade head` to bring the schema to the latest revision",
            observed=done.output,
            hint="this runs on the *host*, where POSTGRES_HOST=db does not resolve — "
            "`scripts/smoke` exports localhost and the published port for it "
            "(scripts/_common's use_host_database)",
        )
    return "schema at head"


def step_seed(context: Context) -> str:
    done = run_uv("-m", "scripts.seed_politicians", timeout=300.0)
    if done.code != 0:
        raise SmokeFailure(
            expected="the politician roster to load",
            observed=done.output,
            hint="exit 1 means `app/data/politicians.json` is unusable and the message "
            "names the row; exit 2 means the database refused",
        )
    return (done.stdout.strip().splitlines() or ["seeded"])[-1]


def step_api_health(context: Context) -> str:
    deadline = time.monotonic() + HEALTH_TIMEOUT
    last = ""
    while time.monotonic() < deadline:
        try:
            with httpx.Client(base_url=context.api, timeout=HTTP_TIMEOUT) as client:
                live = client.get("/health")
                ready = client.get("/health/ready")
            if live.status_code == 200 and ready.status_code == 200:
                return f"/health {live.json()}, /health/ready {ready.json()}"
            last = (
                f"/health {live.status_code}, /health/ready {ready.status_code} {ready.text[:120]}"
            )
        except httpx.HTTPError as error:
            last = f"{type(error).__name__}: {error}"
        time.sleep(2.0)
    raise SmokeFailure(
        expected=f"GET {context.api}/health and /health/ready to answer 200",
        observed=last,
        hint="a 503 from /health/ready is the API up but unable to reach Postgres by "
        "service name; a connection error is the published port (API_HOST_PORT)",
    )


def step_cors(context: Context) -> str:
    with httpx.Client(base_url=context.api, timeout=HTTP_TIMEOUT) as client:
        response = client.request(
            "OPTIONS",
            "/v1/stocks",
            headers={
                "Origin": BROWSER_ORIGIN,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
    allowed = response.headers.get("access-control-allow-origin", "")
    if allowed != BROWSER_ORIGIN:
        raise SmokeFailure(
            expected=f"the preflight for {BROWSER_ORIGIN} to be allowed",
            observed=f"{response.status_code}, access-control-allow-origin={allowed!r}",
            hint="API_CORS_ORIGINS in `.env` is what the browser is checked against; "
            "without the dev server's origin every call from the app fails in the browser "
            "and nowhere else",
        )
    return f"preflight from {BROWSER_ORIGIN} allowed"


def step_reference_data(context: Context) -> str:
    """The rows `seed` wrote, read back through the API.

    Deliberately after the auth steps rather than beside `seed`: **every `/v1` route is
    behind the guard**, reference data included, so reading this needs a bearer token. That
    is a fact worth a step discovering rather than a comment claiming.
    """
    with httpx.Client(base_url=context.api, timeout=HTTP_TIMEOUT) as client:
        payload = expect(
            client.get("/v1/politicians", params={"limit": 1}, headers=context.bearer()),
            200,
            what="GET /v1/politicians",
            hint="a 401 means the bearer was refused; a 500 means the seed wrote nothing",
        )
    items = page_items(payload, what="GET /v1/politicians")
    if not items or payload["total"] < 1:
        raise SmokeFailure(
            expected="the seeded roster to be readable over HTTP",
            observed=f"total={payload.get('total')}, items={len(items)}",
            hint="`seed` reported success but the API sees an empty table — the two are "
            "pointed at different databases (POSTGRES_HOST on the host vs in the container)",
        )
    return f"{payload['total']} legislators readable over HTTP"


def account(username: str) -> dict[str, str]:
    """The registration body. Its own function so a test can validate it against the schema."""
    return {
        "username": username,
        "email": f"{username}@{EMAIL_DOMAIN}",
        "password": PASSWORD,
    }


def step_register(context: Context) -> str:
    context.username = f"{USERNAME_PREFIX}{uuid.uuid4().hex[:10]}"
    body = account(context.username)
    with httpx.Client(base_url=context.api, timeout=HTTP_TIMEOUT) as client:
        created = expect(
            client.post("/v1/users", json=body),
            201,
            what="POST /v1/users",
            hint="a 422 naming `failed_rules` is the ANV-43 password policy; one naming "
            "`email` is the address. Both are asserted offline by tests/unit/test_smoke.py, "
            "so a 422 here means the schema changed",
        )
        again = client.post("/v1/users", json=body)
    expect_error(again, 409, "conflict", what="registering the same username twice")
    if created.get("username") != context.username:
        raise SmokeFailure(
            expected=f"the created account to be named {context.username}",
            observed=json.dumps(created)[:200],
            hint="",
        )
    return f"registered {context.username}; a second attempt is 409 `conflict`"


def step_login(context: Context) -> str:
    with httpx.Client(base_url=context.api, timeout=HTTP_TIMEOUT) as client:
        pair = expect(
            client.post(
                "/v1/auth/login",
                data={"username": context.username, "password": PASSWORD},
            ),
            200,
            what="POST /v1/auth/login",
            hint="the login route takes an OAuth2 **form** body, not JSON — a 422 here is "
            "usually a caller sending `json=`",
        )
        refused = client.post(
            "/v1/auth/login",
            data={"username": context.username, "password": PASSWORD + "-wrong"},
        )
    expect_error(refused, 401, "unauthorized", what="logging in with a wrong password")
    for key in ("access_token", "refresh_token"):
        if not pair.get(key):
            raise SmokeFailure(
                expected=f"the token pair to carry a {key}",
                observed=", ".join(sorted(pair)),
                hint="",
            )
    if pair.get("token_type") != "bearer":
        raise SmokeFailure(
            expected="token_type `bearer`",
            observed=str(pair.get("token_type")),
            hint="",
        )
    context.access_token = pair["access_token"]
    context.refresh_token = pair["refresh_token"]
    return "signed in; a wrong password is 401 `unauthorized`"


def step_refresh(context: Context) -> str:
    """Exchange a refresh token for a working pair — and **two things deliberately not
    asserted**, each of which this step tried to assert and found untrue.

    1. **The presented token is not revoked.** The first version expected the replay to be
       401, because `docs/architecture.md` said this route "invalidates the token
       presented". It answers 200. These are stateless JWTs and nothing records that one has
       been spent.
    2. **The new refresh token is not necessarily a different string.** The second version
       expected inequality and failed intermittently — a `TokenPair` is
       `{sub, type, iat, exp}` with one-second resolution and **no `jti`**, so a login and a
       rotation inside the same second produce byte-identical tokens.

    One root cause, and it is the missing `jti`: see `TODO(ANV-refresh-revocation)` in
    `app/services/auth.py` and the row in `docs/architecture.md` §6. What is asserted here
    is what is actually true and worth being true — the exchange answers a well-formed pair,
    the pair works, and an *access* token offered in a refresh token's place is refused.
    """
    spent = context.refresh_token
    with httpx.Client(base_url=context.api, timeout=HTTP_TIMEOUT) as client:
        rotated = expect(
            client.post("/v1/auth/refresh", json={"refresh_token": spent}),
            200,
            what="POST /v1/auth/refresh",
            hint="the refresh token goes in a JSON **body**; the old app passed it as a "
            "query parameter and that shape is gone",
        )
        expect_error(
            client.post("/v1/auth/refresh", json={"refresh_token": rotated["access_token"]}),
            401,
            "wrong_token_type",
            what="presenting an **access** token where a refresh token belongs",
        )
    for key in ("access_token", "refresh_token"):
        if not rotated.get(key):
            raise SmokeFailure(
                expected=f"the rotated pair to carry a {key}",
                observed=", ".join(sorted(rotated)),
                hint="the exchange answered 200 with something that is not a `TokenPair`",
            )
    context.access_token = rotated["access_token"]
    context.refresh_token = rotated["refresh_token"]
    with httpx.Client(base_url=context.api, timeout=HTTP_TIMEOUT) as client:
        me = expect(
            client.get("/v1/users/me", headers=context.bearer()),
            200,
            what="GET /v1/users/me with the rotated access token",
        )
    if me.get("username") != context.username:
        raise SmokeFailure(
            expected=f"/v1/users/me to be {context.username}",
            observed=str(me.get("username")),
            hint="",
        )
    return (
        "exchanged for a working pair (the spent token is NOT revoked — architecture.md §6), "
        "an access token is refused where a refresh belongs, /v1/users/me agrees"
    )


async def _ensure_security() -> str:
    async with get_session() as session:
        existing = await stock_repo.get_by_ticker(session, SYMBOL)
        if existing is not None:
            return str(existing.stock_id)
        created = await stock_repo.create(
            session, ticker_symbol=SYMBOL, company=COMPANY, market=MARKET
        )
        await session.commit()
        return str(created.stock_id)


def step_security(context: Context) -> str:
    """The one fixture this run creates.

    There is no `POST /v1/stocks` — the API reads the roster and the ingest fills it, and
    nothing in Anvex creates a security from a vendor response (`app/services/ingest.py`
    says so explicitly). So the row is written through the repository, which is the same
    path a future admin route would take.
    """
    context.stock_id = in_a_loop(_ensure_security)
    with httpx.Client(base_url=context.api, timeout=HTTP_TIMEOUT) as client:
        found = expect(
            client.get("/v1/stocks", params={"search": SYMBOL}, headers=context.bearer()),
            200,
            what=f"GET /v1/stocks?search={SYMBOL}",
            hint="a 401 here means the bearer token was not accepted — the list is behind "
            "the guard, which is exactly what the frontend's cold load exercises",
        )
    tickers = [item["ticker_symbol"] for item in page_items(found, what="GET /v1/stocks")]
    if SYMBOL not in tickers:
        raise SmokeFailure(
            expected=f"{SYMBOL} to be in the searched securities list",
            observed=f"found {tickers}",
            hint="",
        )
    return f"{SYMBOL} present ({context.stock_id}), searchable over HTTP"


async def _ingest(*, live: bool, settings: Settings) -> dict[str, Any]:
    if live:
        client = AlphaVantageClient(settings, retry=LIVE_RETRY)
    else:
        client = AlphaVantageClient(
            settings.model_copy(update={"alphavantage_api_key": SecretStr(STUB_API_KEY)}),
            transport=stub_transport(symbol=SYMBOL, month=MONTH, count=STUB_CANDLE_COUNT),
        )
    async with client, get_session() as session:
        report = await IngestService(session, settings, client=client).ingest_month(
            ticker=SYMBOL, month=MONTH
        )
    return report.as_result()


def step_ingest(context: Context) -> str:
    live = context.options.live_vendor
    try:
        report = in_a_loop(lambda: _ingest(live=live, settings=context.settings))
    except SmokeFailure:
        raise
    except Exception as error:
        raise SmokeFailure(
            expected=f"one {'live' if live else 'stubbed'} ingest of {SYMBOL} {MONTH}",
            observed=f"{type(error).__name__}: {error}",
            hint="a `rate_limited` reason is AlphaVantage's 200-that-means-429 — the free "
            "tier is ~25 calls a day and this run has spent one of them",
        ) from error
    leg = "LIVE — one real AlphaVantage call" if live else "STUBBED at the transport seam"
    context.notes.append(f"vendor leg: {leg}")
    return (
        f"{leg}: fetched={report['fetched']} in_session={report['in_session']} "
        f"fresh={report['fresh']} written={report['written']}"
    )


def step_worker(context: Context) -> str:
    started = compose("--profile", "celery", "up", "-d", "worker", timeout=COMPOSE_UP_TIMEOUT)
    if started.code != 0:
        raise SmokeFailure(
            expected="the Celery worker to start",
            observed=started.output,
            hint="it shares the api image, so a failure here that is not a build failure "
            "is usually Redis: `scripts/logs redis`",
        )
    report = publish_task("jobs.health.ping", {}, timeout=CELERY_TIMEOUT)
    if report["state"] != "SUCCESS":
        raise SmokeFailure(
            expected="`jobs.health.ping` to be consumed and return SUCCESS",
            observed=f"state={report['state']} result={json.dumps(report['result'])[:300]}",
            hint="the heartbeat touches no database and no vendor on purpose, so a failure "
            "here is the plumbing: broker, worker, or result backend",
        )
    result = report["result"] or {}
    return (
        f"broker → worker → result backend: task {report['id'][:8]}… ran on "
        f"{result.get('worker', 'a worker')} (pid {result.get('pid', '?')})"
    )


def step_ingest_task(context: Context) -> str:
    """Publish the real ingest task and watch it reach a terminal state.

    **The quota rule lives here.** ``ingest_symbol`` makes exactly one vendor call, and the
    worker holds the operator's real key — so this step is allowed to publish it in only two
    situations, and neither of them can spend a call the caller did not ask for:

    * ``--live-vendor`` **and** a key present — one deliberate call, expected to succeed;
    * **no key at all** — the client refuses before opening a socket (``not_configured``),
      so the message still proves broker → worker → task → client seam at zero cost, and a
      FAILURE is the *expected* outcome.

    A key present without ``--live-vendor`` is the one case that is skipped, because running
    it would quietly spend somebody's quota.
    """
    if context.vendor_key_present and not context.options.live_vendor:
        context.notes.append(
            "ingest task not published: a key is configured and --live-vendor was not passed"
        )
        return "skipped — a real key is present, so publishing this would spend quota"

    report = publish_task(
        "jobs.ingest.ingest_symbol",
        {"ticker": SYMBOL, "month": MONTH},
        timeout=CELERY_TIMEOUT,
    )
    if context.options.live_vendor:
        if report["state"] != "SUCCESS":
            raise SmokeFailure(
                expected="`jobs.ingest.ingest_symbol` to succeed against the live vendor",
                observed=f"state={report['state']} result={json.dumps(report['result'])[:300]}",
                hint="`rate_limited` means the daily cap; `client_error` means the vendor "
                "does not know the symbol. Neither is retried by the job, deliberately",
            )
        written = (report["result"] or {}).get("written")
        return f"live ingest task SUCCESS, wrote {written} candles"

    failure = report["result"] if isinstance(report["result"], dict) else {}
    if report["state"] != "FAILURE" or failure.get("exc_type") != "ExternalServiceError":
        raise SmokeFailure(
            expected="`jobs.ingest.ingest_symbol` to fail with ExternalServiceError, because "
            "ALPHAVANTAGE_API_KEY is blank and the client refuses before it opens a socket",
            observed=f"state={report['state']} result={json.dumps(report['result'])[:300]}",
            hint="a SUCCESS here would mean a call really did leave the machine with no key "
            "configured; a different exception means the message never reached the client",
        )
    context.notes.append(
        "ingest task reached the vendor seam and stopped there (no key, no call, no quota)"
    )
    return "published and consumed; refused at the vendor seam with `not_configured` (0 calls)"


def step_stock_data(context: Context) -> str:
    with httpx.Client(base_url=context.api, timeout=HTTP_TIMEOUT) as client:
        by_ticker = expect(
            client.get(f"/v1/stocks/by-ticker/{SYMBOL.lower()}", headers=context.bearer()),
            200,
            what=f"GET /v1/stocks/by-ticker/{SYMBOL.lower()} (deliberately lower-cased)",
            hint="the service canonicalises the symbol; a 404 means it stopped",
        )
        candles = expect(
            client.get(
                f"/v1/stocks/{context.stock_id}/data",
                params={"limit": 5},
                headers=context.bearer(),
            ),
            200,
            what="GET /v1/stocks/{id}/data",
        )
    if by_ticker["stock_id"] != context.stock_id:
        raise SmokeFailure(
            expected="both routes to resolve the same security",
            observed=f"by-ticker={by_ticker['stock_id']} by-id={context.stock_id}",
            hint="",
        )
    items = page_items(candles, what="GET /v1/stocks/{id}/data")
    if candles["total"] < STUB_CANDLE_COUNT:
        raise SmokeFailure(
            expected=f"at least {STUB_CANDLE_COUNT} stored candles after the ingest",
            observed=f"total={candles['total']}",
            hint="the ingest reported what it wrote; if that was 0, a previous run already "
            "stored this month and the watermark filtered it — but the rows should still "
            "be readable, so an empty page means the read is pointed elsewhere",
        )
    first = items[0]
    if not isinstance(first["close_price"], str):
        raise SmokeFailure(
            expected="prices to be serialised as **quoted JSON strings**",
            observed=f"close_price is a {type(first['close_price']).__name__}",
            hint="they are `Decimal`; a JSON number silently loses the fourth decimal place",
        )
    if first["datetime"].endswith("Z") or "+" in first["datetime"][10:]:
        raise SmokeFailure(
            expected="`datetime` to be naive — the exchange's local trading clock",
            observed=first["datetime"],
            hint="it is the one timestamp in this API without an offset, on purpose",
        )
    return (
        f"{candles['total']} candles; close_price={first['close_price']!r} (a string), "
        f"datetime={first['datetime']} (naive)"
    )


def step_watchlist(context: Context) -> str:
    with httpx.Client(base_url=context.api, timeout=HTTP_TIMEOUT) as client:
        created = expect(
            client.post(
                "/v1/watchlists",
                json={"title": "Smoke"},
                headers=context.bearer(),
            ),
            201,
            what="POST /v1/watchlists",
        )
        watchlist_id = created["watchlist_id"]
        expect(
            client.post(
                f"/v1/watchlists/{watchlist_id}/stocks",
                json={"stock_id": context.stock_id},
                headers=context.bearer(),
            ),
            201,
            what="POST /v1/watchlists/{id}/stocks",
            hint="omitting `position` appends; a 409 means the security is already on it",
        )
        detail = expect(
            client.get(f"/v1/watchlists/{watchlist_id}", headers=context.bearer()),
            200,
            what="GET /v1/watchlists/{id}",
        )
        mine = expect(
            client.get("/v1/watchlists", headers=context.bearer()),
            200,
            what="GET /v1/watchlists",
        )
    entries = detail.get("entries") or []
    tickers = [entry["stock"]["ticker_symbol"] for entry in entries]
    positions = [entry["position"] for entry in entries]
    if SYMBOL not in tickers:
        raise SmokeFailure(
            expected=f"the watchlist to hold {SYMBOL} after it was added",
            observed=f"it holds {tickers}",
            hint="entries come back in `position` order; an empty list means the add "
            "landed on a different watchlist or a different account",
        )
    if positions != sorted(positions):
        raise SmokeFailure(
            expected="entries already in `position` order — the relationship orders them",
            observed=f"positions {positions}",
            hint="neither the schema nor the caller sorts; if this is unordered the "
            "`order_by` on the relationship is gone",
        )
    owned = page_items(mine, what="GET /v1/watchlists")
    return f"watchlist {watchlist_id[:8]}… holds {tickers}; the account owns {len(owned)}"


def step_frontend_up(context: Context) -> str:
    started = compose("--profile", "frontend", "up", "-d", "web", timeout=COMPOSE_UP_TIMEOUT)
    if started.code != 0:
        raise SmokeFailure(
            expected="the Vite dev server container to start",
            observed=started.output,
            hint="there is no node on the dev host, so this container is the only place "
            "any npm command can run",
        )
    deadline = time.monotonic() + HEALTH_TIMEOUT
    last = ""
    while time.monotonic() < deadline:
        try:
            with httpx.Client(timeout=HTTP_TIMEOUT) as client:
                response = client.get(context.web)
            if response.status_code == 200:
                return f"dev server answering on {context.web}"
            last = f"{response.status_code}"
        except httpx.HTTPError as error:
            last = f"{type(error).__name__}: {error}"
        time.sleep(2.0)
    raise SmokeFailure(
        expected=f"GET {context.web} to answer 200",
        observed=last,
        hint="WEB_HOST_PORT publishes it; `scripts/logs web` shows a Vite that failed to "
        "start, which is almost always a syntax error in a config file",
    )


def step_frontend_build(context: Context) -> str:
    """Build the production bundle, **same-origin**.

    ``VITE_API_BASE_URL`` is emptied for this build on purpose: the cold load below runs the
    bundle in a DOM whose document URL is the API's own origin, so the app's requests are
    same-origin and no CORS is involved. That keeps the harness honest — a jsdom
    cross-origin XHR failing would be a fact about jsdom, and the CORS configuration already
    has a step of its own above.

    The output goes to :data:`SMOKE_DIST`, container-local, for the ownership reason
    recorded there. ``--emptyOutDir`` is required rather than tidy: vite refuses to clear an
    ``outDir`` outside the project root unless it is told to, so without it a second run
    would build on top of the first.
    """
    done = compose_exec(
        "web",
        "sh",
        "-c",
        f"VITE_API_BASE_URL= npm run build -- --outDir {SMOKE_DIST} --emptyOutDir",
        timeout=FRONTEND_BUILD_TIMEOUT,
    )
    if done.code != 0:
        raise SmokeFailure(
            expected="`npm run build` to produce a production bundle",
            observed=done.output,
            hint="run it yourself with `scripts/test frontend` first — a build failure here "
            "is a frontend failure, not a stack one",
        )
    development = compose_exec(
        "web",
        "sh",
        "-c",
        f"cat {SMOKE_DIST}/assets/*.js | grep -c jsxDEV || true",
        timeout=60.0,
    )
    count = development.stdout.strip().splitlines()[-1] if development.stdout.strip() else "0"
    if count != "0":
        raise SmokeFailure(
            expected="a production bundle — zero `jsxDEV` calls",
            observed=f"{count} occurrences",
            hint="something set NODE_ENV=development; Vite honours an inherited NODE_ENV "
            "over its own mode and ships a development build with no warning",
        )
    return f"production bundle built into {SMOKE_DIST}, VITE_API_BASE_URL empty (same-origin)"


def step_cold_load(context: Context) -> str:
    """The single most valuable thing in this file.

    A cold start with nothing but a refresh token exercises the route guard, the axios
    interceptor, the token rotation, the bearer replay and the API — in one page load. It is
    also the path that had never run end to end before ANV-36, because the desktop renders
    empty under jsdom and nothing else on `/research` issues a request.
    """
    done = compose_exec(
        "web",
        "sh",
        "-c",
        f"cat > {COLD_LOAD_PATH} && node {COLD_LOAD_PATH}",
        timeout=COLD_LOAD_TIMEOUT + 60.0,
        stdin=COLD_LOAD_SOURCE,
        env={
            "ANVEX_SMOKE_DIST": SMOKE_DIST,
            "ANVEX_SMOKE_ORIGIN": "http://api:8000",
            "ANVEX_SMOKE_ROUTE": "/research",
            "ANVEX_SMOKE_TICKER": SYMBOL,
            "ANVEX_SMOKE_REFRESH": context.refresh_token,
            "ANVEX_SMOKE_TIMEOUT_MS": str(int(COLD_LOAD_TIMEOUT * 1000)),
        },
    )
    report: dict[str, Any] | None = None
    for line in done.stdout.splitlines():
        if line.startswith("ANVEX-COLDLOAD "):
            report = json.loads(line[len("ANVEX-COLDLOAD ") :])
    if report is None:
        raise SmokeFailure(
            expected="the jsdom harness to report on the cold load",
            observed=f"exit {done.code}:\n{done.output}",
            hint="`ReferenceError: Response is not defined` means Node's globals were not "
            "handed to the window — TanStack Router's redirect does `instanceof Response`",
        )
    if not report["ok"]:
        raise SmokeFailure(
            expected="`/research` to load from a refresh token alone and list the securities",
            observed=json.dumps(report)[:600],
            hint="the desktop renders EMPTY under jsdom (no ResizeObserver) and that is "
            "expected — this asserts the securities panel, which is unmeasured",
        )
    if not report.get("sawTicker"):
        raise SmokeFailure(
            expected=f"the securities panel to list {SYMBOL}",
            observed=json.dumps(report)[:600],
            hint="the panel rendered but the row is missing — the app and this script are "
            "reading different databases",
        )
    rotated = "rotated" if report.get("refreshRotated") else "NOT rotated"
    return (
        f'cold start on {report.get("path")}: "{report["detail"]}", {SYMBOL} listed, '
        f"stored refresh token {rotated}"
    )


#: The run, in order. The ids are the checklist in `docs/smoke.md`, and
#: `tests/unit/test_smoke.py` asserts the two lists are equal — a step added here without a
#: line there, or the reverse, fails the backend suite.
STEPS: Final[tuple[Step, ...]] = (
    Step("preflight", "Docker, .env and the vendor key", step_preflight),
    Step("clean", "Destroy the stack (--clean)", step_clean),
    Step("compose-up", "docker compose up: db, redis, minio, api", step_compose_up),
    Step("migrate", "alembic upgrade head", step_migrate),
    Step("seed", "Load the checked-in roster", step_seed),
    Step("api-health", "Liveness and readiness", step_api_health),
    Step("cors", "The browser origin is allowed", step_cors),
    Step("register", "Register a user", step_register),
    Step("login", "Log in", step_login),
    Step("refresh", "Rotate the token pair", step_refresh),
    Step("reference-data", "The seeded roster reads back over HTTP", step_reference_data),
    Step("security", "A security to ingest and to read", step_security),
    Step("ingest", "Ingest one symbol-month", step_ingest),
    Step("worker", "Start the worker and run the heartbeat", step_worker),
    Step("ingest-task", "Trigger the ingest task and watch it finish", step_ingest_task),
    Step("stock-data", "Read the candles back over HTTP", step_stock_data),
    Step("watchlist", "Create a watchlist and add the security", step_watchlist),
    Step("frontend-up", "Start the web container", step_frontend_up, frontend=True),
    Step("frontend-build", "Build the production bundle", step_frontend_build, frontend=True),
    Step("cold-load", "Load /research from a refresh token alone", step_cold_load, frontend=True),
)


# ---------------------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="smoke",
        description="Boot the stack and prove it works end to end.",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="destroy the stack and its volumes first, so the boot is from nothing",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="do not ask before the destructive --clean",
    )
    parser.add_argument(
        "--live-vendor",
        action="store_true",
        help="spend ONE real AlphaVantage call. Off by default: the free tier is ~25 a day",
    )
    parser.add_argument(
        "--skip-frontend",
        action="store_true",
        help="stop after the API and the worker; skips the build and the cold load",
    )
    parser.add_argument(
        "--steps",
        action="store_true",
        help="print the step ids and titles, then exit",
    )
    return parser


def selected(options: Options) -> Iterator[Step]:
    for step in STEPS:
        if step.frontend and options.skip_frontend:
            continue
        yield step


def base_urls() -> tuple[str, str]:
    """Where the API and the dev server are published, from the same `.env` compose reads."""
    api_port = os.environ.get("API_HOST_PORT") or env_value("API_HOST_PORT", "8000")
    web_port = os.environ.get("WEB_HOST_PORT") or env_value("WEB_HOST_PORT", "5173")
    return f"http://localhost:{api_port}", f"http://localhost:{web_port}"


def env_value(key: str, fallback: str) -> str:
    """One value out of the repo-root `.env`, the same way `scripts/_common` reads it."""
    path = REPO_ROOT / ".env"
    if not path.is_file():
        return fallback
    found = fallback
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            candidate = line[len(key) + 1 :].strip()
            if candidate:
                found = candidate
    return found


def confirm_clean() -> bool:
    reply = input("`--clean` deletes the dev database volume. Continue? [y/N] ")
    return reply.strip() in {"y", "Y", "yes", "YES"}


def main(argv: Sequence[str] | None = None) -> int:
    options_namespace = build_parser().parse_args(list(argv) if argv is not None else None)
    if options_namespace.steps:
        for step in STEPS:
            out(f"{step.id}\t{step.title}")
        return EXIT_OK

    options = Options(
        clean=options_namespace.clean,
        yes=options_namespace.yes,
        live_vendor=options_namespace.live_vendor,
        skip_frontend=options_namespace.skip_frontend,
    )
    if options.clean and not options.yes and not confirm_clean():
        out("nothing was destroyed and nothing was run")
        return EXIT_PRECONDITION

    api, web = base_urls()
    context = Context(options=options, settings=get_settings(), api=api, web=web)
    steps = list(selected(options))

    out("")
    out(f"Anvex smoke — {len(steps)} steps, API {api}, web {web}")
    if options.live_vendor:
        out("  --live-vendor: this run will spend ONE AlphaVantage call")
    else:
        out("  vendor calls: none (the ingest is stubbed; pass --live-vendor to spend one)")

    started = time.monotonic()
    # Tracked outside the loop so the failure report can name the step even when the very
    # first one is what raised.
    number, step = 0, steps[0]
    try:
        for number, step in enumerate(steps, start=1):
            out("")
            out(f"== [{number}/{len(steps)}] {step.id} — {step.title}")
            began = time.monotonic()
            detail = step.run(context)
            note(f"ok ({time.monotonic() - began:.1f}s) {detail}")
    except SmokeFailure as failure:
        out("")
        out(f"FAILED at step {number}/{len(steps)}: {step.id} — {step.title}")
        out(f"  expected: {failure.expected}")
        out(f"  observed: {failure.observed}")
        if failure.hint:
            out(f"  hint:     {failure.hint}")
        out("")
        out("`backend/docs/runbook.md` has the table of things that stop the stack coming up.")
        return EXIT_FAILED
    except Precondition as precondition:
        out("")
        out(f"cannot run: {precondition}")
        return EXIT_PRECONDITION

    out("")
    out(f"PASSED — {len(steps)} steps in {time.monotonic() - started:.0f}s")
    for line in context.notes:
        out(f"  · {line}")
    if options.skip_frontend:
        out("  · the frontend legs were skipped (--skip-frontend)")
    out("  · `scripts/down` stops what this started; the worker is still running")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - the entry point
    raise SystemExit(main())
