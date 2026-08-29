/**
 * The application's URL vocabulary (ANV-27).
 *
 * One module, no imports, so anything may depend on it — the route modules, the guards,
 * `App.jsx`'s `onSignOut` handler, and ANV-28's header links alike. Nothing in the repo
 * types `'/login'` as a string literal a second time; a rename is one edit here.
 *
 * **Not to be confused with `features/auth/api.js`'s `LOGIN_PATH`**, which is the *API*
 * path `/v1/auth/login`. These are browser routes; that is an endpoint. The two live in
 * different modules on purpose.
 */

export const HOME_ROUTE = '/'
export const LOGIN_ROUTE = '/login'
export const SIGNUP_ROUTE = '/signup'
export const RECOVERY_ROUTE = '/recovery'
export const UNAUTHORIZED_ROUTE = '/unauthorized'
export const RESEARCH_ROUTE = '/research'
export const PORTFOLIO_ROUTE = '/portfolio'

/**
 * Where a signed-in user goes when nothing else says otherwise.
 *
 * Used by `/login`'s bounce when there is no `redirect` search param — a user who typed
 * `/login` while already signed in was not "going" anywhere, so there is nothing to
 * return them to. The old app hardcoded `/research` in `Login.jsx`; ANV-29 imports this
 * instead.
 */
export const DEFAULT_AUTHENTICATED_ROUTE = RESEARCH_ROUTE

/** The name of the search param carrying "where they were going". */
export const REDIRECT_SEARCH_PARAM = 'redirect'
