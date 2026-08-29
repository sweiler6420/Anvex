import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { login as loginRequest } from '@features/auth/api'
import { readRefreshToken, writeRefreshToken } from '@features/auth/authStorage'
import { installTokenStore } from '@lib/api'

import { AuthContext } from './AuthContext'

/**
 * The auth store (ANV-26). Replaces `AuthProvider` + `PersistLogin` + `useRefreshToken`.
 *
 * Three operations — `login`, `logout`, `restore` — and one rendered fact,
 * `isAuthenticated`. The tokens themselves are **not** state: they live in refs, and the
 * transport reads them through `installTokenStore` (ANV-24) at request time.
 *
 * ---------------------------------------------------------------------------------------
 * ## Why the tokens are refs and not state
 *
 * Nothing renders a token, so putting one in `useState` would re-render the whole app on
 * every silent refresh for a value no component displays. Worse, it would be *wrong*: the
 * four callbacks handed to `installTokenStore` are installed once, so a `useState` value
 * captured in them is a snapshot, and `getAccessToken()` would keep returning the token the
 * session started with. A ref is read at call time, which is exactly the contract
 * `getTokenStore()`-per-request was designed for. `isAuthenticated` is the one thing a
 * component branches on, so it — and only it — is state.
 *
 * ## Why `restore` is synchronous, and does not refresh
 *
 * The old `PersistLogin` awaited a refresh on **every** page load and rendered a
 * full-screen `"Loading..."` div over the entire route tree until it settled — including
 * for anonymous visitors on public pages, who waited for a request that was always going to
 * fail. That is not reproduced here, and not by making the wait prettier: **there is no
 * wait.** `restore()` reads `localStorage`, and a stored refresh token means "provisionally
 * signed in". It returns a boolean and it returns it immediately, so the first render
 * already knows the answer and no route ever has to be gated on a boot promise.
 *
 * An explicit boot-time refresh would be strictly worse in four ways:
 *
 *  1. **The transport already does it.** ANV-24 refreshes on any 401 except `invalid_token`
 *     / `wrong_token_type` — framed that way precisely because a reload holds a refresh
 *     token and no access token, so the first protected call is a 401 `unauthorized` and
 *     the interceptor refreshes and replays it. A boot refresh would be a second
 *     implementation of a path that is already single-flight and already tested.
 *  2. **It costs a round trip on every load, including loads that never need a token** —
 *     the home page, the login page, a marketing route.
 *  3. **It spends a rotation to learn nothing.** `/v1/auth/refresh` invalidates the token
 *     presented, so a boot refresh burns the stored credential on a page view.
 *  4. **It fails at the wrong moment.** A dead refresh token would sign a user out while
 *     they are reading a public page, instead of when they actually ask for something —
 *     where ANV-27 can send them to `/login` with a `redirect` that means something.
 *
 * The honest cost, stated rather than discovered: `isAuthenticated` is **provisional**. A
 * refresh token the server has already invalidated will let a guard through, and the first
 * protected request will then 401, fail its refresh, and end the session via `clear()`. The
 * user sees the protected route briefly before being redirected. That is the same trade
 * every persist-login makes — the alternative is the blocking spinner — and it is the
 * reason `onSignOut` exists.
 *
 * ## The redirect seam
 *
 * `lib/api` deliberately never navigates, which is what makes the refresh path testable
 * outside React. So the navigation lands here, as the **`onSignOut` prop**: a callback
 * invoked *after* the store has gone anonymous, on both kinds of session end. ANV-27 wires
 * it to the router at the root, where the router instance exists; the store stays a plain
 * React component that a test can mount without one. It fires at most once per session, so
 * two concurrent requests both discovering a dead session produce one redirect.
 */

/** The user asked to sign out. ANV-27: plain `/login`, no `redirect` param. */
export const SIGN_OUT_LOGOUT = 'logout'

/** The API refused the session. ANV-27: `/login?redirect=<where they were>`. */
export const SIGN_OUT_SESSION_EXPIRED = 'session_expired'

/**
 * The uninstaller for the auth store currently installed on the transport, or `null`.
 *
 * Module-level rather than a ref, and that is forced by React: **StrictMode re-invokes a
 * component's render with a fresh set of hooks**, so a `useRef` guard cannot make a
 * render-phase install happen only once — the second pass gets a new ref whose `current` is
 * `null` and installs a second store, leaving one install unbalanced and `getTokenStore()`
 * pointing at a discarded render's closures after unmount.
 *
 * There is exactly one auth store in an application, so the fix is to **replace rather than
 * stack**: installing uninstalls whatever this module put in first. The returned cleanup
 * checks it still owns the slot before firing, which is the same guard `client.js` uses on
 * its in-flight refresh and for the same reason.
 */
let uninstallCurrentStore = null

function installProviderStore(store) {
  uninstallCurrentStore?.()
  const uninstall = installTokenStore(store)
  uninstallCurrentStore = uninstall
  return () => {
    if (uninstallCurrentStore !== uninstall) return
    uninstallCurrentStore = null
    uninstall()
  }
}

export function AuthProvider({ children, onSignOut }) {
  /** The access token. **In memory only** — nothing here ever writes it to storage. */
  const accessTokenRef = useRef(null)

  /**
   * The refresh token, mirrored from `localStorage`.
   *
   * `undefined` means "not read yet"; `null` is a real value meaning "signed out", which is
   * why the sentinel cannot be `null`. Read lazily during the first render rather than at
   * module scope (ANV-25's rule) — storage access belongs inside the component, where a
   * refusal is a React failure and not an import-time crash.
   */
  const refreshTokenRef = useRef(undefined)
  if (refreshTokenRef.current === undefined) refreshTokenRef.current = readRefreshToken()

  const [isAuthenticated, setIsAuthenticated] = useState(() => refreshTokenRef.current !== null)

  // Kept in a ref so the `clear` callback installed once below always calls the *current*
  // prop rather than the one that existed when the provider mounted.
  const onSignOutRef = useRef(onSignOut)
  useEffect(() => {
    onSignOutRef.current = onSignOut
  }, [onSignOut])

  /**
   * Adopt a token pair — from a login, or from a refresh the transport performed.
   *
   * **Both halves, always.** `setTokens` receives the whole rotated pair because
   * `/v1/auth/refresh` invalidates the token it was given; persisting only the access token
   * would leave the *next* refresh presenting a spent credential, which the API answers with
   * a 401 and which ends the session. That is the single most important line in this file,
   * and the rotation test is the one that fails without it.
   */
  const adopt = useCallback((pair) => {
    accessTokenRef.current = pair.accessToken
    refreshTokenRef.current = pair.refreshToken
    writeRefreshToken(pair.refreshToken)
    setIsAuthenticated(true)
  }, [])

  /**
   * End the session and tell whoever owns navigation.
   *
   * Guarded on "was there a session at all", so the second of two concurrent failures is a
   * no-op rather than a second redirect. The remembered username is deliberately **not**
   * cleared: it is a form convenience owned by ANV-29's checkbox, and signing out is not a
   * request to forget who you are.
   */
  const endSession = useCallback((reason) => {
    const hadSession = accessTokenRef.current !== null || refreshTokenRef.current !== null
    accessTokenRef.current = null
    refreshTokenRef.current = null
    writeRefreshToken(null)
    setIsAuthenticated(false)
    if (hadSession) onSignOutRef.current?.({ reason })
  }, [])

  /**
   * Re-read persisted state. Called once during the first render (via the ref initialiser
   * above) and available afterwards for anything that needs to reconcile — a test, or a
   * future cross-tab listener.
   *
   * Synchronous, and it makes **no network call**; see the header. It never fires
   * `onSignOut`, because finding no stored token on boot is not a session ending.
   *
   * @returns {boolean} whether a refresh token was found.
   */
  const restore = useCallback(() => {
    const stored = readRefreshToken()
    refreshTokenRef.current = stored
    if (stored === null) accessTokenRef.current = null
    setIsAuthenticated(stored !== null)
    return stored !== null
  }, [])

  /**
   * Sign in. Rejects with an `ApiError` — it does **not** route the failure through
   * `useErrors`, because a bad password belongs beside the password field (ANV-29 renders
   * it with "Try Again"), not in a global banner that auto-clears after ten seconds.
   */
  const login = useCallback(
    async ({ username, password }) => {
      adopt(await loginRequest({ username, password }))
    },
    [adopt],
  )

  const logout = useCallback(() => endSession(SIGN_OUT_LOGOUT), [endSession])

  // ------------------------------------------------------------------ the ANV-24 seam

  /**
   * The four callbacks ANV-24's transport reads tokens through, and the uninstaller for
   * them.
   *
   * **Installed during render, not only in the effect**, and that is deliberate. React runs
   * effects bottom-up, so every descendant's mount effect fires *before* this provider's.
   * An effect-only install therefore leaves a window in which a child — or ANV-27's
   * `RouterProvider`, which starts the initial route load from an effect of its own —
   * issues a protected request against the anonymous default store: no `Authorization`
   * header, a 401, no refresh token to exchange, and a spurious sign-out on the first paint
   * after a reload. Installing while rendering closes that window, because a parent renders
   * before its children do; `installProviderStore` above is what makes doing so safe when
   * the render happens more than once.
   *
   * The effect still owns the **uninstall**, and re-installs after StrictMode's simulated
   * unmount — which runs the cleanup without re-rendering, so nothing else would.
   */
  const storeRef = useRef(null)
  const uninstallRef = useRef(null)

  if (storeRef.current === null) {
    storeRef.current = {
      getAccessToken: () => accessTokenRef.current,
      getRefreshToken: () => refreshTokenRef.current ?? null,
      setTokens: adopt,
      clear: () => endSession(SIGN_OUT_SESSION_EXPIRED),
    }
    uninstallRef.current = installProviderStore(storeRef.current)
  }

  useEffect(() => {
    if (uninstallRef.current === null) {
      uninstallRef.current = installProviderStore(storeRef.current)
    }
    return () => {
      uninstallRef.current?.()
      uninstallRef.current = null
    }
  }, [])

  const value = useMemo(
    () => ({ isAuthenticated, login, logout, restore }),
    [isAuthenticated, login, logout, restore],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export default AuthProvider
