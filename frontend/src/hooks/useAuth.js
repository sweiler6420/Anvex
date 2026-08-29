import { useContext } from 'react'

import { AuthContext } from '@providers/AuthContext'

/**
 * Read and drive the session (ANV-26).
 *
 * ```js
 * const { isAuthenticated, login, logout } = useAuth()
 * ```
 *
 * - **`isAuthenticated`** — is there a session? Boolean, and correct on the *first* render:
 *   `restore` reads `localStorage` synchronously, so there is no boot-time pending state to
 *   wait for and nothing needs to be gated behind a spinner (which is what the old
 *   `PersistLogin` did to every route, public ones included). It is **provisional** until
 *   the first protected call succeeds — a stored refresh token the server has already
 *   invalidated reads as `true` here — and `AuthProvider`'s `onSignOut` prop is how that is
 *   noticed.
 * - **`login({username, password})`** — async. Resolves when the pair is stored, and
 *   **rejects with an `ApiError`** otherwise, so a form can render `err.message` beside the
 *   field and branch on `err.code`. It does not raise through `useErrors`: a bad password is
 *   not a global banner.
 * - **`logout()`** — synchronous. Clears both tokens and fires `onSignOut`.
 * - **`restore()`** — re-read persisted state; returns whether a refresh token was found.
 *   The provider already calls it on mount, so a component rarely needs to.
 *
 * **There is no `accessToken` here, on purpose.** The transport reads it through the token
 * seam (ANV-24); a component that wants an authenticated call imports its feature's
 * `api.js`. The old `useAuth` returned `{auth, setAuth}` and let any component overwrite the
 * whole session object, which is how the token ended up copied into three places.
 *
 * Throws outside the provider rather than returning the old hook's `{}` default — a
 * component that silently believes nobody is signed in is much harder to diagnose than one
 * that names the missing provider.
 *
 * @returns {{isAuthenticated: boolean,
 *            login: (credentials: {username: string, password: string}) => Promise<void>,
 *            logout: () => void,
 *            restore: () => boolean}}
 */
export function useAuth() {
  const context = useContext(AuthContext)
  if (context === null) {
    throw new Error('useAuth must be used inside <AuthProvider>.')
  }
  return context
}

export default useAuth
