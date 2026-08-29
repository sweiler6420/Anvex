/**
 * `lib/api` — the transport layer (ANV-24). CLAUDE.md §5: **all network calls go through
 * here**, and no component ever imports `axios` directly.
 *
 * What this package is:
 *
 *   - `publicApi` / `authApi` — the two axios instances, plus the interceptors that attach
 *     the bearer token and perform a single-flight refresh on a 401.
 *   - `ApiError` — the one failure shape, with a `code` to branch on and a `status` that is
 *     `null` when nothing answered.
 *   - `installTokenStore` — the seam the auth store (ANV-26) plugs into.
 *
 * What this package is **not**: an endpoint catalogue. A per-resource module lives in its
 * feature (`features/watchlist/api.js`), imports `authApi` from here, and owns nothing but
 * the URL, the params, and the projection of the response. There is deliberately no
 * `lib/api/watchlists.js` — that would put every feature's URLs in one shared module and
 * undo the feature-first rule the moment a second feature needed one.
 */

export {
  ApiError,
  AUTH_ERROR_CODES,
  CLIENT_ERROR_CODES,
  isApiError,
  isAuthError,
  isNetworkError,
  toApiError,
} from './errors'

export {
  authApi,
  DEFAULT_TIMEOUT_MS,
  publicApi,
  REFRESH_PATH,
  resetRefreshState,
} from './client'

export {
  ANONYMOUS_TOKEN_STORE,
  getTokenStore,
  installTokenStore,
  resetTokenStore,
} from './tokenStore'
