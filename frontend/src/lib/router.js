import { createRouter } from '@tanstack/react-router'

import { SIGN_OUT_SESSION_EXPIRED } from '@providers/AuthProvider'
import { LOGIN_ROUTE, REDIRECT_SEARCH_PARAM } from '@routes/paths'
import { routeTree } from '@routes/tree'

/**
 * The router factory (ANV-27). CLAUDE.md §5 puts "router config" in `lib/`; the routes
 * themselves live in `routes/`.
 *
 * **A factory, not a module-level singleton**, and that is a testing decision with teeth.
 * A router carries navigation state, a history and a match cache, so one shared instance
 * would let a test that navigates to `/research` decide where the *next* test starts —
 * the same argument that gives every test its own `render()`. `App.jsx` creates exactly
 * one, in a `useState` initialiser, so the application still has a single router for its
 * lifetime.
 *
 * The `context` seeded here is a placeholder. `<RouterProvider context={{ auth }} />`
 * overwrites it during render — before the initial route load runs, because
 * `RouterProvider` calls `router.update()` in its render body and the load happens in a
 * descendant's effect. The seed exists so `beforeLoad` can read `context.auth.…` without a
 * null check in the window between `createRouter` and the first render, and it is
 * anonymous because "not signed in" is the safe default for a guard.
 *
 * @param {{history?: import('@tanstack/react-router').RouterHistory}} [options]
 *   `history` is how a test starts at a given URL, via `createMemoryHistory`.
 */
export function createAppRouter({ history } = {}) {
  return createRouter({
    routeTree,
    context: { auth: { isAuthenticated: false } },
    // A protected route that redirects has nothing to scroll to; leaving restoration off
    // keeps ANV-28 free to decide it once, for the shell that actually scrolls.
    ...(history === undefined ? {} : { history }),
  })
}

/**
 * Where a sign-out lands — the ANV-26 `onSignOut({reason})` seam, expressed as
 * `router.navigate` options.
 *
 * A pure function of `(reason, currentHref)` rather than four lines inside `App`'s
 * callback, because the *branch* is the behaviour: an expired session must carry the user
 * back to where they were, and a deliberate logout must not. Swapping the two arms is a
 * plausible mistake that no rendered output would obviously contradict, so it gets a test
 * that names both.
 *
 *  - **`session_expired`** — the API refused the session mid-task. `/login?redirect=<href>`,
 *    so signing back in resumes rather than restarts.
 *  - **anything else (`logout`)** — they asked to leave. Plain `/login`; dragging somebody
 *    back to the page they walked away from is not what logging out means.
 *
 * `replace: true` either way: the page they were just thrown out of should not be one Back
 * press from a guard that will only throw them out again.
 *
 * @param {{reason: string, currentHref: string}} signOut
 */
export function signOutNavigation({ reason, currentHref }) {
  const search =
    reason === SIGN_OUT_SESSION_EXPIRED ? { [REDIRECT_SEARCH_PARAM]: currentHref } : {}

  return { to: LOGIN_ROUTE, search, replace: true }
}

export default createAppRouter
