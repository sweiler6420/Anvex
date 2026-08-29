import { createRoute } from '@tanstack/react-router'

import RoutePlaceholder from './RoutePlaceholder'
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
 *  3. **`component`** is the form. **ANV-29 replaces the placeholder**, and what it gets
 *     from here is `Route.useSearch()` — it does not need it for the redirect (the guard
 *     handles that) but it does for the "you were signed out" messaging.
 */
export const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: LOGIN_ROUTE,
  validateSearch: validateRedirectSearch,
  beforeLoad: redirectIfAuthenticated,
  component: () => <RoutePlaceholder title="Login" ticket="ANV-29" />,
})
