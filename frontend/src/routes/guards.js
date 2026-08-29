import { redirect } from '@tanstack/react-router'

import {
  DEFAULT_AUTHENTICATED_ROUTE,
  LOGIN_ROUTE,
  REDIRECT_SEARCH_PARAM,
} from './paths'

/**
 * The route guards (ANV-27). CLAUDE.md §5: **auth guarding happens in the router**, via
 * `beforeLoad` redirects — not by rendering a `<RequireAuth>` wrapper.
 *
 * ---------------------------------------------------------------------------------------
 * ## Why `beforeLoad` and not a wrapper component
 *
 * The old app's `RequireAuth` was a route *element*: it rendered, read `useAuth()`, and
 * returned `<Navigate to="/login">`. That means the protected branch is entered before the
 * decision is made — the route's loader runs, its component mounts, its effects fire a
 * protected request, and only then does a second render unwind it. `beforeLoad` runs
 * *while the navigation is being resolved*, before any of the destination's code exists,
 * so a refusal never renders anything and never issues a request. It is also the only
 * place a redirect composes with the rest of the load: throwing a `redirect()` cancels the
 * pending navigation outright rather than racing it.
 *
 * ## There is no boot window to gate
 *
 * The backlog called for "a pending component covering the silent-refresh boot window".
 * There is no such window. ANV-26's `restore()` is a **synchronous** `localStorage` read
 * performed in a ref initialiser during `AuthProvider`'s first render, so
 * `context.auth.isAuthenticated` is a settled boolean the first time `beforeLoad` ever
 * runs. Nothing here awaits, and there is nothing for a `pendingComponent` to cover.
 *
 * The cost of that, stated by ANV-26 and inherited here: `isAuthenticated` is
 * **provisional**. A guard can admit a user whose refresh token the server has already
 * invalidated. The guard is deliberately not made authoritative — doing so would mean an
 * `await`ed round trip on every protected navigation, which is the blocking spinner
 * `PersistLogin` was, moved one layer down. `onSignOut` corrects it instead: the first
 * protected call 401s, the refresh fails, and the session ends with a redirect that knows
 * where the user was.
 */

/**
 * A throwaway origin for parsing a value already known to start with `/`. It exists so
 * `new URL` has a base; nothing ever reads it back, and `.invalid` is the reserved TLD
 * guaranteed never to resolve if one ever leaked.
 */
const SAFE_BASE = 'http://anvex.invalid'

/**
 * Reduce a `redirect` search value to something safe to navigate to, or `undefined`.
 *
 * The value arrives from the URL bar, so it is attacker-controlled: a link to
 * `/login?redirect=https://evil.example/harvest` would otherwise turn our own login page
 * into an open redirect, and a phished user would land off-site immediately after
 * authenticating, at the moment they are most primed to type a password again. Only a
 * same-site *path* survives:
 *
 *  - it must start with `/` — rejecting `https://…`, `javascript:…` and a bare word;
 *  - it must not start with `//` — a protocol-relative URL is off-site;
 *  - it must not start with `/\` — browsers normalise the backslash, so `/\evil.example`
 *    is `//evil.example` by the time it is followed.
 *
 * `/login` itself is also rejected, whatever its query string. It is never a meaningful
 * destination — a `?redirect=/login` would make `redirectIfAuthenticated` bounce an
 * authenticated user from the login page back to the login page, for ever — and it is
 * reachable by accident as well as on purpose, since a sign-out that happened while the
 * login page was open would otherwise record the login page as "where they were".
 *
 * Anything rejected becomes `undefined` rather than an error: a malformed `redirect` is
 * not a reason to refuse to show somebody a login form.
 */
export function sanitiseRedirect(value) {
  if (typeof value !== 'string' || value === '') return undefined
  if (!value.startsWith('/')) return undefined
  if (value.startsWith('//') || value.startsWith('/\\')) return undefined
  if (new URL(value, SAFE_BASE).pathname === LOGIN_ROUTE) return undefined
  return value
}

/**
 * `validateSearch` for any route that accepts a `redirect` param.
 *
 * Sanitising at the route's edge rather than at each read is what makes the safety
 * property checkable: `search.redirect` is either absent or a same-site path, everywhere,
 * and no consumer has to remember. Unknown params are dropped, so the search object a
 * component sees is exactly the one this route declares.
 */
export function validateRedirectSearch(search) {
  const value = sanitiseRedirect(search?.[REDIRECT_SEARCH_PARAM])
  return value === undefined ? {} : { [REDIRECT_SEARCH_PARAM]: value }
}

/**
 * Turn an internal href (`/research?tab=news#top`) into `redirect()` navigation options.
 *
 * `redirect({ href })` exists but is documented for **external** redirects and infers
 * `reloadDocument`, which would reload the whole app and throw away the in-memory access
 * token — the opposite of what "return to where you came from" should cost. So the href is
 * split into the `{ to, search, hash }` triple the router builds a location from.
 *
 * The base is a throwaway origin: `sanitiseRedirect` has already guaranteed a leading `/`,
 * so the origin is never read and only the path, search and hash come back out.
 */
function toNavigationOptions(href) {
  const url = new URL(href, SAFE_BASE)
  const search = Object.fromEntries(url.searchParams)
  return {
    to: url.pathname,
    search,
    hash: url.hash === '' ? undefined : url.hash.slice(1),
  }
}

/**
 * `beforeLoad` for a protected route: admit a session, or send an anonymous visitor to
 * `/login` carrying where they were going.
 *
 * `location.href` — not `location.pathname` — so a deep link's query string and hash
 * survive the round trip. `replace: true` keeps the refused URL out of the history stack:
 * pressing Back from the login page should return to wherever the user actually came
 * from, not bounce off the guard a second time.
 */
export function requireAuth({ context, location }) {
  if (context.auth.isAuthenticated) return

  throw redirect({
    to: LOGIN_ROUTE,
    search: { [REDIRECT_SEARCH_PARAM]: location.href },
    replace: true,
  })
}

/**
 * `beforeLoad` for `/login`: bounce a user who already has a session.
 *
 * This is **both** halves of the redirect round trip, which is why it is one function.
 * It covers "an authenticated user typed /login" *and* "the login form just succeeded" —
 * because a successful login flips `isAuthenticated`, `App.jsx` invalidates the router,
 * this guard re-runs, and the user is sent to `search.redirect`. So ANV-29's login page
 * has no navigation code in it at all: it calls `auth.login()` and the router does the
 * rest. Two implementations of "where do they go now" is how the old app ended up
 * hardcoding `/research` in `Login.jsx` while `RequireAuth` remembered somewhere else.
 */
export function redirectIfAuthenticated({ context, search }) {
  if (!context.auth.isAuthenticated) return

  const target = sanitiseRedirect(search?.[REDIRECT_SEARCH_PARAM])

  throw redirect({
    ...(target === undefined
      ? { to: DEFAULT_AUTHENTICATED_ROUTE }
      : toNavigationOptions(target)),
    replace: true,
  })
}
