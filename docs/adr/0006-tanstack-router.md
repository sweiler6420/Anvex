# ADR-0006 — TanStack Router, not react-router

## Status

**Accepted** — ANV-27. Recorded in ANV-39.

## Context

**The ticket's premise was wrong, and that is worth recording rather than tidying away.**
ANV-27 was written as "replace react-router v6". `react-router-dom` was never installed in
Anvex: the premise described `AverageInvestorWeb`, the CRA application being ported from.
So this was not a migration. It was a choice of first router, made with the old app's
routing available as a worked example of what not to do.

What the old app did, and what each thing cost:

- **`RequireAuth` was a route *element*.** It rendered, read `useAuth()`, and returned
  `<Navigate>`. So the protected branch was entered *before* the decision was made: the
  loader ran, the component mounted, its effects fired a protected request, and a second
  render unwound it. A guard that renders is a guard that has already let you in.
- **`PersistLogin` awaited a refresh on every page load**, covering the entire route tree —
  public pages included — with a `"Loading..."` div until it settled. It spent a token
  rotation to learn something the first protected call would have discovered for free.
- **Two authorities disagreed about where a login lands.** `Login.jsx` hardcoded
  `/research` while `RequireAuth` separately remembered somewhere else.

The real question was therefore: which router lets a guard run *before* the destination
exists, and lets that guard be tested without a browser?

## Decision

**TanStack Router**, in **code-based** mode. One module per route under
`frontend/src/routes/`, assembled by `frontend/src/routes/tree.js`;
`frontend/src/lib/router.js` owns the router factory and the sign-out destination.

Guards are `beforeLoad` hooks that `throw redirect(...)`. Route URLs are constants in
`frontend/src/routes/paths.js`.

## Consequences

**A refusal renders nothing and requests nothing.** `beforeLoad` runs while the navigation
is being resolved, before any of the destination's code exists, and throwing `redirect()`
cancels the pending navigation instead of racing it. That is the property the old
`RequireAuth` could not have.

**Code-based routing was chosen over TanStack's file-based mode, and it is a real trade.**
File-based mode generates a `routeTree.gen.ts` from a Vite plugin: a generated file in the
source tree, a codegen step before `vitest` can resolve a route, and a second place — the
plugin config — that decides what a route is. Eight routes do not earn that. The cost is
that the tree is assembled by hand and a new route means editing `tree.js`; the benefit is
that a test imports `routeTree`, builds a router over a memory history, and runs the real
guards with no build step.

**Four of the library's defaults are wrong for this application and are overridden
explicitly.**

- `activeOptions={{exact: true, includeHash: true}}` on every nav `Link`. `exact: false`
  makes `/` a prefix of every route, so Home stays underlined on `/research`;
  `includeHash: false` lights up every item that shares a route, which on a marketing page
  with four fragments is all of them.
- The root route declares `validateSearch: () => ({})`. TanStack merges a parent match's
  search into its child's, and a route without a `validateSearch` passes the raw query
  string straight through — so without it, a child's sanitised param would sit in
  `Route.useSearch()` beside whatever else was in the URL.
- `redirect({ href })` is for *external* redirects and infers `reloadDocument`. Returning a
  user to an internal href therefore means splitting it into `{to, search, hash}`, because a
  full document reload discards the in-memory access token — the opposite of what "return to
  where you came from" should cost.
- `createAppRouter` is a **factory**, never a module-level singleton. A router carries
  navigation state, a history and a match cache, so one shared instance would let a test that
  navigates decide where the next test starts.

**The session reaches the router as a prop, not as an import**, and the mounting order is
load-bearing. `<RouterProvider context={{ auth }} />` merges the prop into `router.options`
during its own render, which is before the initial load fires from a descendant effect, so
the first `beforeLoad` already sees the real session. `AuthProvider` mounts *above*
`RouterProvider`, and installs its token store during **render** rather than only in an
effect — React runs effects bottom-up, so an effect-only install leaves a window in which the
router's initial route load issues a protected request against the anonymous default store.

**`isAuthenticated` is provisional, and the guard is deliberately not authoritative.**
Making it authoritative would mean an awaited round trip on every protected navigation —
`PersistLogin`'s blocking spinner, moved one layer down. A stored refresh token means
"provisionally signed in", known synchronously during the first render, and a guard can
therefore admit a user whose refresh token the server has already killed. The correction
happens after the fact, through the transport's `onSignOut` callback. **A page-load "am I
signed in" question is answered from storage; a page-load "is my session still good"
question is not asked.**

**Only a *gained* session invalidates the router.** A route already matched does not re-read
`context`, so a login must call `router.invalidate()` for the guards to re-run — that is what
completes the redirect round trip, and it is why a login page contains no navigation code at
all. A *lost* session must **not** invalidate: `router.navigate` is asynchronous, so a
simultaneous invalidation can still see the protected match and issue a competing
`/login?redirect=…` for a logout that was supposed to land on plain `/login`.

**The `redirect` search param is attacker-controlled and is sanitised at the route's edge.**
`sanitiseRedirect` keeps only a value starting with a single `/` — rejecting absolute URLs,
`javascript:`, protocol-relative `//host`, and `/\host`, which browsers normalise — and
rejects `/login` itself. Without it, the login page is an open redirect aimed at users at the
exact moment they are primed to type a password.

**A route module exports a route object and no component.** A `.jsx` file exporting both
loses React Fast Refresh (`react-refresh/only-export-components`), which is why
`frontend/src/routes/NotFound.jsx` is its own file. It is also why a *page* component never
lives in `routes/` — quite apart from Fast Refresh, `routes/Unauthorized.jsx` beside
`routes/unauthorized.jsx` is the same file on Windows and macOS.
