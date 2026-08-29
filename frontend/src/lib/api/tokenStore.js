/**
 * The token seam between the transport (ANV-24) and the auth store (ANV-26).
 *
 * **ANV-24 owns how tokens travel; ANV-26 owns where they live.** The interceptor in
 * `client.js` must not contain the words `localStorage` or `useState`: the moment it does,
 * the refresh path can only be tested by mutating browser storage, the React store and the
 * transport hold two copies of the same token that drift, and a change of storage policy
 * (session-only, cookie, native keychain when this is wrapped for iOS) is a change to the
 * transport. The old app had exactly that coupling — `useRefreshToken.js` read
 * `localStorage.getItem('refresh_token')` *and* called `setAuth` *and* called `navigate`,
 * three concerns in one function, which is why it could only be used from inside a
 * component.
 *
 * So the transport reads through a swappable object with four methods, and ships a no-op
 * default. **`client.js` calls `getTokenStore()` at request time, never at import time**,
 * so installing a store after the module graph has loaded (which is what a React provider
 * does) works.
 *
 * @typedef {object} TokenStore
 * @property {() => string|null} getAccessToken
 *   The bearer token to attach, or `null` for "not signed in" — in which case no
 *   `Authorization` header is sent at all, rather than `Bearer null`.
 * @property {() => string|null} getRefreshToken
 *   The token to exchange at `/v1/auth/refresh`, or `null` — in which case a 401 is
 *   terminal and no refresh is attempted.
 * @property {(pair: {accessToken: string, refreshToken: string}) => void} setTokens
 *   Called with the **rotated pair** after a successful refresh. `/v1/auth/refresh`
 *   returns a new refresh token as well as a new access token and invalidates the old one,
 *   so a store that persists only the access token logs the user out on the next refresh.
 * @property {() => void} clear
 *   Called when the session is over: the refresh was refused, or a 401 arrived with a code
 *   refreshing cannot fix. This is ANV-26's "log out" signal — redirecting is its job, not
 *   the transport's.
 */

/** What the transport does when nothing has been installed: behave as a signed-out client. */
const ANONYMOUS_TOKEN_STORE = Object.freeze({
  getAccessToken: () => null,
  getRefreshToken: () => null,
  setTokens: () => {},
  clear: () => {},
})

const REQUIRED_METHODS = ['getAccessToken', 'getRefreshToken', 'setTokens', 'clear']

let installed = ANONYMOUS_TOKEN_STORE

/** The store in force right now. Call this per request; never cache the result. */
export function getTokenStore() {
  return installed
}

/**
 * Install the auth store's implementation. Returns an uninstall function.
 *
 * The shape is checked **here**, at install time, rather than being discovered halfway
 * through a 401 when `store.setTokens` turns out to be undefined and the refresh that
 * would have saved the session throws instead.
 *
 * @param {TokenStore} store
 * @returns {() => void} restores whatever was installed before
 */
export function installTokenStore(store) {
  for (const method of REQUIRED_METHODS) {
    if (typeof store?.[method] !== 'function') {
      throw new TypeError(
        `installTokenStore: a token store must implement ${REQUIRED_METHODS.join(', ')} — ` +
          `'${method}' is ${typeof store?.[method]}.`,
      )
    }
  }
  const previous = installed
  installed = store
  return () => {
    if (installed === store) installed = previous
  }
}

/** Back to signed-out. Used by the test harness and by a full logout at the root. */
export function resetTokenStore() {
  installed = ANONYMOUS_TOKEN_STORE
}

export { ANONYMOUS_TOKEN_STORE }
