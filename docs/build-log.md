# Anvex build log

Status and live context for the monorepo build. **Read this first after any session restart** — it
is the handoff document, and it is kept short on purpose.

| Read | For |
| --- | --- |
| this file | current status, environment traps, and the carry-overs still outstanding |
| [`../CLAUDE.md`](../CLAUDE.md) | the architecture contract — what goes where, and why |
| [`backlog.md`](./backlog.md) | the ticket specs, in execution order |
| [`ticket-log.md`](./ticket-log.md) | the archive: what each completed ticket decided and found |

## How this build runs

- The backlog in `docs/backlog.md` is executed **sequentially**, one ticket at a time.
- Each ticket is delegated to a **single clean subagent**, never run in parallel.
- Every ticket ships tests for its own changes and appends framework-level conventions to
  `CLAUDE.md`.
- Tickets live in **Linear**, team **Anvex**, project
  [Anvex — Monorepo Build](https://linear.app/reality-drift/project/anvex-monorepo-build-7dc988651385).
  Linear's `ANV-N` numbering matches `backlog.md` exactly.

## Environment facts that bite

| Fact | Consequence |
| --- | --- |
| `uv` at `C:\Users\sweil\.local\bin\uv.exe`, **not on PATH** | prepend it in every Python command block |
| Stale `VIRTUAL_ENV` → `AverageInvestorApi\venv` | must be cleared (`$env:VIRTUAL_ENV = $null`) or uv targets the wrong env |
| **A native `postgresql-x64-18` service owns host port 5432** (auto-start, PID confirmed) | compose publishes `db` on **5442**. On Windows the native server *and* Docker's proxy both bind 5432 successfully, so a host client silently reaches the wrong database with no error |
| **`uv run pytest` is blocked by an Application Control policy** (`os error 4551`) | always use `uv run python -m pytest`; `uv run ruff` is fine |
| No `node` / `npm` installed | all frontend tooling runs **inside Docker** |
| `gh` CLI installed 2026-08-28, on the *user* PATH, and **not authenticated** | older shells need the full path; `gh run watch` is unavailable — the repo is public, so poll `api.github.com/repos/sweiler6420/Anvex/actions/runs` instead (logs are 403, step conclusions are not) |
| Docker daemon frequently stopped | check before depending on it |
| Bash tool sandbox has a minimal PATH | use the PowerShell tool for uv / docker / git |

## Legacy repos — read-only

`AverageInvestorApi`, `AverageInvestorService`, `AverageInvestorWeb` under
`C:\Users\sweil\OneDrive\Documents\Projects\AverageInvestor\` are reference only and must never be
modified. What each contributes to Anvex:

- **Api** — FastAPI, sync SQLAlchemy 1.4, `avg_inv` schema. Tables `users`, `stocks`, `stock_data`,
  `watchlists`, `watchlist_data`, `politicians`. Routers: auth (JWT access+refresh), users, stocks,
  stock_data, watchlist (with reorder), news (hardcoded blob). Config via
  `settings/config.{env}.json`.
- **Service** — AlphaVantage 5-min intraday → pandas → Postgres, with EC2/Lambda glue. Becomes
  Celery jobs + an AlphaVantage client.
- **Web** — CRA + React 18 + Tailwind + react-router v6. Auth context + `PersistLogin`/`RequireAuth`
  + axios refresh interceptors. Pages: Home (already Anvex-branded), Login, SignUp, Recovery,
  Unauthorized, Research/Portfolio (placeholders). Plus the ~1200-line `binpacking` window system
  and its widgets.

---

## Status

| Ticket | Title | Status |
| --- | --- | --- |
| ANV-1 | Monorepo scaffold and architecture contract | **Done** |
| ANV-2 | Backend uv project and settings | **Done** |
| ANV-3 | Async database layer and Alembic | **Done** |
| ANV-4 | App factory, middleware and error contract | **Done** |
| ANV-5 | Docker Compose stack and backend Dockerfile | **Done** |
| ANV-6 | Pytest harness | **Done** — *E1 Foundation complete* |
| ANV-7 | Models and initial migration | **Done** |
| ANV-8 | Pydantic schemas | **Done** |
| ANV-9 | Repositories | **Done** — *E2 Data layer complete* |
| ANV-10 | Security utilities and pure token domain | **Done** |
| ANV-11 | Auth service, dependencies and routes | **Done** |
| ANV-42 | Drop passlib, hash with bcrypt directly | **Done** *(inserted — Stephen's call)* |
| ANV-12 | Users service and routes | **Done** — *E3 Auth complete* |
| ANV-13 | Stocks service and routes | **Done** |
| ANV-14 | Stock data service and routes | **Done** |
| ANV-15 | Watchlists — reorder domain, service and routes | **Done** |
| ANV-16 | Politicians seed data, service and routes | **Done** — *E4 Core features complete* |
| ANV-17 | Client base | **Done** |
| ANV-18 | AlphaVantage client | **Done** |
| ANV-19 | NewsAPI client, service and routes | **Done** |
| ANV-20 | S3 client and storage service | **Done** |
| ANV-21 | Celery application and worker wiring | **Done** |
| ANV-22 | Stock ingest job | **Done** — *E5 complete; **the backend is done*** |
| ANV-23 | Vite scaffold, Tailwind and test harness | **Done** |
| ANV-24 | API client layer | **Done** |
| ANV-25 | Theme and error providers | **Done** |
| ANV-26 | Auth state and token lifecycle | **Done** |
| ANV-27 | TanStack Router and route guards | **Done** |
| ANV-28 | Layout, header and dark-mode switcher | **Done** — *frontend foundation complete* |
| ANV-29 | Login page | **Done** |
| ANV-30 | Sign-up page | **Done** |
| ANV-31 | Recovery and Unauthorized pages | **Done** |
| ANV-32 | Home marketing page | **Done** |
| ANV-33 | Bin-packing window system | **Done** |
| ANV-34 | Dashboard widgets | **Done** |
| ANV-35 | Interactive desktop demo | **Done** |
| ANV-36 | Research and Portfolio pages | **Done** — *the frontend is complete* |
| ANV-43 | Backend password strength policy | **Done** — *found by ANV-30; E3 gap closed* |
| ANV-37 | Developer scripts | **Done** |
| ANV-38 | CI pipeline | **Done** — *green on the first push* |
| ANV-39 | Documentation and ADRs | Next |
| ANV-40 … ANV-41 | see `backlog.md` | Not started |

**4,224 tests total** — **3,302 backend** (2,919 with Docker stopped, DB/S3/broker tiers skipping)
and **922 frontend**, all in-container. 99% backend coverage; `ruff check`, `ruff format --check`
and `eslint` all clean. Container tiers genuinely execute: **276 DB**, **29 S3**, **9 broker**.

**Run everything through `scripts/`** (ANV-37): `up`, `down`, `logs`, `migrate`, `makemigration`,
`seed`, `test`, `lint`, `fmt`, `reset-db`, each a `.ps1`/`.sh` pair taking the same command line.
`scripts\test.ps1` is the whole suite, `scripts\lint.ps1` is ruff + `ruff format --check` + eslint.
They already encode every trap in the table above, so a session that uses them cannot fall into one.

**Cosmetic issues in the ported marketing copy, flagged and deliberately NOT changed** (they are
Stephen's, and each changes appearance):
- `Features` carries **`sm:1/2`** — a typo for `sm:w-1/2`, so it is not a Tailwind class at all and
  the cards stay one-per-row until `lg`. One-line fix, but it changes the `sm` layout.
- Only `Pricing`'s **middle card has `h-full`**, so at `sm` the Pro card stretches and its
  neighbours do not.
- `Footer`'s `border-neutral-700` has **no light variant** — a near-black top border in light mode.
- `text-lg`, `text-md`, `lg:text-6xl` are **not in this Tailwind `fontSize` scale** (it stops at
  `5xl`), so they emit nothing.
- The Contact "sending is not connected yet" sentence is **the agent's wording**, not Stephen's —
  the old page never admitted the button was dead. Replace freely.

`AverageInvestorService` is now fully replaced and can be deleted. Two things it did that Anvex
does not yet: **deep historical backfill** (its 43-month sweep — `ingest_month` accepts any explicit
month, but nothing infers a gap older than the watermark, so closing one means dispatching months by
hand) and the **EC2/Lambda deployment glue** (deliberately gone, not ported — beat + a worker
container is the equivalent). Both are ticket-sized additions, neither needs the old code.

---

## Active carry-overs

Only what is still outstanding. Once a ticket consumes one of these, delete it — the full record
stays in [`ticket-log.md`](./ticket-log.md).

**For any ticket writing a service — the sweep pattern (new, from ANV-15):**
Any property that must hold for *every* use case (auth required, resource noun stable, no commit on
a read) is better expressed as one parameterised sweep whose case list is **derived** from
`vars(XService)` and asserted complete, than as N hand-written tests one of which will be forgotten.
ANV-15's ownership sweep fails the suite if a new use case is added without isolation coverage.

**For any ticket writing a service:**
- Pre-check for the message, constraint for the correctness: keep `*_exists` so the 409 can name
  `details.field`, *and* catch `IntegrityError` — with `await session.rollback()` **first**, because
  Postgres aborts the transaction and refuses everything after it.
- Translate `app/utils/` exceptions at the service; utils raise builtins by layering rule, and
  uncaught they become 500s for input the API should have refused.
- Add fakes **beside** the existing ones in `tests/helpers.py`, and keep them faithful to the
  awkward parts of the real repo — a forgiving fake silently passes the bug the test exists to catch.

**Frontend mechanics (established by ANV-23):**
- **Node is not installed on this machine, by choice.** Run commands in the container:
  `docker compose --profile frontend exec web npm run test`, or one-shot
  `docker run --rm -v "<repo>:/repo" -w /repo/frontend anvex/web:dev npm run build` — **mount the
  repo root**, because `envDir` reaches one level up to the shared `.env`.
- **`node_modules` lives at `/node_modules`, not `/app/node_modules`** (compose bind-mounts
  `./frontend` over `/app` and would hide it — same argument as the backend's `/opt/venv`). **There
  is no node_modules volume**, so a dependency change is `up -d --build`, never a stale volume.
- **Never set `NODE_ENV=development` in the Dockerfile.** Vite honours an inherited `NODE_ENV` over
  its own mode, so it silently ships a *development* React build — 330 kB with `jsxDEV`, versus
  145 kB. ANV-23 hit this.
- **Tailwind is v3.4 deliberately** — v4 is its own ticket, *after* the component ports, because its
  default changes (border-color → `currentColor`, ring 1px, `shadow-sm` renamed,
  `outline-none` → `outline-hidden`) would silently restyle every ported component.
- **`src/lib/env.js` is the only module that touches `import.meta.env`.** Import `API_BASE_URL` /
  `apiUrl(path)`. **An empty `API_BASE_URL` is meaningful** (same-origin, proxied) — never
  `if (!API_BASE_URL) throw`.
- **Mock at the network boundary with MSW**, never by stubbing `fetch`/`axios` — that is what keeps
  interceptors under test rather than mocked away. Use `errorResponse()` / `pageResponse()` from
  `src/test/msw/handlers.js` so a mock cannot invent a body the backend would never send.
- Aliases `@`, `@lib`, `@components`, `@features`, `@hooks`, `@providers`, `@routes`, `@styles`,
  `@test` are live in both Vite and vitest.
- Gotcha: vitest stubs `.css` imports (including `?raw`), and `import.meta.url` is an `http:` URL
  inside vitest — read files from disk relative to `process.cwd()`.

**The API client layer (ANV-24) — what ANV-25/26 plug into:**
- **`installTokenStore({getAccessToken, getRefreshToken, setTokens, clear})`** is the seam.
  `client.js` calls `getTokenStore()` **per request, never at import**, so a provider mounting later
  works. `setTokens` receives the **whole rotated pair** — storing only the access token breaks the
  next refresh. **`clear()` is where the redirect to `/login` belongs**; the transport deliberately
  never navigates, which is what makes it testable outside React.
- **Every failure is an `ApiError` with a `code`.** The five client-side codes (`network_error`,
  `timeout`, `request_cancelled`, `malformed_response`, `unknown_error`) are **disjoint from every
  backend code**, asserted by test — so one `switch (err.code)` covers both origins and nobody
  writes `if (!err.response)`. `request_cancelled` should be **swallowed silently**: it is a
  component unmounting, not a failure.
- **Only a refusal ends the session.** `clear()` fires on a 4xx from the refresh endpoint — *not* on
  a network failure or a 5xx. Signing a user out because their wifi blipped discards tokens that are
  still valid.
- Refresh fires on **any 401 except** `invalid_token` / `wrong_token_type`. A page reload holds a
  refresh token but no access token, so its first protected call is a 401 `unauthorized` — exactly
  the case refresh exists to rescue. **For persist-login, just fire the first protected call.**
- `POST /v1/auth/login` is **form-encoded** (`OAuth2PasswordRequestForm`), so it needs an explicit
  `Content-Type` override of the instance default. Login and recovery go on **`publicApi`**.
- Import from `@lib/api`, not the submodules. **There is deliberately no resource module** —
  per-resource `api.js` belongs to each feature.

**The providers (ANV-25) — what ANV-26/27/28 plug into:**
- **`useDarkMode()` → `{theme, isDark, setTheme, toggleTheme}`.** The old `DarkModeSwitcher` and
  `Header` destructure `{theme, toggleTheme}`, so those ports are a straight copy. **The provider
  owns the `light`/`dark` class on `<html>`** — no other component may touch it.
- **`useErrors()` → `{error, isNetworkFailure, setError, clearError}`.** `error` is an `ApiError` or
  `null`. `setError` accepts anything throwable and normalises with `toApiError`, so
  `catch (err) { setError(err) }` is the whole integration. `isNetworkFailure` is the derived
  "cannot reach the server" flag, so a banner need not import `@lib/api`. `ERROR_TIMEOUT_MS` (10s)
  is exported if a banner wants a countdown.
- **`request_cancelled` is swallowed and leaves any existing error on screen** — an unmounting
  sibling must not wipe a real login failure.
- **A logout triggered by `clear()` should not go through `setError`** — it is a navigation, not a
  failure.
- `ThemeProvider` owns only the `localStorage` key `theme`; nothing collides with ANV-26's tokens.
  `main.jsx` currently nests `ThemeProvider > ErrorsProvider > App`; mount the auth provider
  **inside** `ErrorsProvider` if it wants to raise through `useErrors`.
- Contexts default to `null` and both hooks **throw** naming the missing provider, rather than
  handing back a half-working default the way the old `ErrorsContext` did.
- **Known gap, deliberately left for whoever owns the shell (ANV-28):** the theme class is applied
  in an effect, so a first paint before React mounts still flashes the light default. The fix is a
  two-line blocking script in `index.html`.

**Auth (ANV-26) — what ANV-27/29 plug into:**
- **`useAuth() → { isAuthenticated, login, logout, restore }`.** Deliberately **no `accessToken`** —
  the transport reads it through the seam, so there is exactly one copy. Tokens live in refs;
  `isAuthenticated` is the only state, so a silent refresh does not re-render the app.
- **`restore()` is synchronous and there is no boot-pending state.** A stored refresh token means
  "provisionally signed in", known during the first render. ANV-24's interceptor already
  refreshes-and-replays the first protected 401, so an explicit boot refresh would cost a round trip
  on every load, **spend a rotation to learn nothing**, and fail while the user is on a public page.
  **ANV-27's spec line about "a pending component for the silent-refresh boot window" no longer
  applies — there is no such window.**
- **`isAuthenticated` is provisional.** A guard can admit a user whose refresh token the server has
  already killed; `onSignOut` is what corrects it.
- **The redirect seam is `onSignOut({reason})`, a prop on `AuthProvider`** — a prop, not the router,
  so the store mounts and tests without one. It fires **at most once per session**, so three
  requests hitting one dead session produce one navigation. **ANV-27 passes it in `main.jsx`:**
  `session_expired` → `/login?redirect=<current path>`, `logout` → plain `/login`.
  Mount `AuthProvider` **above** `RouterProvider`.
- **`login` rejects with an `ApiError`** rather than raising through `useErrors` — a bad password
  belongs beside the password field, not in a 10-second global banner.
- **"Remember me" primitives live in `features/auth/authStorage.js`** (`readRememberedUsername`,
  `rememberUsername`) and **ANV-29 owns the checkbox**. Username only. **Logout does not forget the
  username.** There is no password prefill — that is the bug being removed.
- `LOGIN_PATH` / `RECOVERY_PATH` are exported so nothing hardcodes a URL. `requestRecovery({username})`
  is already written and tested for ANV-31.

**Two test-harness traps found in ANV-26 — they will bite you too:**
- **`vi.spyOn(window.localStorage, 'getItem')` is a no-op** on jsdom's Proxy: it stores an item
  *named* `"getItem"` and leaves the real method in place. Spy on **`Storage.prototype`** instead.
  Two of ANV-26's own storage-failure tests passed vacuously until it caught this.
- **A rejection escaping `act()` unbalances React's acting depth**, which makes the *next* test's
  `render()` silently not flush — it presents as a null context in an unrelated test.

**Routing (ANV-27) — what ANV-28→36 plug into:**
- **`rootRoute.component` is `() => <Outlet />`** in `src/routes/root.jsx`. **ANV-28 wraps that
  outlet in `Layout`/`Header`** — every route, public ones included, sits under it, matching the old
  `<Route path="/" element={<Layout/>}>`.
- **Every route is a `RoutePlaceholder`** with a `data-testid`. Replacing one is a single-line edit
  in the route module (`component: () => <LoginPage />`). `NotFound.jsx` is a **real** page and can
  stay.
- **Links import from `@routes/paths`** (`HOME_ROUTE`, `LOGIN_ROUTE`, …) — nothing hardcodes a URL —
  and use TanStack's `<Link to={...}>`.
- **ANV-29's login page contains no navigation code at all.** A successful login flips
  `isAuthenticated`, `App` invalidates the router, and `/login`'s own guard performs the bounce —
  so the guard is both halves of the redirect round-trip. (The old app hardcoded `/research` in
  `Login.jsx` while `RequireAuth` separately remembered somewhere else.)
- **`redirect` search params are sanitised at the route edge** by `sanitiseRedirect`. Only a
  single-leading-`/` same-site path survives. **Do not read the raw param anywhere** —
  `search.redirect` is either absent or safe, everywhere.
- **Only a *gained* session invalidates the router.** A lost one must not: `router.navigate` is
  async, and a simultaneous invalidation can still see the protected match and issue a competing
  `/login?redirect=…` for a logout meant to land on plain `/login`. **`onSignOut` is the single
  authority on where a sign-out goes.**
- A logout button calling `useAuth().logout()` is what makes the `logout` branch reachable
  end-to-end — **ANV-28 should add that end-to-end test when the button lands.**
- `src/test/setup.js` now no-ops `window.scrollTo`; anything mounting a router inherits it.
- **ANV-25's flash-of-light gap is ANV-28's:** the theme class is applied in an effect, so first
  paint before React mounts shows the light default. Two-line blocking script in `index.html`.

**Layout and header (ANV-28) — what every page ticket plugs into:**
- **`Layout` is `rootRoute.component`**, so every route including the 404 renders under it. A page
  ticket replaces `component: () => <RoutePlaceholder …/>` in its route module with the feature
  component — **one line, nothing else in the route changes.** Do not add a header or a page wrapper.
- **Pages contain no navigation code** (ANV-27's rule). The header's logout button is the only
  sign-out trigger.
- **Use `<Link>`, never `<a href>`, for internal destinations.** The old app's `<a href='/login'>`
  is a **full document navigation**, which reloads the bundle and **discards the in-memory access
  token**. An `href` assertion cannot discriminate the two — write a test that *clicks* and asserts
  the router moved.
- `@components/ui/icons` exists (hand-rolled, four MIT paths). Add icons there, not inline.
- **Do not touch `providers/themeStorage.js`'s key or rule without re-running
  `themeStorage.test.jsx`** — it holds the single definition the pre-paint script and
  `ThemeProvider` are both built from, and its matrix test is what stops them drifting.
- **Test harness:** anything mounting the router needs `ThemeProvider` + an `AuthContext.Provider`
  around it (the *same* `auth` object in both React and router context). `Header.test.jsx`'s
  `renderAt` is the copyable helper. The desktop and drawer copies are both in the DOM under jsdom,
  so scope with `within()` using `header-desktop-actions` / `header-drawer` / `header-desktop-nav`.
- **Not ported, deliberately:** the old header's ~80-line `IntersectionObserver` scroll-spy. Its
  sections arrive with ANV-32, and jsdom has neither layout nor `IntersectionObserver`, so it would
  be unverifiable code observing elements that do not exist. Active state comes from the router's
  location instead. **ANV-32 should decide whether it wants scroll-spy back.**

**The page/form pattern (ANV-29) — every page ticket copies this:**
Five parts, in order: local `useState` per field (a half-typed form is not application state); a
**pure** validator called first with an early return, so an invalid submit **never reaches the
network**; one `await` on the feature operation; `catch (err)` → `toApiError(err)` → branch on
**`err.code`** and display `err.message`; **no navigation at all**. A `submitting` flag guards the
handler *and* disables the button (two different failure modes) and is deliberately **not** cleared
on success — the guard is already unmounting the page.
- **Keep the placeholder's `data-testid`** (`route-sign-up`, etc.) so the ANV-27/28 routing tests
  need no edits.
- **ARIA the old pages lacked entirely:** `htmlFor`/`id` on every control (the old login had
  **zero** of each, so neither field had an accessible name), `aria-invalid` + `aria-describedby` on
  a failed field, and `role="alert"` slots **rendered unconditionally and left empty** — a live
  region must exist *before* its text arrives, since inserting region and text together is the case
  screen readers handle worst. The old visibility toggle was `onClick` on an `aria-hidden` `<svg>`:
  no tab stop, no role, unusable from a keyboard.
- Write **both** harnesses: a `renderLogin`-style one (real router, stubbed `AuthContext`) for
  validation/ARIA/failures, and a full-`App` + MSW one for anything about destinations or storage.

**The sign-up → login hand-off (for ANV-30):**
- `frontend/src/features/auth/handoff.js` — `navigate({to: LOGIN_ROUTE, replace: true, state: signUpHandoffState({username})})`.
- **Decision: hand off the username only, not the password.** Browsers persist session-history
  state to disk for tab restore, so a password passed this way can outlive the tab.
  `signUpHandoffState({username})` is complete and valid; the login page prefills the identifier and
  leaves the password empty.
- The hand-off **wins over** the remembered username — someone creating a second account must not be
  handed the first one's name.
- **Test trap:** `createBrowserHistory` *overwrites* an entry's whole state at startup when it finds
  neither `key` nor `__TSR_key` on it, so a hand-made `window.history.replaceState` fixture must
  include them. A real `navigate({state})` already does; this only bites test rigs.

**~~Open backend gap found by ANV-30~~ — closed by ANV-43.** `app/domain/password.py` mirrors the
four client rules including their Unicode definitions, `UserService.register` refuses a weak password
with a 422 naming the failed rules in `details.failed_rules`, and a backend test **parses
`SignUpPage.jsx`** so the two cannot drift. Consequence for any future frontend ticket: **editing
`PASSWORD_RULES` breaks a backend test** — an id, the order, a `label`, a `missing` phrase or a `met`
predicate. That is the point; change the server to match rather than deleting the assertion. The
one accepted divergence is documented at `test_the_one_known_divergence_is_astral_length…`: JS
`.length` counts UTF-16 units and Python counts code points, so a password of four astral characters
is 8 to the client and 4 to the server.

**API contract facts the UI must respect:**
- **Prices are quoted JSON strings**, not numbers — `"1234.5678"`. That is what preserves the fourth
  decimal; JSON numbers go through float and lose it. Chart code must `Number()` them.
- **`stock_data`'s `datetime` is naive on purpose** — no `Z`, no offset. It is the exchange's local
  clock, and stamping UTC on 09:30 ET would move every candle. Do not "fix" it.
- **The error envelope is fixed and every non-2xx uses it:**
  `{"error": {"code", "message", "details", "request_id"}}`, `details` always `{}` rather than
  `null`. **Branch on `code`, never on `message`.**
- **Auth codes to handle:** `token_expired` → refresh; `invalid_token` / `wrong_token_type` → log
  out. Refresh must be **single-flight** — the old app fired one refresh per concurrent 401 — and it
  triggers on **401**, not 403 (the old app had that backwards).
- **`POST /v1/auth/refresh` takes a JSON body**, not a query parameter.
- Routes are `/v1/auth/{login,refresh,recovery}`, `/v1/users` + `/me` + `/{id}`, `/v1/stocks` +
  `/{id}` + `/by-ticker/{ticker}`, `/v1/stocks/{id}/data`, `/v1/watchlists` (+ `/stocks` sub-routes),
  `/v1/politicians`, `/v1/news/{top,by-symbol/{ticker}}`.
- **Lists return `Page[T]` = `{items, total, limit, offset, has_more}`.**
- **Never persist a password.** The old app wrote it to `localStorage` for "remember me" — username
  only, and the access token stays in memory.

**Still unimplemented, deliberately:**
- **No mail client.** `POST /v1/auth/recovery` logs `delivered=False` and returns 202, behind a
  `TODO(ANV-mail)` that a unit test asserts is still present. Wiring real delivery needs its own
  ticket.
- **No deep historical backfill.** `ingest_month` takes any explicit month, but nothing infers a gap
  older than the watermark.
- **`download_url` exists but no route mounts it** — whether a presigned URL should leave the API is
  an open decision, not an oversight.


**For the remaining ops tickets — from ANV-43:**
- **ANV-39 (docs):** the registration 422 now carries `details.failed_rules` — a list of rule ids
  from `("length", "uppercase", "number", "symbol")`. Worth documenting beside the error envelope,
  because it is the first `details` value that is not a scalar.
- **ANV-41 (smoke):** any smoke script that registers an account needs a password satisfying the
  policy. `"Correct-horse-battery1"` is the value the suites standardised on.

**For the remaining ops tickets (ANV-39, 41) — from ANV-36:**
- **ANV-39 (docs):** **`/portfolio` is a documented non-feature, not a bug** — say so, or the first
  reader files it. Likewise: **the research desktop's window arrangement does not survive a
  reload**, which is the highest-value follow-up on that page. The cosmetic issues in the ported
  marketing and research copy are Stephen's and deliberate.
- **ANV-41 (smoke):** ANV-36's live-render technique is the right shape and is cheap — jsdom over
  `dist/`, a fake `XMLHttpRequest`, assertions on `data-testid`. **Two non-obvious requirements:**
  jsdom 25 defines neither `fetch` nor `Response`, and TanStack Router's redirect machinery does an
  `instanceof Response`, so Node's globals must be handed to the window or **every route load dies**
  with `ReferenceError: Response is not defined`; and jsdom has no `ResizeObserver`, so the desktop
  renders **empty** — a smoke test should assert the securities panel and the route testid, not a
  window. **Against a live API, the cold-load path is the single most valuable thing to smoke:**
  seed a refresh token, load `/research`, assert the securities list arrives — that exercises the
  guard, the interceptor, the rotation and the API in one go.
- **Known follow-ups worth tickets:** desktop layout persistence (needs an API endpoint — there is
  none), and a holdings model + quote source before `/portfolio` can be real.

**For the remaining ops tickets (ANV-39, 40, 41) — from ANV-37:**
- **ANV-40 (AWS skeleton):** `migrate`, `seed` and `reset-db` reach Postgres by translating `.env`'s
  `POSTGRES_HOST=db` to `localhost` plus the published port, in `_common` only. Anything that runs
  those against a real RDS just sets `POSTGRES_HOST` in the environment — the translation stands
  aside when it is set. Do not add a second DSN.
- **ANV-41 (smoke):** `scripts/smoke` should be a pair like everything else and will need a README
  row. The boot sequence it documents already exists and is verified: `up core` → `migrate` →
  `seed`, then `up frontend`. `reset-db --yes` is the clean-slate version of the same thing and
  runs end to end in about 25 seconds.
- **Where `fmt` stops:** it is backend-only, because the frontend has no formatter — eslint is a
  linter and prettier is not installed. Adding prettier is a repo-wide diff and wants its own
  ticket; until then `lint frontend` is the whole frontend story, and the `fmt` header says so.

**For the remaining ops tickets (ANV-39, 40, 41) — from ANV-38:**
- **CI exists and is green, so every later ticket has a verifier.**
  [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs `Backend` and `Frontend` on every
  push to `main` and every pull request; `backend/tests/unit/test_ci_workflow.py` (64 tests) is what
  guards it. **A push that only touches `docs/` runs neither job** — that is the path filter working,
  not CI being broken.
- **Adding a test that reads outside `backend/` means adding its path to the backend filter.**
  `test_ci_workflow.py` discovers those paths by scanning the test sources for `REPO_ROOT / …` and
  fails if one is not covered, so it will tell you — but it fails *after* you write the test, not
  before. Currently covered: `scripts/**`, `SignUpPage.jsx`, `frontend/package.json`, `README.md`,
  `CLAUDE.md`, `.env.example`, `docker-compose.yml`.
- **ANV-39 (docs):** the workflow's own header comment and `README.md`'s `## Continuous integration`
  section are the current CI documentation, and a test asserts the README section names both jobs and
  explains why the backend filter reaches into the frontend. **Two ADR-shaped decisions are already
  argued in prose and want a home:** CI calls `scripts/` for the backend but `package.json` for the
  frontend (the wrappers only add a container the runner does not need), and a path filter is
  treated as coverage-deleting — which is why the backend filter is wider than `backend/**`.
- **ANV-40 (AWS skeleton):** the workflow is **read-only** — `permissions: contents: read` plus
  `pull-requests: read`, no `id-token`, no secrets, no deploy step. Anything that pushes an image or
  assumes a role needs `permissions: id-token: write` on *that job only* and should be a separate
  workflow file rather than a third job here, so a deploy credential is never in scope for a test
  run. `POSTGRES_HOST` set in the environment is still the one supported way to point host-side
  tooling at RDS (from ANV-37) — CI does the same thing with `POSTGRES_TEST_HOST`.
- **ANV-41 (smoke):** a smoke job is a **third job in this workflow**, not a fourth top-level file,
  and it needs the boot sequence a service container cannot express (`up core` → `migrate` → `seed`)
  — so it wants `docker compose` on the runner rather than `services:`. If it adds `scripts/smoke`
  as a pair, remember the README table row, and remember that `test_ci_workflow.py` asserts every
  `./scripts/*.sh` the workflow names actually exists.
- **The S3 tier does not run in CI** — 14 tests skip. GitHub service containers can override an
  entrypoint but cannot pass arguments, and `minio/minio` needs `server /data`. An image with a
  default command, or a `docker run` step in a smoke job, are the two ways back; both are decisions
  rather than fixes.
- **`gh` is installed but unauthenticated**, so a session cannot watch a run through it. The repo is
  **public**, so `https://api.github.com/repos/sweiler6420/Anvex/actions/runs` and
  `…/actions/runs/<id>/jobs` answer unauthenticated and give per-step conclusions — enough to verify
  a run end to end. **Log bodies are 403 without a token**, so exact test counts have to come from
  the local run. 60 requests/hour: poll every 25 s, not every 2 s.
- **`actionlint` validates the workflow locally with no Docker**: the Windows binary from the
  `rhysd/actionlint` GitHub release runs standalone. `docker pull` failed on this machine with a DNS
  error for `production.cloudfront.docker.com`, which may recur.
