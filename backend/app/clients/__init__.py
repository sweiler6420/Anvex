"""Third-party I/O. One module per vendor, all of them built on :mod:`app.clients.base`.

``CLAUDE.md`` §3: a client knows exactly one vendor and nothing about Anvex. It takes
primitives, returns typed data, and raises
:class:`~app.domain.errors.ExternalServiceError` — never a raw ``httpx`` exception and
never a raw ``Response``.

Nothing is re-exported here on purpose: import the vendor you mean
(``from app.clients.alphavantage import AlphaVantageClient``) so a service's imports say
which upstreams it actually touches.
"""
