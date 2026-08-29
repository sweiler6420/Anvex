import { act, render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import App from './App'
import { login as loginRequest, LOGIN_PATH } from '@features/auth/api'
import { REFRESH_TOKEN_KEY } from '@features/auth/authStorage'
import { getTokenStore, resetTokenStore } from '@lib/api'
import { apiUrl } from '@lib/env'
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
  return render(<App />)
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
