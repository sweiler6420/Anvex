/**
 * The **only** module in Anvex that writes an auth value to browser storage (ANV-26).
 *
 * CLAUDE.md §5 fixes the policy: **access token in memory, refresh token in
 * `localStorage`.** That split is the whole security posture of the client, so it is worth
 * being able to audit it by reading one short file rather than by grepping a provider:
 *
 *   - there is no `writeAccessToken` here, and there is nowhere else that could define one;
 *   - the two keys below are the complete list of what Anvex persists for auth;
 *   - a test can assert the *contents* of `localStorage` against that list, which is a much
 *     stronger claim than "the API we exposed does not offer to store it".
 *
 * ---------------------------------------------------------------------------------------
 * **The security bug this replaces.** `AverageInvestorWeb/src/components/authenticate/
 * Login.jsx` implemented "remember me" as
 *
 *     localStorage.setItem("username", JSON.stringify(loginForm.username))
 *     localStorage.setItem("pass", JSON.stringify(loginForm.password))
 *
 * — the user's **plaintext password**, kept forever, readable by any script on the origin
 * and by anyone who opens devtools on a shared machine, and re-read into a password field
 * on every visit. A remembered *username* is a convenience; a remembered password is a
 * credential store with no encryption and no expiry. Only the username survives the port,
 * and `rememberUsername` is deliberately the only "remember" primitive that exists.
 *
 * ---------------------------------------------------------------------------------------
 * **Storage can refuse, and every access is guarded individually** — ANV-25's rule, applied
 * to the other storage consumer. A browser with site data blocked can throw on reading the
 * `window.localStorage` *property*, on `getItem`, and on `setItem`/`removeItem`
 * independently, so all four are wrapped. The degradation is the same in every case: **auth
 * still works for this tab and simply does not survive a reload.** It is never an error
 * worth surfacing — a user in a private window has asked for exactly this.
 */

/** Where the refresh token lives. Namespaced so nothing collides with `theme` (ANV-25). */
export const REFRESH_TOKEN_KEY = 'anvex.refresh_token'

/** Where "remember me" keeps the username — and, deliberately, nothing else. */
export const REMEMBERED_USERNAME_KEY = 'anvex.remembered_username'

/** Every key this module is allowed to write. Exported so a test can assert on the set. */
export const AUTH_STORAGE_KEYS = Object.freeze([REFRESH_TOKEN_KEY, REMEMBERED_USERNAME_KEY])

/**
 * `window.localStorage`, or `null` if there is no window or the property itself throws.
 *
 * Reading the property is its own failure mode, separate from reading a value out of it —
 * which is why this is a function and not a module-level constant.
 */
function storage() {
  if (typeof window === 'undefined') return null
  try {
    return window.localStorage ?? null
  } catch {
    return null
  }
}

/**
 * A stored string, or `null`.
 *
 * An **empty string reads as absent**. The old app wrote `localStorage.setItem('username',
 * "")` to mean "forget this", then tested `localStorage.username !== ""` before a
 * `JSON.parse` — three different spellings of the same idea, one of which threw. Here
 * "absent" has exactly one representation on the way out, whatever is on the way in.
 */
function read(key) {
  const store = storage()
  if (store === null) return null
  try {
    const value = store.getItem(key)
    return typeof value === 'string' && value.length > 0 ? value : null
  } catch {
    return null
  }
}

/** Persist, or don't. `null` removes the key rather than storing the string `"null"`. */
function write(key, value) {
  const store = storage()
  if (store === null) return
  try {
    if (value === null || value === undefined || value === '') store.removeItem(key)
    else store.setItem(key, String(value))
  } catch {
    // Site data blocked, private mode, quota exceeded. The in-memory session is already
    // correct; only the *next* page load loses it.
  }
}

/** The refresh token from a previous session, or `null`. This is what makes login persist. */
export function readRefreshToken() {
  return read(REFRESH_TOKEN_KEY)
}

/** Store the refresh token; `null` forgets it. The rotated one, never the old one. */
export function writeRefreshToken(token) {
  write(REFRESH_TOKEN_KEY, token)
}

/**
 * The username a previous "remember me" saved, or `null`.
 *
 * Owned here rather than by the login page because this file is the audit surface for
 * "what does Anvex put in storage", and the answer has to be complete. **The page (ANV-29)
 * owns the checkbox** and calls these two functions; the auth store never does, because a
 * form affordance is not part of the session.
 */
export function readRememberedUsername() {
  return read(REMEMBERED_USERNAME_KEY)
}

/** Remember a username for the next visit; `null` forgets it. Never a password. */
export function rememberUsername(username) {
  write(REMEMBERED_USERNAME_KEY, username)
}
