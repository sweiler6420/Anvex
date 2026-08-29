"""The public shape of a news article.

Unlike every other module in this package, :class:`NewsArticleOut` is **not** built from an
ORM row — Anvex stores no news. It is the projection of
:class:`~app.clients.newsapi.NewsArticle`, and the two are separate on purpose: a vendor's
response shape is not an API contract, and the AST sweep over ``app/clients/`` forbids that
layer from importing this one for exactly that reason. ``app/services/news.py`` is where the
two meet.

That has one visible consequence. The package docstring's rule — "optionality mirrors the
database exactly, and a defensive ``| None`` is a null check clients pay for" — is about
columns, and there are no columns here. Four of these fields really are ``null`` most of the
time: NewsAPI returns no ``description`` for a large fraction of what it indexes, no
``author`` for wire copy, and no image for most of it. Declaring them required would be the
lie, not the other way round. ``title``, ``url`` and ``published_at`` are **not** optional,
because ``app/domain/news.py`` drops any article missing the first two before this schema is
ever reached, and an article with no timestamp cannot be placed in a feed.
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field


class NewsArticleOut(BaseModel):
    """One article as the API returns it.

    ``source`` is flattened to the outlet's display name rather than nested: the vendor's
    ``{"id": null, "name": "Reuters"}`` object has a slug for only its own short list of
    partners, so nesting it would give every client an object whose interesting half is
    ``null`` most of the time.

    The vendor's ``content`` field is deliberately **not** exposed. It is the first ~200
    characters of the article with a literal ``"… [+2541 chars]"`` glued on the end — it
    cannot be read, it cannot be summarised, and re-serving a publisher's body text is a
    licensing question this endpoint has no reason to raise. ``description`` is the outlet's
    own summary and is what a card actually renders. The client still parses ``content``,
    because ``app/domain/news.py`` counts its presence when choosing between two copies of
    one story; it just does not leave the building.
    """

    model_config = ConfigDict(from_attributes=True)

    title: str = Field(description="The headline, as the outlet wrote it.")
    url: str = Field(description="A link to the article on the publisher's own site.")
    published_at: dt.datetime = Field(
        description="When the outlet published it. Timezone-aware, normally UTC."
    )
    source_name: str | None = Field(
        default=None, description="The outlet's display name, e.g. `Reuters`."
    )
    author: str | None = Field(default=None, description="Byline, where the outlet gave one.")
    description: str | None = Field(default=None, description="The outlet's own summary.")
    url_to_image: str | None = Field(default=None, description="Lead image, where there is one.")


__all__ = ["NewsArticleOut"]
