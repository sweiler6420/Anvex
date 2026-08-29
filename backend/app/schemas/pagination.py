"""The one envelope every list endpoint returns.

Every collection response from ANV-13 onward is a ``Page[T]`` rather than a bare JSON
array, so a client writes one unwrapping helper and one paging control instead of one per
resource. A bare array is also unextendable: adding a count later would be a breaking
change, while adding a key to this envelope is not.

**Field set, and why each one earns its place.**

``items``
    The rows themselves, already the resource's ``XOut`` schema.
``total``
    How many rows match the query *ignoring* ``limit``/``offset``. The frontend's tables
    render numbered pages, which is impossible to do without it — that is the single
    reason this is offset paging rather than cursor paging. Every list Anvex serves is a
    bounded set ordered by a stable key (a user's watchlists, a search's matches, a
    stock's candles for a date range), so the usual argument against ``COUNT(*)`` — an
    unbounded feed — does not apply here.
``limit`` / ``offset``
    Echoed back so the response is self-describing: a client that did not send them (and
    got :data:`DEFAULT_PAGE_LIMIT`) still knows what window it is looking at, and a cached
    or logged response can be interpreted without its request.
``has_more``
    Computed, not stored — ``offset + len(items) < total``. It is derivable, but every
    consumer would otherwise derive it, and an off-by-one in a "Next" button is a bug
    worth having exactly one implementation of.

Deliberately **not** included: a ``page`` number (``offset``/``limit`` is the primitive and
a page number is the view layer's arithmetic), ``pages`` (likewise derivable), and a
cursor (see ``total`` above).

The limit bounds live here, not in ``app/deps/``, so the dependency that parses the query
string and the schema that reports the result cannot disagree.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, computed_field

#: What a client gets when it asks for a list without saying how many.
DEFAULT_PAGE_LIMIT = 50

#: The ceiling a client may ask for. Requests above it are rejected at the edge rather
#: than quietly clamped, so a caller never believes it received a complete list.
MAX_PAGE_LIMIT = 200


class Page[T](BaseModel):
    """One window onto a larger collection.

    ``T`` is always an output schema, never an ORM model (``CLAUDE.md`` §3)::

        Page[StockOut](items=[...], total=417, limit=50, offset=100)
    """

    model_config = ConfigDict(from_attributes=True)

    items: list[T] = Field(description="The rows in this window, in the endpoint's order.")
    total: int = Field(
        ge=0,
        description="Rows matching the query in total, ignoring `limit` and `offset`.",
        examples=[417],
    )
    limit: int = Field(
        ge=1,
        le=MAX_PAGE_LIMIT,
        description="The window size that produced `items`.",
        examples=[DEFAULT_PAGE_LIMIT],
    )
    offset: int = Field(
        ge=0,
        description="How many rows were skipped before `items`.",
        examples=[100],
    )

    @computed_field(  # type: ignore[prop-decorator]
        description="True when another window follows this one.",
    )
    @property
    def has_more(self) -> bool:
        """Whether a further request at ``offset + limit`` would return anything."""
        return self.offset + len(self.items) < self.total


__all__ = ["DEFAULT_PAGE_LIMIT", "MAX_PAGE_LIMIT", "Page"]
