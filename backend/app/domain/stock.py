"""Pure rules about a security itself, independent of how one is stored or served.

There is one rule here today and it is a small one, but it had outgrown its previous home.
:func:`normalise_ticker` was written in ``app/services/stock.py`` (ANV-13) because that was
the only caller; ANV-14 gave it a second one, and a *rule* shared by two services is exactly
what ``CLAUDE.md`` §3 means by "if it would still be true on paper without a computer, it
belongs in domain". Two services importing each other sideways would have been the wrong
shape — both now import downward, from here.

``app/services/stock.py`` re-exports the name so ANV-13's public surface is unchanged: a
caller that already writes ``from app.services.stock import normalise_ticker`` keeps
working, and a new caller reaches for the rule where rules live.
"""

from __future__ import annotations


def normalise_ticker(ticker: str) -> str:
    """The canonical spelling of a ticker: trimmed and upper-cased.

    A security has exactly one spelling — ``aapl``, ``AAPL`` and ``" AAPL "`` are the same
    instrument — and ``stocks.ticker_symbol`` is unique, so a lookup that skipped this step
    would report a perfectly real stock as missing. The repo lookups it feeds are exact and
    case-sensitive on purpose: folding case in SQL would defeat the unique index that serves
    them.

    Nothing beyond whitespace and case is touched. A dot (``BRK.B``), a hyphen (``BF-B``) and
    a caret are all real characters in real symbols, so "cleaning" them would corrupt the
    lookup rather than help it.
    """
    return ticker.strip().upper()


__all__ = ["normalise_ticker"]
