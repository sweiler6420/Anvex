import { createRoute } from '@tanstack/react-router'

import LoginPage from '@features/auth/components/LoginPage'

import { redirectIfAuthenticated, validateRedirectSearch } from './guards'
import { rootRoute } from './root'
import { LOGIN_ROUTE } from './paths'

/**
 * `/login` — public, and the one route that carries a search param.
 *
 * Three declarations, in the order the router applies them:
 *
 *  1. **`validateSearch`** parses and *sanitises* `?redirect=`. It is the route's edge, so
 *     an off-site value is dropped before anything reads it (see `guards.js`).
 *  2. **`beforeLoad`** bounces a user who already has a session — to `search.redirect` if
 *     there is one, otherwise to `DEFAULT_AUTHENTICATED_ROUTE`. This is the *same* code
 *     path that lands a user after a successful sign-in, which is why the login form owns
 *     no navigation.
 *  3. **`component`** is `LoginPage` (ANV-29). It reads **nothing** from this route —
 *     not `Route.useSearch()`, not the `redirect` param, not `DEFAULT_AUTHENTICATED_ROUTE`:
 *     the guard above owns the destination, so the form owns no navigation. (ANV-27 left a
 *     note here guessing the page would want `useSearch()` for "you were signed out"
 *     messaging; there is no such message on this page, so it does not.)
 */
export const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: LOGIN_ROUTE,
  validateSearch: validateRedirectSearch,
  beforeLoad: redirectIfAuthenticated,
  component: () => <LoginPage />,
})
