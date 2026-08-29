"""The shared async HTTP foundation every ``app/clients/`` integration is built on.

ANV-17 opens the integrations epic, so this module is the *shape* of the layer as much as
it is code — the same role ``app/data/loader.py`` plays for reference data. ANV-18
(AlphaVantage), ANV-19 (NewsAPI) and anything after them subclass :class:`BaseHTTPClient`
and inherit its lifecycle, timeouts, retry policy, logging and error contract rather than
re-deriving any of it.

The contract, from ``CLAUDE.md`` §3
-----------------------------------

* **A client knows exactly one vendor** — its base URL, its auth, its retry and rate-limit
  behaviour, and the shape of its responses. Two vendors are two modules.
* **A client returns typed data, never a raw** ``Response``. :meth:`BaseHTTPClient.get_json`
  is deliberately the widest thing the base offers: a subclass parses the payload into its
  own pydantic model and returns *that*. A caller must never have to know an HTTP status
  code to use a client.
* **A client knows nothing about Anvex.** No session, no repo, no ``stock_id``, no
  ``app.schemas`` (that is the API's public shape, and a vendor does not share it), no
  ``app.domain`` rule. Primitives in, vendor data out. The one Anvex import allowed here is
  :class:`~app.domain.errors.ExternalServiceError`, because "the upstream failed" is the
  only thing a client has to be able to say to the rest of the application.

``tests/unit/test_clients_base.py::TestTheLayerStaysInItsLane`` enforces all of that by
parsing every module in this package, because a layering rule that lives only in prose gets
broken — the same argument ``tests/unit/test_data_loader.py`` makes for ``app/data/``.

Failures are :class:`~app.domain.errors.ExternalServiceError` (→ 502)
--------------------------------------------------------------------

Every way a vendor call can fail — unreachable host, timeout, 5xx, 4xx, rate limit, a body
that is not JSON — leaves this module as one exception type. That is deliberately
*different* from ``app/data/``, which raises a plain ``ValueError`` and stays free of Anvex
error vocabulary: a broken seed file is a repository defect reached from a script and has no
status code, whereas a client call is always inside a request or a job and 502 ("we are up,
the upstream is not") is already waiting for it in ``CLAUDE.md`` §4's mapping table. This
module is that mapping's first producer.

``details`` carries ``reason``, ``attempts`` and — where they exist — ``status_code`` and
``retry_after``. It never carries the vendor's body, its URL, or anything derived from a
credential.

Timeouts
--------

Four, named and chosen rather than inherited from httpx's single 5-second default:
:data:`CONNECT_TIMEOUT_SECONDS` is short because a TCP handshake either happens promptly or
the host is unreachable; :data:`READ_TIMEOUT_SECONDS` is much longer because a vendor
genuinely thinks about a query; write and pool sit in between. A timeout is a
``TransportError`` and therefore retryable.

Retry
-----

**4xx is never retried.** Retrying a 401 turns one permanent failure into three, and
retrying a 429 makes the exact problem the vendor is complaining about strictly worse. Only
transport errors (which includes timeouts) and 5xx are retried on the ordinary budget.

**429 is treated as its own thing**, because it is a "not now" rather than a "never":

* it gets a **separate, shorter budget** (:attr:`RetryPolicy.rate_limited_attempts`, one
  retry) — enough to ride out a burst boundary, not enough to hammer a vendor that means it;
* when the response carries ``Retry-After``, that wait is **honoured but capped**
  (:attr:`RetryPolicy.retry_after_cap_seconds`). A vendor asking for 60 seconds is asking
  for longer than any request path may be held open, so the call fails immediately with
  ``retry_after`` in ``details`` and the caller — a Celery job, typically — reschedules.
  There is deliberately no code path here that can wait an unbounded amount of time.
* an ``HTTP-date`` form of ``Retry-After`` is treated as absent rather than parsed, because
  parsing it needs a clock and ``CLAUDE.md`` §4 keeps clock reads out of shared machinery;
  the capped backoff below is a safe answer either way.

Waiting is ``await``-ed, never slept through: the default sleeper is :func:`asyncio.sleep`
and a blocking ``time.sleep`` is banned by the layering test. The loop is bounded twice —
by attempt count *and* by :attr:`RetryPolicy.total_budget_seconds` of wall clock — so a
vendor that is slow rather than down still cannot pin a worker.

Proactive quota throttling (AlphaVantage's five-calls-per-minute, say) is **not** here. That
is a scheduling concern belonging to the job that fans out, not to a request path that would
have to block to honour it.

Credentials
-----------

API keys arrive from ``app/settings.py`` as ``SecretStr`` and they must not stop being
secret the moment they are useful. Two rules keep them out of the record:

1. **A secret is unwrapped inside one stack frame and never stored in plaintext.** A
   subclass holds the ``SecretStr`` and returns it from :meth:`BaseHTTPClient.auth_params`
   or :meth:`BaseHTTPClient.auth_headers`; the base unwraps it while building the request
   and lets the plaintext go out of scope with the call. (``CLAUDE.md`` §3 named the service
   layer as the only place allowed to call ``.get_secret_value()``; a client is the second,
   and only in the request builder — see §3's client section.)
2. **Nothing that could contain one is ever logged.** Request and response bodies and
   headers are never logged at all, and the URL is logged through :func:`redact_url`, which
   blanks any query value whose *name* looks like a credential and any value that *is* one
   of this call's secrets. Redaction is by construction, not by remembering: a subclass
   cannot log a raw URL, because it never gets one.

Nothing derived from a credential reaches an exception either — every message is a fixed
template naming only the vendor.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar, Final, Self
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
import structlog
from pydantic import SecretStr

from app.domain.errors import ExternalServiceError

logger = structlog.get_logger("anvex.clients")

# ---------------------------------------------------------------------------------------
# Timeouts
# ---------------------------------------------------------------------------------------

#: A TCP connection either happens quickly or the host is not there.
CONNECT_TIMEOUT_SECONDS: Final[float] = 5.0
#: The vendor is allowed to think. This is the one that is deliberately generous.
READ_TIMEOUT_SECONDS: Final[float] = 15.0
#: Uploading a request body; our payloads are tiny, so this only catches a stalled socket.
WRITE_TIMEOUT_SECONDS: Final[float] = 10.0
#: Waiting for a free connection from the pool. Long enough to queue, short enough that a
#: saturated pool surfaces as a 502 rather than as an unexplained hang.
POOL_TIMEOUT_SECONDS: Final[float] = 5.0

#: The four above as httpx expects them. Never pass a bare number as a timeout — it sets
#: all four at once and hides which one you meant.
DEFAULT_TIMEOUT: Final[httpx.Timeout] = httpx.Timeout(
    connect=CONNECT_TIMEOUT_SECONDS,
    read=READ_TIMEOUT_SECONDS,
    write=WRITE_TIMEOUT_SECONDS,
    pool=POOL_TIMEOUT_SECONDS,
)

#: Sent on every request. Some vendors (NewsAPI) refuse a request without one.
USER_AGENT: Final[str] = "anvex-backend"

#: Merged under a subclass's own headers, so a subclass can override either.
DEFAULT_HEADERS: Final[Mapping[str, str]] = {
    "Accept": "application/json",
    "User-Agent": USER_AGENT,
}

# ---------------------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------------------

#: What a redacted value is replaced with. Obviously not a value, greppable, and made of
#: characters ``urlencode`` leaves alone — ``***`` would come back out as ``%2A%2A%2A``,
#: which is the same guarantee wearing a disguise nobody greps for.
REDACTED: Final[str] = "REDACTED"

#: Query parameter names whose value is blanked before a URL is logged, whether or not this
#: client knows the value is a secret. Belt to the braces of the per-call secret list: a
#: vendor that names its key ``token`` is covered before anyone remembers to say so.
SENSITIVE_PARAM_NAMES: Final[frozenset[str]] = frozenset(
    {
        "access_key",
        "access_token",
        "api-key",
        "api_key",
        "apikey",
        "auth",
        "authorization",
        "client_secret",
        "key",
        "password",
        "refresh_token",
        "secret",
        "sig",
        "signature",
        "token",
        "x-api-key",
    }
)


def redact_url(url: httpx.URL | str, *, secrets: Iterable[str] = ()) -> str:
    """``url`` with every credential-shaped query value replaced by :data:`REDACTED`.

    Two independent tests, because either alone has a hole: the parameter's *name* is in
    :data:`SENSITIVE_PARAM_NAMES` (catches a key this client did not know was one), or its
    *value* is one of ``secrets`` (catches a vendor that calls its key ``u`` — NewsAPI-style
    naming is not hypothetical).

    Everything else survives, on purpose. ``function=TIME_SERIES_INTRADAY&symbol=AAPL`` is
    the whole diagnostic value of the log line; blanking the entire query string would keep
    the secret safe and make the log useless.
    """
    parts = urlsplit(str(url))
    if not parts.query:
        return urlunsplit(parts)

    known = {secret for secret in secrets if secret}
    pairs = [
        (name, REDACTED if name.lower() in SENSITIVE_PARAM_NAMES or value in known else value)
        for name, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit(parts._replace(query=urlencode(pairs)))


def scrub(text: str, secrets: Iterable[str]) -> str:
    """``text`` with every literal occurrence of a secret replaced.

    The last line of defence, for text this module did not compose itself (a library's
    exception message). Nothing in Anvex should rely on it alone.
    """
    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTED)
    return text


# ---------------------------------------------------------------------------------------
# Failure taxonomy and retry policy
# ---------------------------------------------------------------------------------------


class Failure(StrEnum):
    """Why a vendor call did not produce a payload. The value lands in ``details.reason``."""

    #: The request never got an answer: DNS, refused connection, reset, or a timeout.
    TRANSPORT = "transport_error"
    #: 5xx. The vendor is broken right now, which is the archetypal retryable failure.
    SERVER_ERROR = "server_error"
    #: 429 specifically. A "not now", handled on its own budget — see the module docstring.
    RATE_LIMITED = "rate_limited"
    #: Any other 4xx. Permanent by definition, and never retried.
    CLIENT_ERROR = "client_error"
    #: A 3xx. Redirects are not followed (see :meth:`BaseHTTPClient._new_client`).
    REDIRECT = "unexpected_redirect"
    #: A 2xx whose body is not the JSON it claimed to be.
    MALFORMED = "malformed_response"
    #: A protocol-level ``httpx`` error that is not a transport problem (too many
    #: redirects, a bad decoding). Not transient, so not retried.
    PROTOCOL = "protocol_error"


#: The only failures a retry could possibly fix.
RETRYABLE: Final[frozenset[Failure]] = frozenset(
    {Failure.TRANSPORT, Failure.SERVER_ERROR, Failure.RATE_LIMITED}
)

#: One sentence per failure, naming the vendor and nothing else. These are the strings an
#: API consumer sees in a 502 body, so they say what happened without saying anything about
#: how the request was built.
_MESSAGES: Final[Mapping[Failure, str]] = {
    Failure.TRANSPORT: "The upstream service '{vendor}' could not be reached.",
    Failure.SERVER_ERROR: "The upstream service '{vendor}' failed.",
    Failure.RATE_LIMITED: "The upstream service '{vendor}' is rate limiting Anvex.",
    Failure.CLIENT_ERROR: "The upstream service '{vendor}' rejected the request.",
    Failure.REDIRECT: "The upstream service '{vendor}' redirected the request unexpectedly.",
    Failure.MALFORMED: "The upstream service '{vendor}' returned an unreadable response.",
    Failure.PROTOCOL: "The upstream service '{vendor}' could not be spoken to.",
}


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """How many times, how long between, and when to stop trying.

    Immutable and passed in, so a vendor with different manners gets its own policy rather
    than a subclass reimplementing the loop::

        class SlowVendorClient(BaseHTTPClient):
            def __init__(self) -> None:
                super().__init__(retry=RetryPolicy(attempts=5, max_backoff_seconds=5.0))
    """

    #: Total tries — *not* retries — for a transport error or a 5xx. ``1`` disables retry.
    attempts: int = 3
    #: Total tries for a 429. Shorter on purpose: see the module docstring.
    rate_limited_attempts: int = 2
    #: Wait after the first failure. Doubled by :attr:`backoff_multiplier` each time.
    initial_backoff_seconds: float = 0.2
    backoff_multiplier: float = 2.0
    #: Ceiling on a single wait, before jitter.
    max_backoff_seconds: float = 2.0
    #: Fraction of the backoff that jitter may remove (``0.25`` → a wait in
    #: ``[0.75·base, base]``). Spreads a fanned-out job's retries instead of synchronising
    #: them into a second thundering herd against a vendor that is already struggling.
    jitter_ratio: float = 0.25
    #: A ``Retry-After`` longer than this is refused rather than waited out.
    retry_after_cap_seconds: float = 2.0
    #: Wall-clock ceiling on the whole call, retries and waits included. The second bound:
    #: attempts alone cannot stop three slow-but-not-dead responses adding up.
    total_budget_seconds: float = 20.0

    def __post_init__(self) -> None:
        if self.attempts < 1 or self.rate_limited_attempts < 1:
            raise ValueError("a retry policy must allow at least one attempt")
        if self.initial_backoff_seconds < 0 or self.max_backoff_seconds < 0:
            raise ValueError("backoff cannot be negative")
        if self.backoff_multiplier < 1:
            raise ValueError("a backoff multiplier below 1 shortens each successive wait")
        if not 0.0 <= self.jitter_ratio < 1.0:
            raise ValueError("jitter_ratio must be in [0, 1)")
        if self.retry_after_cap_seconds < 0 or self.total_budget_seconds <= 0:
            raise ValueError("a wait budget cannot be negative")

    def attempts_for(self, failure: Failure) -> int:
        """How many total tries this kind of failure is allowed. Non-retryable → ``1``."""
        if failure is Failure.RATE_LIMITED:
            return self.rate_limited_attempts
        return self.attempts if failure in RETRYABLE else 1

    def backoff_for(self, attempt: int, *, jitter: float = 0.0) -> float:
        """Seconds to wait after ``attempt`` (1-based) failed. Capped, then jittered down."""
        base = min(
            self.initial_backoff_seconds * self.backoff_multiplier ** (attempt - 1),
            self.max_backoff_seconds,
        )
        return base * (1.0 - self.jitter_ratio * min(max(jitter, 0.0), 1.0))

    def wait_for(
        self, attempt: int, *, retry_after: float | None = None, jitter: float = 0.0
    ) -> float | None:
        """The wait before the next attempt, or ``None`` meaning "stop now".

        ``None`` is returned when the vendor's own ``Retry-After`` exceeds
        :attr:`retry_after_cap_seconds`. Failing immediately is the right answer there: the
        caller gets a 502 with ``retry_after`` in ``details`` and can reschedule, which is
        strictly better than holding a request open for a minute to find out the same thing.
        """
        if retry_after is not None:
            return None if retry_after > self.retry_after_cap_seconds else retry_after
        return self.backoff_for(attempt, jitter=jitter)


#: The policy every client gets unless it says otherwise.
DEFAULT_RETRY: Final[RetryPolicy] = RetryPolicy()


def parse_retry_after(value: str | None) -> float | None:
    """``Retry-After`` as seconds, or ``None`` when it is absent or not a number.

    Only the delta-seconds form is understood. The ``HTTP-date`` form needs a clock to mean
    anything, and shared machinery reading the clock is exactly what ``CLAUDE.md`` §4
    forbids; treating it as absent falls back to the capped backoff, which is safe.
    """
    if value is None:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    return max(seconds, 0.0)


# ---------------------------------------------------------------------------------------
# The base client
# ---------------------------------------------------------------------------------------

#: An awaitable sleep. Injected so a test can assert *what* was waited without waiting.
Sleeper = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class _Attempt:
    """The outcome of one try: either a decoded payload or a classified failure.

    A named result rather than a tuple, so :meth:`BaseHTTPClient.request_json`'s loop reads
    as a sequence of decisions instead of unpacking four positional maybes.
    """

    failure: Failure | None = None
    payload: Any = None
    status_code: int | None = None
    retry_after: float | None = None
    cause: BaseException | None = None


class BaseHTTPClient:
    """One vendor's HTTP surface: lifecycle, timeouts, retry, logging, error contract.

    A subclass supplies the two class attributes and its own typed methods::

        class AlphaVantageClient(BaseHTTPClient):
            vendor = "alphavantage"
            base_url = "https://www.alphavantage.co"

            def __init__(self, settings: Settings, **kwargs: Any) -> None:
                self._api_key = settings.alphavantage_api_key   # stays a SecretStr
                super().__init__(**kwargs)

            def auth_params(self) -> Mapping[str, SecretStr | str]:
                return {"apikey": self._api_key}

            async def fetch_quote(self, symbol: str) -> Quote:
                payload = await self.get_json(
                    "/query", params={"function": "GLOBAL_QUOTE", "symbol": symbol}
                )
                return Quote.model_validate(payload)   # typed data, never a Response

    Note what the subclass does *not* write: no ``try``, no status-code check, no retry, no
    logging, no ``ExternalServiceError``. All of that is inherited, which is the point —
    three vendors cannot each get the 4xx-retry decision subtly wrong if none of them makes
    it.

    **Lifecycle.** One :class:`httpx.AsyncClient` is created lazily on first use and reused
    for every call, so connections, DNS and TLS are pooled instead of being rebuilt per
    request. Close it with ``await client.aclose()`` or use the client as an async context
    manager. Closing is final — a request afterwards raises ``RuntimeError``, mirroring
    httpx's own refusal to reopen, because silently reconnecting would hide a lifecycle bug
    in a dependency that is meant to be long-lived.
    """

    #: The vendor slug. Appears in every log line and in ``details.service`` of every error.
    #: Required — a subclass without one cannot be constructed.
    vendor: ClassVar[str] = ""
    #: Scheme and host (and any fixed prefix). Request paths are relative to it.
    base_url: ClassVar[str] = ""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        retry: RetryPolicy = DEFAULT_RETRY,
        headers: Mapping[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleeper = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        cls = type(self)
        if not cls.vendor:
            raise TypeError(f"{cls.__name__} must set a class-level `vendor` slug")
        resolved = base_url if base_url is not None else cls.base_url
        if not resolved:
            raise TypeError(f"{cls.__name__} must set a class-level `base_url`")

        self._base_url = resolved
        self._timeout = timeout
        self._retry = retry
        self._headers = {**DEFAULT_HEADERS, **(headers or {})}
        self._transport = transport
        self._sleep = sleep
        self._jitter = jitter
        self._clock = clock
        self._http: httpx.AsyncClient | None = None
        self._closed = False

    # ----- lifecycle ---------------------------------------------------------------

    def _new_client(self) -> httpx.AsyncClient:
        """Build the underlying client. Overridable, but the defaults are load-bearing.

        ``follow_redirects=False`` in particular: a redirect would re-send the request —
        credential-carrying query string included — to whatever host the vendor named. A
        3xx is a failure here, not a hop.
        """
        return httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
            headers=self._headers,
            transport=self._transport,
            follow_redirects=False,
        )

    @property
    def http(self) -> httpx.AsyncClient:
        """The pooled client, created on first use."""
        if self._closed:
            raise RuntimeError(f"{type(self).__name__} has been closed and cannot be reopened")
        if self._http is None:
            self._http = self._new_client()
        return self._http

    @property
    def is_closed(self) -> bool:
        return self._closed

    async def aclose(self) -> None:
        """Release the connection pool. Idempotent."""
        self._closed = True
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    # ----- auth (subclass hooks) ---------------------------------------------------

    def auth_params(self) -> Mapping[str, SecretStr | str]:
        """Query parameters carrying credentials. Return ``SecretStr`` values.

        Unwrapped by the base while building one request and never stored in plaintext;
        registered as this call's secrets so redaction covers them even if the vendor gives
        the parameter an unremarkable name.
        """
        return {}

    def auth_headers(self) -> Mapping[str, SecretStr | str]:
        """Headers carrying credentials (``Authorization``, ``X-Api-Key``, …).

        Headers are never logged at all, but the values are still registered as secrets so
        :func:`scrub` covers anything a library says about them.
        """
        return {}

    @staticmethod
    def _unwrap(values: Mapping[str, SecretStr | str]) -> tuple[dict[str, str], tuple[str, ...]]:
        """Split an auth mapping into plain values and the subset that were secret."""
        plain: dict[str, str] = {}
        secrets: list[str] = []
        for name, value in values.items():
            if isinstance(value, SecretStr):
                revealed = value.get_secret_value()
                secrets.append(revealed)
                plain[name] = revealed
            else:
                plain[name] = value
        return plain, tuple(secrets)

    # ----- requests ----------------------------------------------------------------

    async def get_json(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """``GET path`` and return the decoded JSON body. The usual entry point."""
        return await self.request_json("GET", path, params=params, headers=headers)

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        json: Any | None = None,
    ) -> Any:
        """Perform one vendor call, retrying where retrying can help, and decode the body.

        :returns: whatever the vendor's JSON decodes to. A subclass validates it into a
            model; this layer deliberately does not know what a good payload looks like.
        :raises ExternalServiceError: for every failure mode. Nothing else escapes — an
            ``httpx`` exception reaching a service would make every caller import ``httpx``.
        """
        auth_params, param_secrets = self._unwrap(self.auth_params())
        auth_headers, header_secrets = self._unwrap(self.auth_headers())
        secrets = (*param_secrets, *header_secrets)

        request = self.http.build_request(
            method,
            path,
            params={**(params or {}), **auth_params},
            headers={**(headers or {}), **auth_headers},
            json=json,
        )
        safe_url = redact_url(request.url, secrets=secrets)
        log = logger.bind(vendor=self.vendor, method=method, url=safe_url)
        started = self._clock()
        attempt = 0

        while True:
            attempt += 1
            outcome = await self._attempt(
                request, log=log, attempt=attempt, started=started, secrets=secrets
            )
            if outcome.failure is None:
                return outcome.payload

            delay = self._delay_before_retry(
                outcome.failure,
                attempt=attempt,
                retry_after=outcome.retry_after,
                started=started,
            )
            if delay is None:
                log.warning(
                    "client.request.failed",
                    reason=outcome.failure.value,
                    attempts=attempt,
                    status_code=outcome.status_code,
                    duration_ms=self._elapsed_ms(started),
                )
                raise self._error(
                    outcome.failure,
                    attempts=attempt,
                    status_code=outcome.status_code,
                    retry_after=outcome.retry_after,
                ) from outcome.cause

            log.warning(
                "client.request.retrying",
                reason=outcome.failure.value,
                attempt=attempt,
                status_code=outcome.status_code,
                delay_seconds=round(delay, 3),
            )
            await self._sleep(delay)

    async def _attempt(
        self,
        request: httpx.Request,
        *,
        log: Any,
        attempt: int,
        started: float,
        secrets: tuple[str, ...],
    ) -> _Attempt:
        """One try, classified. Never raises: :meth:`request_json` owns the decisions.

        Keeping the try/except here and the retry arithmetic there is what stops this
        becoming a try inside a while inside a try, which is where retry loops go wrong.
        """
        log.debug("client.request.started", attempt=attempt)
        try:
            response = await self.http.send(request)
        except httpx.TransportError as error:
            # Timeouts are a TransportError subclass, so this one clause is the whole
            # retryable network family.
            log.warning(
                "client.request.transport_error",
                attempt=attempt,
                error_type=type(error).__name__,
                error=scrub(str(error), secrets),
            )
            return _Attempt(failure=Failure.TRANSPORT, cause=error)
        except httpx.HTTPError as error:
            # Too many redirects, a decoding failure: real, but not transient.
            log.warning(
                "client.request.protocol_error",
                attempt=attempt,
                error_type=type(error).__name__,
                error=scrub(str(error), secrets),
            )
            return _Attempt(failure=Failure.PROTOCOL, cause=error)

        status = response.status_code
        if 200 <= status < 300:
            try:
                payload = response.json()
            except ValueError as error:
                # Not retried: a vendor answering 200 with HTML is broken, not blipping,
                # and asking twice more gets the same HTML at three times the cost.
                log.warning(
                    "client.request.malformed",
                    attempt=attempt,
                    status_code=status,
                    content_type=response.headers.get("content-type"),
                    error_type=type(error).__name__,
                )
                return _Attempt(failure=Failure.MALFORMED, status_code=status, cause=error)
            log.info(
                "client.request.completed",
                attempt=attempt,
                status_code=status,
                duration_ms=self._elapsed_ms(started),
            )
            return _Attempt(payload=payload)

        failure = self._classify(status)
        retry_after = (
            parse_retry_after(response.headers.get("retry-after"))
            if failure is Failure.RATE_LIMITED
            else None
        )
        return _Attempt(failure=failure, status_code=status, retry_after=retry_after)

    @staticmethod
    def _classify(status: int) -> Failure:
        """Map a non-2xx status onto the taxonomy. The retry decision follows from this."""
        if status >= 500:
            return Failure.SERVER_ERROR
        if status == httpx.codes.TOO_MANY_REQUESTS:
            return Failure.RATE_LIMITED
        if status >= 400:
            return Failure.CLIENT_ERROR
        return Failure.REDIRECT

    def _delay_before_retry(
        self, failure: Failure, *, attempt: int, retry_after: float | None, started: float
    ) -> float | None:
        """Seconds to wait before trying again, or ``None`` to give up now.

        Four independent ways to stop, and every one of them is a bound: the failure is not
        retryable at all, the attempt budget for that kind of failure is spent, the vendor
        asked for longer than we may wait, or the wall-clock budget for the whole call would
        be exceeded by waiting.
        """
        if failure not in RETRYABLE or attempt >= self._retry.attempts_for(failure):
            return None
        delay = self._retry.wait_for(attempt, retry_after=retry_after, jitter=self._jitter())
        if delay is None:
            return None
        if (self._clock() - started) + delay > self._retry.total_budget_seconds:
            return None
        return delay

    def _error(
        self,
        failure: Failure,
        *,
        attempts: int,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> ExternalServiceError:
        """Build the one exception this layer raises.

        ``details`` gets the vendor, why it failed, how many tries it took to decide, and —
        where they exist — the upstream status and the wait it asked for. It never gets the
        vendor's body or URL: ``CLAUDE.md`` §4 makes the error body a public contract, and
        forwarding upstream output through it is how an internal detail becomes an API.
        """
        details: dict[str, Any] = {"reason": failure.value, "attempts": attempts}
        if status_code is not None:
            details["status_code"] = status_code
        if retry_after is not None:
            details["retry_after"] = retry_after
        return ExternalServiceError(
            self.vendor, _MESSAGES[failure].format(vendor=self.vendor), details=details
        )

    def _elapsed_ms(self, started: float) -> float:
        return round((self._clock() - started) * 1000, 2)


__all__ = [
    "CONNECT_TIMEOUT_SECONDS",
    "DEFAULT_HEADERS",
    "DEFAULT_RETRY",
    "DEFAULT_TIMEOUT",
    "POOL_TIMEOUT_SECONDS",
    "READ_TIMEOUT_SECONDS",
    "REDACTED",
    "RETRYABLE",
    "SENSITIVE_PARAM_NAMES",
    "USER_AGENT",
    "WRITE_TIMEOUT_SECONDS",
    "BaseHTTPClient",
    "Failure",
    "RetryPolicy",
    "Sleeper",
    "parse_retry_after",
    "redact_url",
    "scrub",
]
