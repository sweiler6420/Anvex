/**
 * The two axios instances every network call in Anvex goes through (ANV-24).
 *
 * `publicApi`  — no credentials, no refresh. Login, refresh, recovery, sign-up.
 * `authApi`    — attaches the bearer token, and refreshes it once on a 401.
 *
 * Errors leave both instances as an {@link ApiError} (see `errors.js`), so a feature's
 * `api.js` never sees an `AxiosError` and never inspects `err.response`.
 *
 * ---------------------------------------------------------------------------------------
 * Three bugs in `AverageInvestorWeb/src/hooks/useAxiosPrivate.js`, all confirmed by reading
 * it, and all deliberately not reproduced here.
 *
 * **1. It refreshed on 403.** `if (error?.response?.status === 403 && !prevRequest?.sent)`.
 *    Anvex returns **401** for a token that is missing, expired, malformed or of the wrong
 *    type, and **403** (`ForbiddenError`) for "authenticated, but not allowed". The old
 *    interceptor therefore never fired on the real signal and *did* fire on a genuine
 *    permission denial — burning a refresh-token rotation to retry a request that was
 *    always going to be refused. Here: 401 refreshes, 403 is passed straight through.
 *
 * **2. No single-flight guard.** `prevRequest.sent` is a flag on *one request's* config, so
 *    five concurrent calls that all 401 set five separate flags and fire five refreshes.
 *    `/v1/auth/refresh` **rotates** — it returns a new pair and invalidates the presented
 *    token — so refresh #2 arrives holding a token #1 has already spent, is refused, and
 *    the old `useRefreshToken` responded to that by clearing storage and navigating to
 *    `/login`. A dashboard that fires three requests on mount would log the user out on the
 *    first expiry. That is a real bug against this API's semantics, not a theoretical one.
 *    Here: one shared promise, and everything that 401s while it is in flight awaits it.
 *
 * **3. `useApi.js` swallowed errors.** Five differently-shaped functions, each catching
 *    everything and returning `{status, message, error, detail}` — no `data`, no throw — so
 *    a caller could not distinguish success from failure without probing fields that might
 *    not exist. Here every failure is a rejection carrying an `ApiError` with a `code`.
 *
 * A fourth, uncommented: the old refresh posted **`FormData` to `v1/refresh`**, an endpoint
 * that took the token as a query parameter. Anvex's is `POST /v1/auth/refresh` with a JSON
 * body `{"refresh_token": "..."}`.
 */

import axios from 'axios'

import { API_BASE_URL } from '../env'
import { ApiError, AUTH_ERROR_CODES, CLIENT_ERROR_CODES, toApiError } from './errors'
import { getTokenStore } from './tokenStore'

/** CLAUDE.md §4 — the router prefix is `/v1`, and the resource is `auth`. */
export const REFRESH_PATH = '/v1/auth/refresh'

/**
 * A ceiling, not a target. axios defaults to *no* timeout, which turns a hung connection
 * into a spinner that never resolves and a promise that is never settled.
 */
export const DEFAULT_TIMEOUT_MS = 20_000

/** Marks a config that has already been replayed once. See `onAuthResponseError`. */
const RETRIED = '_anvexRefreshRetried'

/**
 * 401 codes that refreshing cannot fix, so the session ends instead.
 *
 * The inverse framing is deliberate: **refresh on any 401 except these**, rather than
 * refresh only on `token_expired`. A page reload has a refresh token but no access token
 * yet, so its first protected call is a 401 `unauthorized` — and that is precisely the
 * case a refresh is meant to rescue. `invalid_token` and `wrong_token_type`, by contrast,
 * describe a token that is wrong rather than old; presenting its sibling would only be
 * refused again.
 */
const NON_REFRESHABLE_CODES = new Set([
  AUTH_ERROR_CODES.INVALID_TOKEN,
  AUTH_ERROR_CODES.WRONG_TOKEN_TYPE,
])

const baseConfig = {
  // `API_BASE_URL` is `''` when the app is served same-origin behind the dev proxy, and
  // that is a meaningful value, not a missing one (CLAUDE.md §5). axios treats an empty
  // baseURL as "use the path as given", which is exactly right.
  baseURL: API_BASE_URL,
  timeout: DEFAULT_TIMEOUT_MS,
  headers: { 'Content-Type': 'application/json' },
}

/** Unauthenticated transport. Also what the refresh call itself uses — see below. */
export const publicApi = axios.create(baseConfig)

/** Authenticated transport. Everything behind a login goes through this one. */
export const authApi = axios.create(baseConfig)

// --------------------------------------------------------------------------- headers

/**
 * axios 1.x hands interceptors an `AxiosHeaders` instance, but a config object built by
 * hand (or one round-tripped through a test) may carry a plain object. Both are supported
 * rather than assumed, because getting it wrong shows up only on the retry path.
 */
function setHeader(config, name, value) {
  if (typeof config.headers?.set === 'function') {
    config.headers.set(name, value)
    return
  }
  config.headers = { ...(config.headers ?? {}), [name]: value }
}

function getHeader(config, name) {
  if (typeof config.headers?.get === 'function') return config.headers.get(name)
  return config.headers?.[name]
}

// ------------------------------------------------------------------- error normalising

const rejectAsApiError = (error) => Promise.reject(toApiError(error))

publicApi.interceptors.response.use((response) => response, rejectAsApiError)

// ------------------------------------------------------------------------ request auth

authApi.interceptors.request.use((config) => {
  // Read through the seam **per request**. Capturing the token at import time is how the
  // old hook ended up rebuilding its interceptors on every `auth` change.
  const token = getTokenStore().getAccessToken()
  if (token && !getHeader(config, 'Authorization')) {
    setHeader(config, 'Authorization', `Bearer ${token}`)
  }
  return config
}, rejectAsApiError)

// ------------------------------------------------------------------ single-flight refresh

/**
 * The one in-flight refresh, or `null`.
 *
 * **This promise *is* the queue.** Every request that 401s while a refresh is running
 * awaits the same promise and then replays itself with the token it resolves to, so there
 * is no separate subscriber array to keep in step with it — and, critically, no window
 * between "a refresh is running" and "I have been added to its list" in which a second
 * refresh could start. The assignment below is synchronous with the check above it, and JS
 * is single-threaded, so the window does not exist.
 */
let refreshInFlight = null

/**
 * Ask for a fresh access token, joining the in-flight refresh if there is one.
 *
 * @returns {Promise<string>} the new access token
 */
function refreshAccessToken() {
  if (refreshInFlight) return refreshInFlight

  const attempt = performRefresh().finally(() => {
    // Guarded rather than unconditional: a later refresh may already own the slot by the
    // time this one's `finally` runs, and clearing it would let a third refresh start
    // alongside the second.
    if (refreshInFlight === attempt) refreshInFlight = null
  })
  refreshInFlight = attempt
  return attempt
}

/**
 * Exchange the stored refresh token for a new pair.
 *
 * Goes out on **`publicApi`**, which is load-bearing in two ways: the refresh request must
 * not carry the expired bearer token, and — more importantly — a 401 from the refresh
 * endpoint must not re-enter the interceptor that is currently awaiting it. On `authApi`
 * that is a refresh that triggers a refresh.
 */
async function performRefresh() {
  const store = getTokenStore()
  const refreshToken = store.getRefreshToken()

  if (!refreshToken) {
    // Nothing to exchange. Signal the end of the session without a pointless round trip
    // that the API would answer with a 422 for a missing body field.
    store.clear()
    throw new ApiError({
      code: AUTH_ERROR_CODES.UNAUTHORIZED,
      message: 'Your session has ended. Please sign in again.',
    })
  }

  let pair
  try {
    // A JSON body. The old client sent `FormData` to a query-parameter endpoint.
    const response = await publicApi.post(REFRESH_PATH, { refresh_token: refreshToken })
    pair = response?.data
  } catch (error) {
    const apiError = toApiError(error)
    // **Only a refusal ends the session.** A 4xx means the API looked at the token and
    // said no. A network failure or a 5xx means we never got an answer — clearing there
    // would sign a user out because their wifi blipped or because the API was restarting,
    // and the tokens they hold may still be perfectly good. Reject, keep the tokens, let
    // the next request try again.
    if (apiError.status !== null && apiError.status >= 400 && apiError.status < 500) {
      store.clear()
    }
    throw apiError
  }

  if (typeof pair?.access_token !== 'string' || typeof pair?.refresh_token !== 'string') {
    store.clear()
    throw new ApiError({
      code: CLIENT_ERROR_CODES.MALFORMED,
      message: 'The refresh response did not contain a token pair.',
      status: 200,
    })
  }

  // Both halves, always. `/v1/auth/refresh` rotates the refresh token and invalidates the
  // one presented, so storing only `access_token` breaks the *next* refresh.
  store.setTokens({ accessToken: pair.access_token, refreshToken: pair.refresh_token })
  return pair.access_token
}

// ---------------------------------------------------------------- response / retry path

async function onAuthResponseError(error) {
  const apiError = toApiError(error)
  const original = error?.config

  // 403 lands here. It is `ForbiddenError` — authenticated, not permitted — and no token
  // in the world fixes it. The old app refreshed on exactly this.
  if (apiError.status !== 401 || !original) throw apiError

  // A replay that 401s again is the end of it. One retry per request, full stop; without
  // this, a server that answers 401 to a freshly-minted token loops forever.
  if (original[RETRIED]) {
    getTokenStore().clear()
    throw apiError
  }

  if (NON_REFRESHABLE_CODES.has(apiError.code)) {
    getTokenStore().clear()
    throw apiError
  }

  original[RETRIED] = true

  // Rejects with an `ApiError`; `performRefresh` has already decided whether the session
  // is over, so there is nothing to add here. Letting it propagate means the caller learns
  // *why* the session ended rather than seeing the original 401 again.
  const accessToken = await refreshAccessToken()

  setHeader(original, 'Authorization', `Bearer ${accessToken}`)
  return authApi.request(original)
}

authApi.interceptors.response.use((response) => response, onAuthResponseError)

/**
 * Drop any in-flight refresh. **Test harness only** — module state outlives a single test,
 * and a rejected promise left in the slot would be joined by the next test's 401.
 */
export function resetRefreshState() {
  refreshInFlight = null
}
