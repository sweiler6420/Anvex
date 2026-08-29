import { HttpResponse, http } from 'msw'
import { StrictMode, useEffect } from 'react'
import { act, render, renderHook, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { LOGIN_PATH } from '@features/auth/api'
import {
  REFRESH_TOKEN_KEY,
  REMEMBERED_USERNAME_KEY,
  rememberUsername,
} from '@features/auth/authStorage'
import { useAuth } from '@hooks/useAuth'
import {
  ANONYMOUS_TOKEN_STORE,
  authApi,
  getTokenStore,
  REFRESH_PATH,
  resetRefreshState,
  resetTokenStore,
} from '@lib/api'
import { apiUrl } from '@lib/env'
import { errorResponse, pageResponse } from '@test/msw/handlers'
import { server } from '@test/msw/server'

import { AuthProvider, SIGN_OUT_LOGOUT, SIGN_OUT_SESSION_EXPIRED } from './AuthProvider'

/**
 * ANV-26, tested through the **real** transport (CLAUDE.md §5): MSW answers real HTTP and
 * nothing stubs `axios`, so the refresh/replay path that the store plugs into is genuinely
 * exercised rather than mocked away.
 *
 * The assertions that matter are on the *contents of `localStorage`*, not on the store's
 * API surface. "We never offered a way to persist the access token" is a much weaker claim
 * than "after a login and a rotation, no value in browser storage is the access token".
 */

const PROTECTED_PATH = '/v1/stocks'

/** The live context, captured so a test can call `login`/`logout` directly. */
let auth = null

function Probe() {
  auth = useAuth()
  return <span data-testid="authenticated">{String(auth.isAuthenticated)}</span>
}

function mount({ onSignOut, wrapper: Wrapper } = {}) {
  const tree = (
    <AuthProvider onSignOut={onSignOut}>
      <Probe />
      {Wrapper ? <Wrapper /> : null}
    </AuthProvider>
  )
  return render(tree)
}

const signedIn = () => screen.getByTestId('authenticated').textContent === 'true'

/** Every value currently in `localStorage`, for the "never persisted" assertions. */
const storageDump = () =>
  Object.fromEntries(
    Object.keys(window.localStorage).map((key) => [key, window.localStorage.getItem(key)]),
  )

/**
 * A miniature Anvex with the auth semantics that matter, matching ANV-24's own mock:
 * `/v1/auth/refresh` **rotates**, is single-use, and refuses a spent token; the protected
 * route 401s `token_expired` for anything but the current access token.
 */
function mockApi({ accessToken = 'access-0', refreshToken = 'refresh-0' } = {}) {
  const state = { accessToken, refreshToken, refreshCalls: 0, protectedCalls: 0 }

  server.use(
    http.post(apiUrl(LOGIN_PATH), async ({ request }) => {
      const form = new URLSearchParams(await request.text())
      if (form.get('password') !== 'hunter2') {
        return errorResponse('unauthorized', 'Incorrect username or password.', { status: 401 })
      }
      return HttpResponse.json({
        access_token: state.accessToken,
        refresh_token: state.refreshToken,
        token_type: 'bearer',
      })
    }),

    http.post(apiUrl(REFRESH_PATH), async ({ request }) => {
      state.refreshCalls += 1
      const body = await request.json()
      if (body?.refresh_token !== state.refreshToken) {
        return errorResponse('invalid_token', 'That refresh token is not usable.', { status: 401 })
      }
      state.accessToken = `access-${state.refreshCalls}`
      state.refreshToken = `refresh-${state.refreshCalls}`
      return HttpResponse.json({
        access_token: state.accessToken,
        refresh_token: state.refreshToken,
        token_type: 'bearer',
      })
    }),

    http.get(apiUrl(PROTECTED_PATH), ({ request }) => {
      state.protectedCalls += 1
      if (request.headers.get('Authorization') !== `Bearer ${state.accessToken}`) {
        return errorResponse('token_expired', 'Your session has expired.', { status: 401 })
      }
      return pageResponse([])
    }),
  )

  return state
}

beforeEach(() => {
  auth = null
  window.localStorage.clear()
  resetTokenStore()
  resetRefreshState()
})

afterEach(() => {
  resetTokenStore()
  resetRefreshState()
  window.localStorage.clear()
})

// --------------------------------------------------------------------------------- login

describe('AuthProvider login', () => {
  it('stores both halves of the pair and reports the session', async () => {
    mockApi({ accessToken: 'access-A', refreshToken: 'refresh-A' })
    mount()

    expect(signedIn()).toBe(false)

    await act(() => auth.login({ username: 'ada', password: 'hunter2' }))

    expect(signedIn()).toBe(true)
    // Both halves reach the transport. Only one of them reaches storage.
    expect(getTokenStore().getAccessToken()).toBe('access-A')
    expect(getTokenStore().getRefreshToken()).toBe('refresh-A')
    expect(window.localStorage.getItem(REFRESH_TOKEN_KEY)).toBe('refresh-A')
  })

  it('never writes the access token to localStorage', async () => {
    mockApi({ accessToken: 'access-A', refreshToken: 'refresh-A' })
    mount()

    await act(() => auth.login({ username: 'ada', password: 'hunter2' }))

    // Asserted on the storage *contents*, not on the store's API: every key and every value
    // is inspected, so a stray `setItem` anywhere in the auth path fails this — including
    // one that spelled the key something other than the two this feature owns.
    const dump = storageDump()
    expect(Object.keys(dump)).toEqual([REFRESH_TOKEN_KEY])
    for (const value of Object.values(dump)) {
      expect(value).not.toContain('access-A')
    }
    expect(JSON.stringify(dump)).not.toContain('access-A')
  })

  it('never writes the password anywhere, whatever "remember me" did', async () => {
    mockApi()
    mount()
    // ANV-29 owns the checkbox; the username is all it is allowed to keep.
    rememberUsername('ada')

    await act(() => auth.login({ username: 'ada', password: 'hunter2' }))

    const dump = storageDump()
    expect(Object.keys(dump).sort()).toEqual([REFRESH_TOKEN_KEY, REMEMBERED_USERNAME_KEY].sort())
    expect(dump[REMEMBERED_USERNAME_KEY]).toBe('ada')
    // The bug being fixed: `localStorage.setItem("pass", JSON.stringify(password))`.
    expect(JSON.stringify(dump)).not.toContain('hunter2')
  })

  it('rejects with the ApiError and leaves the session anonymous', async () => {
    mockApi()
    mount()

    // Caught *inside* the act scope: letting a rejection escape `act()` leaves React's
    // acting depth unbalanced, and the next test's `render()` then never flushes.
    let rejection
    await act(async () => {
      rejection = await auth.login({ username: 'ada', password: 'wrong' }).catch((err) => err)
    })

    expect(rejection).toMatchObject({ name: 'ApiError', code: 'unauthorized', status: 401 })
    expect(signedIn()).toBe(false)
    expect(storageDump()).toEqual({})
  })
})

// -------------------------------------------------------------------------------- logout

describe('AuthProvider logout', () => {
  it('clears both tokens and reports signed out', async () => {
    mockApi()
    const onSignOut = vi.fn()
    mount({ onSignOut })
    await act(() => auth.login({ username: 'ada', password: 'hunter2' }))

    act(() => auth.logout())

    expect(signedIn()).toBe(false)
    expect(getTokenStore().getAccessToken()).toBeNull()
    expect(getTokenStore().getRefreshToken()).toBeNull()
    expect(window.localStorage.getItem(REFRESH_TOKEN_KEY)).toBeNull()
    expect(onSignOut).toHaveBeenCalledExactlyOnceWith({ reason: SIGN_OUT_LOGOUT })
  })

  it('keeps the remembered username — signing out is not "forget who I am"', async () => {
    mockApi()
    mount()
    rememberUsername('ada')
    await act(() => auth.login({ username: 'ada', password: 'hunter2' }))

    act(() => auth.logout())

    expect(window.localStorage.getItem(REMEMBERED_USERNAME_KEY)).toBe('ada')
  })

  it('does not fire onSignOut when there was no session to end', () => {
    const onSignOut = vi.fn()
    mount({ onSignOut })

    act(() => auth.logout())

    expect(onSignOut).not.toHaveBeenCalled()
  })
})

// ------------------------------------------------------------------------------- restore

describe('AuthProvider restore', () => {
  it('is signed in on the very first render when a refresh token is stored', () => {
    window.localStorage.setItem(REFRESH_TOKEN_KEY, 'refresh-stored')

    mount()

    // No await, no `waitFor`, no pending state: the answer is known during the first
    // render, which is what lets ANV-27 guard a route without a boot spinner. The old
    // PersistLogin blocked the entire tree — public pages included — until a network call
    // it made on every load came back.
    expect(signedIn()).toBe(true)
    expect(getTokenStore().getRefreshToken()).toBe('refresh-stored')
  })

  it('makes no network call on boot', () => {
    const seen = []
    server.events.on('request:start', ({ request }) => seen.push(request.url))

    window.localStorage.setItem(REFRESH_TOKEN_KEY, 'refresh-stored')
    mount()

    // Deliberate: the transport refreshes on the first protected 401 (ANV-24), so a boot
    // refresh would be a second implementation that costs a round trip and burns a
    // rotation on every page load, including loads that never touch a protected route.
    expect(seen).toEqual([])
    server.events.removeAllListeners('request:start')
  })

  it('fails cleanly with no stored refresh token', () => {
    mount()

    expect(signedIn()).toBe(false)
    expect(getTokenStore().getAccessToken()).toBeNull()
    expect(getTokenStore().getRefreshToken()).toBeNull()
  })

  it('treats an empty stored token as no session', () => {
    window.localStorage.setItem(REFRESH_TOKEN_KEY, '')

    mount()

    expect(signedIn()).toBe(false)
  })

  it('re-reads storage when called explicitly, and returns what it found', () => {
    mount()
    expect(signedIn()).toBe(false)

    window.localStorage.setItem(REFRESH_TOKEN_KEY, 'refresh-later')
    let found
    act(() => {
      found = auth.restore()
    })

    expect(found).toBe(true)
    expect(signedIn()).toBe(true)
  })

  it('does not fire onSignOut when boot finds nothing', () => {
    const onSignOut = vi.fn()
    mount({ onSignOut })

    // Arriving anonymous is not a session *ending*, and redirecting on it would bounce
    // every first-time visitor off the home page.
    expect(onSignOut).not.toHaveBeenCalled()
  })

  it('survives a browser that refuses to read storage', () => {
    // Seeded first, and spied on `Storage.prototype` rather than on the storage object —
    // jsdom's is a Proxy that turns `vi.spyOn(window.localStorage, 'getItem')` into a stored
    // *item* named "getItem", leaving the real method in place.
    window.localStorage.setItem(REFRESH_TOKEN_KEY, 'refresh-stored')
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('The operation is insecure.', 'SecurityError')
    })

    expect(() => mount()).not.toThrow()
    // Site data blocked means "not signed in", not a crash on the first render.
    expect(signedIn()).toBe(false)
  })
})

// ---------------------------------------------------------------------- the ANV-24 seam

describe('AuthProvider token store installation', () => {
  it('installs on mount and uninstalls on unmount', async () => {
    mockApi()
    expect(getTokenStore()).toBe(ANONYMOUS_TOKEN_STORE)

    const { unmount } = mount()
    await act(() => auth.login({ username: 'ada', password: 'hunter2' }))
    expect(getTokenStore()).not.toBe(ANONYMOUS_TOKEN_STORE)
    expect(getTokenStore().getAccessToken()).toBe('access-0')

    unmount()

    // Back to the signed-out no-op. A store left installed by a torn-down provider would
    // hand a dead ref's token to the next tree that mounted.
    expect(getTokenStore()).toBe(ANONYMOUS_TOKEN_STORE)
  })

  it('is installed before a descendant’s mount effect runs', () => {
    window.localStorage.setItem(REFRESH_TOKEN_KEY, 'refresh-stored')
    let seenByChild = 'not-run'

    function EagerChild() {
      useEffect(() => {
        seenByChild = getTokenStore().getRefreshToken()
      }, [])
      return null
    }

    mount({ wrapper: EagerChild })

    // React runs effects bottom-up, so an install that happened only in *this* provider's
    // effect would leave every child — and ANV-27's RouterProvider, which starts the first
    // route load from an effect — reading the anonymous store: no token, a 401 on the first
    // protected call, and a spurious sign-out on the first paint after a reload.
    expect(seenByChild).toBe('refresh-stored')
  })

  it('survives StrictMode’s mount / unmount / remount', () => {
    window.localStorage.setItem(REFRESH_TOKEN_KEY, 'refresh-stored')

    const { unmount } = render(
      <StrictMode>
        <AuthProvider>
          <Probe />
        </AuthProvider>
      </StrictMode>,
    )

    expect(getTokenStore().getRefreshToken()).toBe('refresh-stored')

    unmount()

    expect(getTokenStore()).toBe(ANONYMOUS_TOKEN_STORE)
  })
})

// ------------------------------------------------------------------------ rotation / 401

describe('AuthProvider and the refresh path', () => {
  it('persists the new refresh token when the transport rotates the pair', async () => {
    const api = mockApi({ accessToken: 'access-0', refreshToken: 'refresh-0' })
    mount()
    await act(() => auth.login({ username: 'ada', password: 'hunter2' }))

    // Expire the access token behind the client's back. The next protected call 401s
    // `token_expired`, ANV-24's interceptor refreshes and replays it, and `setTokens` lands
    // here with the **whole rotated pair**.
    api.accessToken = 'access-rotated'

    await act(async () => {
      await authApi.get(PROTECTED_PATH)
    })

    expect(api.refreshCalls).toBe(1)
    // The point of the whole seam: storing only the access token would leave `refresh-0` in
    // localStorage, and the *next* refresh would present a token the API has already spent.
    expect(window.localStorage.getItem(REFRESH_TOKEN_KEY)).toBe('refresh-1')
    expect(getTokenStore().getAccessToken()).toBe('access-1')
    expect(signedIn()).toBe(true)

    // And the proof it is not merely stored but usable: a second expiry refreshes again
    // rather than being refused for presenting a spent token.
    api.accessToken = 'access-expired-again'
    await act(async () => {
      await authApi.get(PROTECTED_PATH)
    })
    expect(api.refreshCalls).toBe(2)
    expect(window.localStorage.getItem(REFRESH_TOKEN_KEY)).toBe('refresh-2')
  })

  it('still keeps the access token out of storage after a rotation', async () => {
    const api = mockApi()
    mount()
    await act(() => auth.login({ username: 'ada', password: 'hunter2' }))
    api.accessToken = 'access-expired'

    await act(async () => {
      await authApi.get(PROTECTED_PATH)
    })

    const dump = storageDump()
    expect(Object.keys(dump)).toEqual([REFRESH_TOKEN_KEY])
    expect(JSON.stringify(dump)).not.toContain(getTokenStore().getAccessToken())
  })

  it('ends the session and reports why when the refresh is refused', async () => {
    const api = mockApi()
    const onSignOut = vi.fn()
    mount({ onSignOut })
    await act(() => auth.login({ username: 'ada', password: 'hunter2' }))

    // The refresh token is no longer the one the API holds: a refusal, not a blip.
    api.accessToken = 'access-expired'
    api.refreshToken = 'refresh-somebody-else-rotated'

    await act(async () => {
      await expect(authApi.get(PROTECTED_PATH)).rejects.toMatchObject({ status: 401 })
    })

    expect(signedIn()).toBe(false)
    expect(window.localStorage.getItem(REFRESH_TOKEN_KEY)).toBeNull()
    // `clear()` is where the log-out lands; the transport never navigates, so the redirect
    // is this callback's job — ANV-27 turns `session_expired` into `/login?redirect=…`.
    expect(onSignOut).toHaveBeenCalledExactlyOnceWith({ reason: SIGN_OUT_SESSION_EXPIRED })
  })

  it('fires onSignOut once when several requests are refused unrecoverably', async () => {
    mockApi()
    const onSignOut = vi.fn()
    mount({ onSignOut })
    await act(() => auth.login({ username: 'ada', password: 'hunter2' }))

    // `invalid_token` is one of the two codes refreshing cannot fix, so the transport does
    // **not** go single-flight here: each of the three requests calls `clear()` directly.
    // That is what makes this a test of the provider's own guard rather than of ANV-24's.
    server.use(
      http.get(apiUrl(PROTECTED_PATH), () =>
        errorResponse('invalid_token', 'That token is not usable.', { status: 401 }),
      ),
    )

    await act(async () => {
      const results = await Promise.allSettled([
        authApi.get(PROTECTED_PATH),
        authApi.get(PROTECTED_PATH),
        authApi.get(PROTECTED_PATH),
      ])
      expect(results.every((r) => r.status === 'rejected')).toBe(true)
    })

    // One dead session, one redirect. Three would be three navigations racing each other.
    expect(onSignOut).toHaveBeenCalledTimes(1)
    expect(onSignOut).toHaveBeenCalledWith({ reason: SIGN_OUT_SESSION_EXPIRED })
  })

  it('keeps the tokens when the refresh fails for a reason that is not a refusal', async () => {
    const api = mockApi()
    const onSignOut = vi.fn()
    mount({ onSignOut })
    await act(() => auth.login({ username: 'ada', password: 'hunter2' }))

    api.accessToken = 'access-expired'
    server.use(http.post(apiUrl(REFRESH_PATH), () => HttpResponse.error()))

    await act(async () => {
      await expect(authApi.get(PROTECTED_PATH)).rejects.toMatchObject({ code: 'network_error' })
    })

    // ANV-24's rule, observed from the store's side: signing a user out because their wifi
    // blipped discards a refresh token that is still perfectly valid.
    expect(signedIn()).toBe(true)
    expect(window.localStorage.getItem(REFRESH_TOKEN_KEY)).toBe('refresh-0')
    expect(onSignOut).not.toHaveBeenCalled()
  })

  it('recovers a reloaded session from the stored refresh token alone', async () => {
    const api = mockApi({ accessToken: 'access-0', refreshToken: 'refresh-0' })
    // Exactly the state after a page reload: a refresh token, and no access token anywhere.
    window.localStorage.setItem(REFRESH_TOKEN_KEY, 'refresh-0')
    mount()

    expect(signedIn()).toBe(true)
    expect(getTokenStore().getAccessToken()).toBeNull()

    // "For persist-login, just fire the first protected call." The 401 is `token_expired`
    // here and `unauthorized` against the real API; both refresh, by ANV-24's framing.
    let page
    await act(async () => {
      page = await authApi.get(PROTECTED_PATH)
    })

    expect(page.status).toBe(200)
    expect(api.refreshCalls).toBe(1)
    await waitFor(() => expect(getTokenStore().getAccessToken()).toBe('access-1'))
    expect(window.localStorage.getItem(REFRESH_TOKEN_KEY)).toBe('refresh-1')
  })
})

// -------------------------------------------------------------------------------- useAuth

describe('useAuth', () => {
  it('throws outside the provider, naming it', () => {
    // The old hook returned the context default `{}`, so a component outside the provider
    // destructured `undefined` and failed somewhere else entirely.
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    expect(() => renderHook(() => useAuth())).toThrow(/useAuth must be used inside <AuthProvider>/)
    spy.mockRestore()
  })

  it('exposes exactly the session surface, and not the access token', async () => {
    mockApi()
    mount()
    await act(() => auth.login({ username: 'ada', password: 'hunter2' }))

    expect(Object.keys(auth).sort()).toEqual(['isAuthenticated', 'login', 'logout', 'restore'])
    expect(JSON.stringify(Object.keys(auth))).not.toContain('ccessToken')
  })
})
