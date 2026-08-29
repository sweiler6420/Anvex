/**
 * The sign-up → login credentials hand-off (ANV-29), and the one place its shape is
 * decided.
 *
 * `/signup` (ANV-30) finishes by sending the user to `/login` with the credentials they
 * just chose, so the login form arrives filled in and they press one button instead of
 * retyping a password they invented ten seconds ago. The old app did this through
 * react-router's `location.state`; this is the same idea with the shape written down
 * rather than implied by `location.state?.username`.
 *
 * ---------------------------------------------------------------------------------------
 * ## Router state, not storage — and the cost of that
 *
 * The hand-off travels in **TanStack Router's location state** (`navigate({ state })`),
 * which is `history.pushState` state: it belongs to one history entry, it is never
 * serialised into the URL, and nothing else on the origin can read it — unlike
 * `localStorage`, which is exactly where the old app's "remember me" put a plaintext
 * password and left it (see `authStorage.js`).
 *
 * It is still not *nothing*: a browser persists session-history state to disk so a crashed
 * tab can be restored, so a password handed off this way can outlive the tab. Two things
 * follow, and ANV-30 owns the second:
 *
 *  1. **This module never writes the hand-off anywhere.** `LoginPage` reads it once, into
 *     the initial value of a `useState`, and the checkbox's `rememberUsername` still
 *     persists the **username only**.
 *  2. **The `password` half is optional on purpose.** `signUpHandoffState({ username })`
 *     is a complete, valid hand-off, and `readSignUpHandoff` fills the missing half with
 *     `''`. If ANV-30 decides the convenience is not worth putting a password into session
 *     history, it drops one key and this page keeps working — it will prefill the
 *     identifier and focus the password field, which is the behaviour ANV-30 should prefer
 *     unless it has a reason not to.
 *
 * ## Why a module rather than an object literal at each end
 *
 * Two pages would otherwise agree on a key by writing the same string twice, and the
 * failure mode of disagreeing is *silent*: the form simply arrives empty, which looks like
 * a design decision. One builder, one reader, one key, and a test that round-trips them.
 */

/**
 * The key the hand-off sits under inside the location state.
 *
 * Namespaced rather than spreading `{username, password}` across the state object, because
 * router state is shared with the router itself (TanStack keeps its own `__TSR_*` bookkeeping
 * there) and with any future feature that wants to hand something to a page.
 */
export const SIGN_UP_HANDOFF_KEY = 'signUp'

/**
 * Build the location state ANV-30 passes to `navigate({ to: LOGIN_ROUTE, state })`.
 *
 * ```js
 * navigate({ to: LOGIN_ROUTE, replace: true, state: signUpHandoffState({ username }) })
 * ```
 *
 * @param {{username: string, password?: string}} credentials
 * @returns {{signUp: {username: string, password?: string}}}
 */
export function signUpHandoffState({ username, password } = {}) {
  return {
    [SIGN_UP_HANDOFF_KEY]: {
      username: typeof username === 'string' ? username : '',
      ...(typeof password === 'string' ? { password } : {}),
    },
  }
}

/**
 * Read a hand-off out of a location state object, or `null` if there is not one.
 *
 * Defensive about the shape rather than trusting it: location state survives a reload and a
 * Back press, so it is user-reachable data by the time it gets here, and a `state.signUp` of
 * `"nope"` must produce an empty form rather than `value={undefined}` (which would make the
 * input uncontrolled and log a React warning three keystrokes later).
 *
 * A hand-off with neither half is `null`, so a caller has one thing to test.
 *
 * @param {unknown} state the router's `location.state`
 * @returns {{username: string, password: string} | null}
 */
export function readSignUpHandoff(state) {
  const handoff = state?.[SIGN_UP_HANDOFF_KEY]
  if (handoff === null || typeof handoff !== 'object') return null

  const username = typeof handoff.username === 'string' ? handoff.username : ''
  const password = typeof handoff.password === 'string' ? handoff.password : ''
  if (username === '' && password === '') return null

  return { username, password }
}
