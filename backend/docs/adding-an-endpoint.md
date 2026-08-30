# Adding an endpoint

A walkthrough of one **real, shipped** endpoint through every layer it touches, in the order
you would build it. Nothing here is invented for the example: every file, function and rule
below is in the repository, and `backend/tests/unit/test_docs.py` asserts that the seven
layers named in the table are real directories and that each file lives in the layer it is
listed under.

The endpoint:

```
PATCH /v1/watchlists/{watchlist_id}/stocks/{stock_id}
Body: {"position": 2}
→ 200, the whole watchlist in its new order
```

"Move NVDA to third on this list." It is a good example precisely because it is small and
still touches everything: it has an ownership rule, a pure algorithm, a write, a
transaction, a refusal that has to be shaped carefully, and a response the client renders
directly.

The rules behind every step are in [`../../CLAUDE.md`](../../CLAUDE.md) §3 and §4. This is
the same material as a sequence of edits.

---

## The seven layers, in order

| # | Layer | File |
| --- | --- | --- |
| 1 | `app/models/` | `backend/app/models/watchlist.py` |
| 2 | `app/repos/` | `backend/app/repos/watchlist.py` |
| 3 | `app/domain/` | `backend/app/domain/watchlist.py` |
| 4 | `app/schemas/` | `backend/app/schemas/watchlist.py` |
| 5 | `app/services/` | `backend/app/services/watchlist.py` |
| 6 | `app/deps/` | `backend/app/deps/watchlist.py` |
| 7 | `app/api/` | `backend/app/api/v1/watchlists.py` |

Build **bottom-up**. Each layer only needs the ones below it to exist, so the suite is green
at every step and you never write a function whose caller does not compile.

Three directories are *not* in that list and it is worth knowing why. `app/middleware/`
already renders the refusal this endpoint raises, so there is nothing to add. `app/clients/`
is untouched because nothing here talks to a vendor. `app/jobs/` is untouched because
nothing schedules a reorder — but if something did, it would call the same service method
step 5 writes, and that reuse is the whole reason for the layering.

---

## 1. `app/models/` — the persistence shape

The rows already existed, but two properties of them decide the entire rest of the feature.

```python
class WatchlistData(Base):
    __tablename__ = "watchlist_data"

    watchlist_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("watchlists.watchlist_id", ondelete="CASCADE"), primary_key=True
    )
    stock_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stocks.stock_id", ondelete="RESTRICT"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer)
```

- **The composite primary key is real.** The predecessor declared
  `__mapper_args__ = {"primary_key": [...]}`, which only told the ORM what to *treat* as a
  key — the table itself had none, so the same stock could be added to one watchlist twice.
  The real key is what makes "already on this list" a 409 instead of a duplicate row.
- **`position` is deliberately not unique.** A swap's intermediate state has to be legal, so
  a non-deferrable unique constraint would reject it. That single choice is why step 3
  renumbers the *whole* list rather than patching the rows it thinks changed: nothing in the
  schema guarantees the ordinals are sane, so the rule has to be total.

The two `ondelete`s are stated rather than defaulted: `CASCADE` where the child is part of
the parent, `RESTRICT` where the parent is reference data somebody depends on. Deleting a
security people are actively watching should be a loud failure, not a silent rewrite of
their lists.

**If you change a model, ship the migration in the same commit** and check
`alembic check` reports *No new upgrade operations detected*. The suite asserts it too.

## 2. `app/repos/` — the only place `select(` is written

Two methods: one to read the ordinals, one to apply them.

```python
async def list_entries(self, session, watchlist_id) -> list[WatchlistData]: ...
async def set_positions(self, session, watchlist_id, positions: Mapping[UUID, int]) -> int: ...
```

- **A repo takes a session; it never holds one.** Every method's first argument is the
  `AsyncSession`, so `watchlist_repo` is a stateless module-level singleton that a service
  takes as a keyword default. A repo that stored a session would have to be constructed per
  request and could outlive the transaction it captured.
- **A repo does not commit.** `set_positions` ends in `flush()`. The transaction boundary
  belongs to the service.
- **`list_entries` deliberately does not load the stocks.** The reorder needs the ordinals,
  not the securities. Where an eager load is optional it is a *separate method*
  (`get_by_id` versus `get_with_entries`), never a boolean flag — and every relationship a
  caller will touch must be named in `.options(selectinload(...))`, because lazy loading
  raises `MissingGreenlet` under asyncio.
- **`set_positions` ignores stock ids that are not on the watchlist.** The caller derived
  the map from this watchlist's own entries, and silently skipping a stale one beats a
  half-applied reorder.
- Naming is uniform across the package, so a reader can guess: `get_*` returns one model or
  `None`; `list_*` returns `(rows, total)` when paginated and `list[Model]` when not;
  `*_exists` returns `bool`; `set_*` mutates and flushes.

## 3. `app/domain/` — the rule, with no I/O in it

This is where the feature actually lives.

```python
def reposition(
    positions: Mapping[uuid.UUID, int], *, stock_id: uuid.UUID, destination: int
) -> dict[uuid.UUID, int]:
    order = canonical_order(positions)
    _require_member(order, stock_id)
    _require_destination(destination, ceiling=len(order) - 1)

    moved = [candidate for candidate in order if candidate != stock_id]
    moved.insert(destination, stock_id)
    return dense_positions(moved)
```

Plain data in, plain data out — no session, no request, no clock. Four decisions are
embedded in those seven lines and each of them is a defect in the endpoint being replaced:

- **The move is keyed on `stock_id`, not on a "current index".** The old endpoint took
  `(stock_id, current_index, destination_index)` and used `current_index` as a list
  subscript, ignoring `stock_id` entirely — so the row that moved was whichever one happened
  to sit at that subscript, and a client one drag stale reordered a *different* stock than
  the user dropped, silently, with a `201`. The server knows where every stock is; the
  client's belief is derived and racy. There is deliberately no way to express "the thing at
  index 3", because that sentence cannot be verified.
- **The arithmetic is a splice, not two shift loops.** The old handler had mirrored loops,
  one per direction, correct *while* positions were exactly `0..n-1`. Two branches is two
  chances to be wrong under an assumption nothing enforces. Remove-and-reinsert has no
  branches and no direction, and is correct for any starting ordinals because
  `canonical_order` normalises them first.
- **An out-of-range destination is a 422, never a clamp.** The old handler subscripted an
  unvalidated client integer: too large was an `IndexError` — a 500 for a request the API
  should simply have refused — and *negative* was worse, because Python subscripts from the
  end, so `destination = -1` quietly moved the stock to the back and reported success.
  Clamping keeps the shape of that second bug: an impossible request getting a
  plausible-looking answer.
- **It renumbers the whole list.** Total renumbering is what makes the rule correct when the
  stored ordinals have already drifted; a patch inherits whatever was wrong.

Because it is pure, its tests need no fixtures, no database and no event loop, and they can
be exhaustive. **That is where the edge cases go** — not through the API.

Two purity rules bite here and everywhere else in this directory: **no clock read and no
`uuid4()`**. Time and entropy arrive as required keyword arguments from the service, so a
test asserts a whole expected value rather than a regex over the interesting half. Each
domain module's unit tests parse its own source and fail on either.

## 4. `app/schemas/` — the public shape

```python
Position = Annotated[int, Field(ge=0, examples=[0])]


class WatchlistEntryUpdate(BaseModel):
    position: Position
```

- **A schema agrees with its column by construction.** A validator's cap is *imported* from
  the model module's constant (`TITLE_MAX_LENGTH`), never retyped, so widening a `VARCHAR`
  cannot leave a stale number behind — and an oversized field is a 422 at the edge rather
  than a `StringDataRightTruncation` from Postgres.
- **An output field is `| None` exactly when its column is nullable.** A defensive `| None`
  makes every client null-check a state that cannot happen.
- **Never return an ORM model and never accept one as a body.** `WatchlistDetailOut` is the
  response, and it is also what `GET /v1/watchlists/{id}` returns — so a client has one
  reducer for both.
- **No `user_id` is ever accepted from a client.** Ownership comes from the access token.
  A `user_id` field on a create body is an invitation to write to somebody else's account.
- Money would be `Decimal` here, which serialises as a **quoted JSON string** — a JSON
  number goes through a float and loses the fourth decimal place.

## 5. `app/services/` — the use case

```python
async def reorder_stock(
    self, data: WatchlistEntryUpdate, *, watchlist_id, stock_id, owner: User
) -> WatchlistDetailOut:
    await self._resolve_owned(watchlist_id, owner)
    moved = reposition(
        await self._positions(watchlist_id), stock_id=stock_id, destination=data.position
    )
    changed = await self.watchlists.set_positions(self.session, watchlist_id, moved)
    await self.session.commit()
    logger.info("watchlists.reordered", ...)
    return await self.get_watchlist(watchlist_id=watchlist_id, owner=owner)
```

The shape is fixed by `app/services/auth.py` and every resource since copies it:

```python
class XService:
    def __init__(self, session: AsyncSession, settings: Settings, *, xs: XRepo = x_repo) -> None:
```

Collaborators in the constructor, repos defaulting to the module singletons — **that default
is the seam a unit test replaces with an in-memory fake**, which is what lets the real
service be tested without Postgres. One `async` method per use case, keyword-only arguments,
a schema out, `app/domain/errors.py` exceptions on the way out, and the `commit()` here
because repos only flush.

Four things this method does that are conventions, not choices:

- **Ownership goes through one gate.** `_resolve_owned` is a private method every use case
  calls; it fetches the watchlist, compares `user_id`, and raises `NotFoundError` — **a 404,
  byte-identical to "no such watchlist"**, because a 403 would confirm which ids are real and
  that is the half of the information worth protecting. It fetches the row *without* its
  entries, so a refusal does no work proportional to a collection the caller may not see. The
  defect this replaces was one handler in a file of four having a `WHERE user_id` clause and
  the others not — invisible in review, and impossible to write a single test for.
- **Its test is a derived sweep, not N hand-written tests.** The case list comes from
  `vars(WatchlistService)` minus a named exempt set and is asserted complete, so a use case
  added without isolation coverage fails the suite. Each case asserts three things: the
  status is 404 and not 403, the body is identical to the body for an id that never existed,
  and no repo method beyond the gate's own lookup was reached.
- **It returns the whole reordered list.** A drag-and-drop client has just guessed what the
  result will be and wants the server's answer to render against — and the response is then
  the same shape as `get_watchlist`, so one client-side reducer handles both. The frontend
  test that keeps this honest makes the server return an order no local splice could produce.
- **It is the only layer that reads the clock**, unwraps a `SecretStr` (outside
  `app/clients/`), or owns a transaction.

Where a service catches an `IntegrityError` — `add_stock` does — three parts are
load-bearing: `await session.rollback()` **first**, because Postgres aborts the whole
transaction on a constraint violation and refuses every later statement in it; the *same*
error as the pre-check would have raised, so a client cannot tell "already taken" from "you
were second"; and an unrecognised constraint **re-raised untouched**, because that one really
is a bug and a bug should be a 500.

## 6. `app/deps/` — wiring, and nothing else

```python
def get_watchlist_service(session=Depends(get_session), settings=Depends(get_settings_dep)):
    return WatchlistService(session, settings)


WatchlistServiceDep = Annotated[WatchlistService, Depends(get_watchlist_service)]
```

One factory per resource, plus the `Annotated` alias so a route signature stays one
parameter. **That factory is the single seam an API test overrides** — which is why every
resource has exactly one, and why two routers sharing a URL prefix still get their own.

**The ownership check is deliberately not here**, even though it would fit: a dependency
could resolve `{watchlist_id}` against `CurrentUser` and hand the handler a row it is already
allowed to see. It lives in the service so that a Celery task, a script or a future WebSocket
handler goes through the same gate. A rule enforced only at the HTTP edge is a rule with one
caller and no guarantee. Same reason `get_current_user` is four lines and delegates.

A **client** is wired differently from a repo, and the difference is a real one: a repo is a
stateless singleton and arrives as a keyword default; a client owns an `httpx.AsyncClient`
and therefore a lifetime, so it is a **required** keyword argument supplied by a `yield`
dependency that `aclose()`s it in the `finally`.

## 7. `app/api/` — the HTTP surface

```python
@router.patch(
    "/{watchlist_id}/stocks/{stock_id}",
    response_model=WatchlistDetailOut,
    summary="Move a stock within a watchlist",
    responses={**UNAUTHORIZED_RESPONSE, **ENTRY_NOT_FOUND_RESPONSE, **INVALID_POSITION_RESPONSE},
)
async def move_watchlist_stock(
    watchlist_id: WatchlistPath,
    stock_id: StockPath,
    body: WatchlistEntryUpdate,
    user: CurrentUser,
    service: WatchlistServiceDep,
) -> WatchlistDetailOut:
    return await service.reorder_stock(
        body, watchlist_id=watchlist_id, stock_id=stock_id, owner=user
    )
```

**One line of body.** No `try`, no `if`, no session, no `HTTPException` — the middleware maps
the domain error into the one envelope. Over ~15 lines means logic leaked in; push it down.

- The router module owns `prefix="/watchlists"` and `tags`; `app/api/v1/__init__.py` adds a
  two-line include. **Never spell `/v1` in a path decorator** — the version lives on the
  aggregating router.
- A protected route annotates `user: CurrentUser` and passes it to the service as `owner`.
  Authorization is not data access, which is why no repo offers an "owned by" query.
- **Declare a literal segment before a parameterised one** — `/users/me` above
  `/users/{user_id}` — because Starlette matches in declaration order and the reversed order
  turns `/me` into a failed attempt to parse `"me"` as a UUID. The trap only bites *within
  one segment*: `/{x}` never matches a `/`, so `/stocks/by-ticker/{ticker}` cannot be
  shadowed by `/stocks/{stock_id}`. Declare literal-first anyway, and if a test claims the
  ordering is load-bearing, make it prove that against a control app with the declarations
  reversed rather than asserting a comment.
- **Document the refusals in `responses=`.** They become the `/docs` page and the generated
  client, and they are where a caller learns that a 404 here has three causes that
  `details.resource` distinguishes.
- Normalising an identifier is the **service's** job, not the schema's. An annotated type's
  `BeforeValidator` does apply to a path parameter — that was verified, not assumed — but it
  only covers callers arriving over HTTP, and a Celery task must obey the same rule.

---

## The tests that ship with it

Every ticket adds tests. Full guide: [`testing.md`](./testing.md).

| Tier | File | What it proves |
| --- | --- | --- |
| unit | `backend/tests/unit/test_domain_watchlist.py` | the rule, exhaustively — every ordering, empty lists, drifted ordinals, both refusals. No fixtures, no I/O |
| unit | `backend/tests/unit/test_services_watchlist.py` | the service against in-memory fakes, including the derived ownership sweep |
| api | `backend/tests/api/test_watchlists.py` | status codes, the error envelope, auth enforcement, and that a cross-account id is 404 with the same body as a missing one |
| integration | `backend/tests/integration/test_repos_watchlist.py` | the SQL, against real Postgres in a rolled-back transaction |
| integration | `backend/tests/integration/test_services_watchlist.py` | the things only real SQL can prove — constraint names, cascade behaviour |

Two rules decide which file a test goes in:

- **Cover an edge case in `unit/`, not through the API.** If you are reaching for the API
  tier to test a rule, the rule is probably in the wrong layer.
- A service test belongs in `unit/` when a fake repo answers the question, and in
  `integration/` when only real SQL can.

An API-tier test overrides the resource's `get_x_service` and **nothing else**, pointing it
at a real service built on an in-memory repo rather than a hand-written stub of the service
itself — so the route, the middleware, the error envelope and the service's own branches are
all genuinely under test with no database and no skip.

Assert error bodies with `assert_error_envelope` from `backend/tests/helpers.py`. It is the
error contract spelled out in exactly one place.

---

## Checklist

1. Model change? Migration in the same commit, and `alembic check` is clean.
2. Query written? It is in `app/repos/`, it ends in `flush()`, and it names every
   relationship the caller will touch.
3. Rule written? It is in `app/domain/`, it takes plain data, and it reads no clock and no
   `uuid4()`.
4. Schema written? Its caps are imported from the model, and `| None` matches the column.
5. Service written? One method per use case, keyword-only, a schema out, domain errors,
   `commit()` here, ownership through one gate.
6. Dependency written? One `get_x_service` factory and its `Annotated` alias. Nothing else.
7. Handler written? One service call, no `try`, no `if`, refusals documented in `responses=`,
   literal paths declared before parameterised ones.
8. Tests at every layer, and the edge cases in the unit tier.
9. New env var? It is in `.env.example` in the same commit — and, if this ever deploys, in
   `backend/infra/modules/compute/locals.tf`, which is asserted equal to `Settings`' fields
   in both directions.
10. New framework-level convention? Append it to [`../../CLAUDE.md`](../../CLAUDE.md).
    Ticket-specific detail does not go there.
11. `./scripts/lint.sh` and `./scripts/test.sh` are both green.
