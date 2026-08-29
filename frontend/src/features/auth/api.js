/**
 * The auth feature's per-resource API module (ANV-26).
 *
 * CLAUDE.md §5: **`lib/api` is the transport, not an endpoint catalogue.** It owns the two
 * axios instances, the one error shape and the token seam, plus exactly one URL — the
 * refresh path, because the interceptor has to know it. Every other URL belongs to the
 * feature that uses it, which is why this file exists and why there is no
 * `lib/api/auth.js`.
 *
 * Both calls here go out on **`publicApi`**, and that is a security decision rather than a
 * convenience: neither has anything to authenticate with, and putting them on `authApi`
 * would attach a stale bearer token and make a 401 from *login* re-enter the refresh
 * interceptor.
 *
 * Failures leave here as an `ApiError` with a `code` (ANV-24), because `publicApi`'s
 * response interceptor already normalises them. So there is no `try`/`catch` below and no
 * `err.response` anywhere — a caller writes `catch (err) { setError(err) }` or branches on
 * `err.code`.
 */

import { ApiError, CLIENT_ERROR_CODES, publicApi } from '@lib/api'

/** CLAUDE.md §4 — `/v1` is the router prefix and `auth` is the resource. */
export const LOGIN_PATH = '/v1/auth/login'

/** "I have forgotten my password." Always 202, whoever asked. */
export const RECOVERY_PATH = '/v1/auth/recovery'

/**
 * Sign in with a username **or** an email address.
 *
 * `POST /v1/auth/login` is FastAPI's `OAuth2PasswordRequestForm`, so the body is
 * **form-encoded** — CLAUDE.md §4 fixes that, because modelling it as JSON would break the
 * standard OAuth2 password flow and Swagger's Authorize button with it. Two consequences
 * are easy to get wrong and are handled explicitly:
 *
 *  - **The `Content-Type` override is required.** Both axios instances are created with an
 *    instance default of `application/json`, and axios only infers a form content type for
 *    a `URLSearchParams` body when nothing has already set the header. An instance default
 *    counts as "already set", so without the override the API receives JSON on a form
 *    endpoint and answers 422.
 *  - **`URLSearchParams` percent-encodes for us**, so a password containing `&`, `+` or a
 *    non-ASCII character survives. Hand-building `` `username=${u}&password=${p}` `` does
 *    not, and it is the kind of bug that only shows up for one user.
 *
 * @param {{username: string, password: string}} credentials
 * @returns {Promise<{accessToken: string, refreshToken: string}>} the pair, camel-cased
 *   once, here — the store and the transport both speak `{accessToken, refreshToken}` and
 *   only this module and `lib/api/client.js` ever see the wire spelling.
 */
export async function login({ username, password }) {
  const form = new URLSearchParams()
  form.set('username', username)
  form.set('password', password)

  const response = await publicApi.post(LOGIN_PATH, form, {
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  })

  return readTokenPair(response)
}

/**
 * Ask for a password reset.
 *
 * The API answers **202 with a fixed body for every username**, whether or not the account
 * exists (CLAUDE.md §4) — the old `/v1/recovery` answered `404 "User not found with
 * username: <x>"`, which made password recovery a free account-enumeration API. So there is
 * nothing here to branch on and nothing to report differently: the caller shows the message
 * it got and never infers anything from it.
 *
 * @param {{username: string}} request
 * @returns {Promise<{status: string, message: string}>}
 */
export async function requestRecovery({ username }) {
  const response = await publicApi.post(RECOVERY_PATH, { username })
  return response?.data
}

/**
 * Read a `TokenPair` off a 2xx, or fail the way every other failure fails.
 *
 * A 200 whose body is not a pair is `malformed_response` — the same code `client.js` raises
 * for the same situation on the refresh path, so a caller has one thing to handle. Returning
 * `{accessToken: undefined}` instead would put `Bearer undefined` on every later request and
 * surface three screens away.
 */
function readTokenPair(response) {
  const data = response?.data
  if (typeof data?.access_token !== 'string' || typeof data?.refresh_token !== 'string') {
    throw new ApiError({
      code: CLIENT_ERROR_CODES.MALFORMED,
      message: 'The sign-in response did not contain a token pair.',
      status: typeof response?.status === 'number' ? response.status : null,
    })
  }
  return { accessToken: data.access_token, refreshToken: data.refresh_token }
}
