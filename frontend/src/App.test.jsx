import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import App from './App'
import { login as loginRequest, LOGIN_PATH } from '@features/auth/api'
import { REFRESH_TOKEN_KEY } from '@features/auth/authStorage'
import { getTokenStore, resetTokenStore } from '@lib/api'
import { apiUrl } from '@lib/env'
import { ThemeProvider } from '@providers/ThemeProvider'
import { server } from '@test/msw/server'

/**
 * The application root (ANV-27): `AuthProvider` above `RouterProvider`, the real token
 * seam, and the `onSignOut` redirect between them.
 *
 * `routes.test.jsx` drives the guards with a hand-made auth context; this file uses the
 * **real** one — `localStorage`, `AuthProvider`, the ANV-24 token store — so the parts that
 * only exist in the wiring are covered: that a stored refresh token is known early enough
 * for the first `beforeLoad`, that gaining a session re-runs the guards, and that losing
 * one navigates.
 *
 * `App` builds its own router over the browser history, so a test chooses its starting URL
 * with `history.replaceState` exactly as a reload would.
 */
function renderAppAt(path) {
  window.history.replaceState(null, '', path)
  // `ThemeProvider` mirrors `main.jsx`, which mounts it above `App`. It became load-bearing
  // in ANV-28: the root route renders `Layout` → `Header` → `DarkModeSwitcher`, and
  // `useDarkMode()` throws outside its provider rather than half-working (ANV-25).
  return render(
    <ThemeProvider>
      <App />
    </ThemeProvider>,
  )
}

const location = () => `${window.location.pathname}${window.location.search}`

beforeEach(() => {
  window.localStorage.clear()
  window.history.replaceState(null, '', '/')
})

afterEach(() => {
  // The provider uninstalls on unmount; this is belt and braces so one test's store can
  // never answer another test's request.
  resetTokenStore()
  window.localStorage.clear()
})

describe('boot', () => {
  it('sends an anonymous reload of a protected URL to /login with the redirect param', async () => {
    renderAppAt('/research')

    expect(await screen.findByTestId('route-login')).toBeInTheDocument()
    expect(location()).toBe('/login?redirect=%2Fresearch')
  })

  it('admits a stored refresh token on the very first render — there is no boot window', async () => {
    // The whole of persist-login. `restore()` is a synchronous `localStorage` read in a ref
    // initialiser during AuthProvider's first render, so the router's first `beforeLoad`
    // already sees `isAuthenticated: true`. Nothing awaits, so there is nothing for a
    // pending component to cover — and no flash of /login on the way to /research.
    window.localStorage.setItem(REFRESH_TOKEN_KEY, 'stored-refresh-token')

    renderAppAt('/research')

    expect(await screen.findByTestId('route-research')).toBeInTheDocument()
    expect(location()).toBe('/research')
    // Not "it ended up here" — it was never anywhere else.
    expect(screen.queryByTestId('route-login')).not.toBeInTheDocument()
  })

  it('leaves a public route alone either way', async () => {
    renderAppAt('/signup')

    expect(await screen.findByTestId('route-sign-up')).toBeInTheDocument()
    expect(location()).toBe('/signup')
  })
})

describe('signing in', () => {
  /**
   * A real `POST /v1/auth/login` through MSW, then the pair handed to the token store —
   * which is exactly what `AuthProvider.login` does with it. There is no login form until
   * ANV-29, so this is the last step of the real path rather than a stand-in for it.
   */
  async function signIn() {
    server.use(
      http.post(apiUrl(LOGIN_PATH), () =>
        HttpResponse.json({
          access_token: 'access-1',
          refresh_token: 'refresh-1',
          token_type: 'bearer',
        }),
      ),
    )

    const pair = await loginRequest({ username: 'ada', password: 'correct-horse-battery' })
    await act(async () => {
      getTokenStore().setTokens(pair)
    })
  }

  it('lands them where they were going, not on a default', async () => {
    renderAppAt('/login?redirect=%2Fportfolio')
    await screen.findByTestId('route-login')

    await signIn()

    expect(await screen.findByTestId('route-portfolio')).toBeInTheDocument()
    expect(location()).toBe('/portfolio')
  })

  it('lands them on the default when they simply visited /login', async () => {
    renderAppAt('/login')
    await screen.findByTestId('route-login')

    await signIn()

    expect(await screen.findByTestId('route-research')).toBeInTheDocument()
    expect(location()).toBe('/research')
  })

  it('completes the round trip a guard started', async () => {
    // /research refused -> /login?redirect=/research -> sign in -> /research. The param the
    // guard wrote is the one the bounce reads; nothing in between hardcodes a destination.
    renderAppAt('/research')
    await screen.findByTestId('route-login')
    expect(location()).toBe('/login?redirect=%2Fresearch')

    await signIn()

    expect(await screen.findByTestId('route-research')).toBeInTheDocument()
    expect(location()).toBe('/research')
  })
})

describe('onSignOut', () => {
  it('sends an expired session to /login carrying the path it died on', async () => {
    window.localStorage.setItem(REFRESH_TOKEN_KEY, 'stored-refresh-token')
    renderAppAt('/research?tab=news')
    await screen.findByTestId('route-research')

    // `clear()` is what ANV-24's interceptor calls when a refresh is refused — the real
    // "your session is gone" path, not a simulation of it.
    await act(async () => {
      getTokenStore().clear()
    })

    expect(await screen.findByTestId('route-login')).toBeInTheDocument()
    await waitFor(() => {
      expect(location()).toBe('/login?redirect=%2Fresearch%3Ftab%3Dnews')
    })
  })

  it('fires once, so several dead requests produce one navigation', async () => {
    window.localStorage.setItem(REFRESH_TOKEN_KEY, 'stored-refresh-token')
    renderAppAt('/portfolio')
    await screen.findByTestId('route-portfolio')

    await act(async () => {
      getTokenStore().clear()
      getTokenStore().clear()
      getTokenStore().clear()
    })

    expect(await screen.findByTestId('route-login')).toBeInTheDocument()
    // The second and third `clear()` found no session, so they added no history entry and
    // no second `redirect` param on top of the first.
    expect(location()).toBe('/login?redirect=%2Fportfolio')
  })

  it('settles on one destination and does not navigate again', async () => {
    window.localStorage.setItem(REFRESH_TOKEN_KEY, 'stored-refresh-token')
    renderAppAt('/research')
    await screen.findByTestId('route-research')

    await act(async () => {
      getTokenStore().clear()
    })
    await screen.findByTestId('route-login')

    // A second navigation racing the first — the protected match's `requireAuth` firing
    // again as the context goes anonymous — would land here. Verified by hand rather than
    // by this assertion alone: neutering `signOutNavigation` leaves the URL with no
    // `redirect` at all, which is what proves the guard is not quietly supplying it.
    await act(async () => {
      await Promise.resolve()
    })
    expect(location()).toBe('/login?redirect=%2Fresearch')
  })
})

/**
 * The logout button, end to end (ANV-28).
 *
 * ANV-26 unit-tested `logout()` against a stub `onSignOut`, and ANV-27 unit-tested
 * `signOutNavigation({reason: 'logout'})` as a pure function. Both passed, and yet **the
 * `logout` branch had never run once from end to end**, because nothing in the UI called
 * it: the button is what ANV-27's log asked ANV-28 to add, and these are the tests it asked
 * for with it.
 *
 * What they prove that the unit tests could not: that a real click on rendered markup
 * reaches `AuthProvider.logout` at all, and that the whole chain behind it — `endSession`
 * → `onSignOut({reason: SIGN_OUT_LOGOUT})` → `App`'s `handleSignOut` → `signOutNavigation`
 * → `router.navigate` — carries the *logout* reason rather than the expired-session one,
 * end to end, from a click. Both were verified by mutation: neutering the button's
 * `onClick` fails four of these, and changing `logout` to raise `SIGN_OUT_SESSION_EXPIRED`
 * fails three of them (plus ANV-26's own unit test). The assertion is `'/login'`
 * **exactly** — the absence of the `redirect` param is the whole point, because that param
 * is the only visible difference between the two reasons.
 *
 * **What they do *not* prove, stated rather than implied.** The second test below was
 * written to catch a competing navigation: `isAuthenticated` goes false while the user is
 * standing on a *protected* match, so if `RoutedApp` invalidated the router on a lost
 * session (ANV-27 deliberately made it invalidate only on a gained one), `requireAuth`
 * would re-run against the now-anonymous context and throw its own
 * `redirect({search: {redirect: '/research'}})`. **Reintroducing exactly that bug — dropping
 * the `if (isAuthenticated)` guard in `App.jsx` — leaves all 233 tests green**, because
 * `onSignOut`'s navigation is already in flight and has replaced the protected match by the
 * time the invalidation lands. So the race is not reachable from here; the test is kept for
 * the plain-`/login` assertion it shares with the first, and the ANV-27 rule it was aimed at
 * is covered by `router.test.js`'s unit test of `signOutNavigation` and by nothing else.
 */
describe('signing out', () => {
  const signedIn = (path) => {
    window.localStorage.setItem(REFRESH_TOKEN_KEY, 'stored-refresh-token')
    return renderAppAt(path)
  }

  it('lands on a plain /login — no redirect param — and forgets the refresh token', async () => {
    const user = userEvent.setup()
    signedIn('/research')
    await screen.findByTestId('route-research')

    const actions = within(screen.getByTestId('header-desktop-actions'))
    await user.click(actions.getByRole('button', { name: 'Log Out' }))

    expect(await screen.findByTestId('route-login')).toBeInTheDocument()
    // Not `toContain('/login')`: an expired session goes to `/login?redirect=…` and a
    // deliberate sign-out must not, so the difference *is* the assertion.
    expect(location()).toBe('/login')
    expect(window.localStorage.getItem(REFRESH_TOKEN_KEY)).toBeNull()
  })

  // Named for what it actually asserts. It was written as "…when the guards get a chance to
  // race it" and renamed after the mutation above showed it cannot discriminate that: the
  // sign-out navigation has already replaced the protected match by the time an invalidation
  // could re-run `requireAuth`. What it still adds over the first test is the settle — the
  // destination does not change *after* the login page has rendered.
  it('does not move again once it has landed on /login', async () => {
    const user = userEvent.setup()
    signedIn('/portfolio')
    await screen.findByTestId('route-portfolio')

    const actions = within(screen.getByTestId('header-desktop-actions'))
    await user.click(actions.getByRole('button', { name: 'Log Out' }))
    await screen.findByTestId('route-login')

    // Let anything queued behind the navigation settle; a later navigation would land here.
    await act(async () => {
      await Promise.resolve()
    })
    expect(location()).toBe('/login')
  })

  it('swaps the nav over to the signed-out links', async () => {
    const user = userEvent.setup()
    signedIn('/research')
    await screen.findByTestId('route-research')

    const actions = () => within(screen.getByTestId('header-desktop-actions'))
    expect(actions().queryByRole('link', { name: 'Log In' })).not.toBeInTheDocument()

    await user.click(actions().getByRole('button', { name: 'Log Out' }))
    await screen.findByTestId('route-login')

    expect(actions().getByRole('link', { name: 'Log In' })).toBeInTheDocument()
    expect(actions().queryByRole('button', { name: 'Log Out' })).not.toBeInTheDocument()
  })

  it('works from inside the mobile drawer, and the drawer closes behind it', async () => {
    const user = userEvent.setup()
    signedIn('/research')
    await screen.findByTestId('route-research')

    await user.click(screen.getByRole('button', { name: 'Open main menu' }))
    const drawer = within(screen.getByTestId('header-drawer'))
    await user.click(drawer.getByRole('button', { name: 'Log Out' }))

    expect(await screen.findByTestId('route-login')).toBeInTheDocument()
    expect(location()).toBe('/login')
    // The drawer closes because the *location* changed, not because the button said so —
    // which is why it also closes when a guard redirects and when Back is pressed.
    expect(screen.queryByTestId('header-drawer')).not.toBeInTheDocument()
  })
})
