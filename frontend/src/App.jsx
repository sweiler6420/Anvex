import { RouterProvider } from '@tanstack/react-router'
import { useCallback, useEffect, useRef, useState } from 'react'

import { useAuth } from '@hooks/useAuth'
import { createAppRouter, signOutNavigation } from '@lib/router'
import { AuthProvider } from '@providers/AuthProvider'

/**
 * The application root (ANV-27): the auth store, the router, and the one seam between
 * them.
 *
 * ---------------------------------------------------------------------------------------
 * ## Why this is a component and not four lines in `main.jsx`
 *
 * ANV-26 said "ANV-27 passes `onSignOut` in `main.jsx`", and the wiring below is exactly
 * that wiring — but `main.jsx` calls `ReactDOM.createRoot(document.getElementById('root'))`
 * and therefore cannot be imported by a test without a real mount. The
 * redirect-on-sign-out behaviour is the most load-bearing thing this ticket adds, so it
 * lives in a component a test can render. `main.jsx` keeps its job — mount the tree — and
 * nothing else moved: `ThemeProvider > ErrorsProvider > App` is unchanged, and the auth
 * provider is still inside `ErrorsProvider`.
 *
 * ## Why `AuthProvider` is above `RouterProvider`
 *
 * Required, not stylistic. ANV-26 found that React runs effects **bottom-up**: a
 * `RouterProvider` mounted above the auth store would start its initial route load — and
 * with it any protected request a route makes — before the store had installed itself on
 * the transport, so the request would go out against the anonymous default token store,
 * 401, find nothing to refresh with, and end the session on first paint. `AuthProvider`
 * installs during *render* to close that window, and rendering a parent happens before
 * rendering a child. Do not invert these two.
 */
export default function App() {
  /**
   * One router for the lifetime of the app, created lazily so the module can be imported
   * without building one. `useState` rather than `useMemo`, because `useMemo` is a
   * performance hint React is permitted to discard and this is identity that must not
   * change.
   */
  const [router] = useState(() => createAppRouter())

  /**
   * The ANV-26 redirect seam. It fires **at most once per session**, so three requests
   * discovering one dead session produce one navigation rather than three racing ones.
   *
   * The destination is `signOutNavigation`'s decision (see `lib/router.js`); the only
   * thing this callback contributes is *when* the current location is read — at the moment
   * of the failure, not at render time, which is why it comes off `router.state` here
   * rather than out of a hook.
   */
  const handleSignOut = useCallback(
    ({ reason }) => {
      router.navigate(signOutNavigation({ reason, currentHref: router.state.location.href }))
    },
    [router],
  )

  return (
    <AuthProvider onSignOut={handleSignOut}>
      <RoutedApp router={router} />
    </AuthProvider>
  )
}

/**
 * Feeds the live session into the router's context and re-runs the guards when it changes.
 *
 * It has to be a separate component because `useAuth()` must be called *below*
 * `AuthProvider`, and the provider is rendered by `App` above.
 */
function RoutedApp({ router }) {
  const auth = useAuth()
  const { isAuthenticated } = auth

  /**
   * `beforeLoad` reads `context.auth`, and a route already resolved does not re-read it on
   * its own — so a session that appears *after* a match was made needs the guards re-run.
   * That is what makes the login round trip work without a line of navigation code in the
   * login form: sign in at `/login?redirect=/research`, `isAuthenticated` flips,
   * `redirectIfAuthenticated` runs again and throws the redirect.
   *
   * **Only a gained session invalidates.** A *lost* one is navigated by `onSignOut`, which
   * ANV-26 made the single authority on where a sign-out lands; invalidating as well would
   * race it — `router.navigate` is asynchronous, so `requireAuth` could still see the
   * protected match and issue a competing `/login?redirect=...` for a logout that is
   * supposed to land on a plain `/login`.
   *
   * The ref, rather than an unguarded effect, is because the effect's dependency is a
   * boolean and its action is a router-wide reload: running it on mount would discard the
   * initial load, and StrictMode's second pass would do it again.
   */
  const previouslyAuthenticated = useRef(isAuthenticated)
  useEffect(() => {
    if (previouslyAuthenticated.current === isAuthenticated) return
    previouslyAuthenticated.current = isAuthenticated
    if (isAuthenticated) router.invalidate()
  }, [router, isAuthenticated])

  // `context` is merged into the router's options during RouterProvider's render, which
  // runs before the initial load fires from a descendant effect — so the first
  // `beforeLoad` already sees the real session rather than createAppRouter's anonymous
  // seed.
  return <RouterProvider router={router} context={{ auth }} />
}
