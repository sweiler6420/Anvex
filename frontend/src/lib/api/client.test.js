import { HttpResponse, delay, http } from 'msw'
import { afterEach, describe, expect, it } from 'vitest'

import { apiUrl } from '../env'
import { errorResponse, pageResponse } from '@test/msw/handlers'
import { server } from '@test/msw/server'

import { authApi, publicApi, REFRESH_PATH, resetRefreshState } from './client'
import { CLIENT_ERROR_CODES, isNetworkError } from './errors'
import { installTokenStore, resetTokenStore } from './tokenStore'

/**
 * ANV-24's tests, at the network boundary.
 *
 * **Nothing here stubs `fetch` or `axios`** (CLAUDE.md §5) — doing so would mock away the
 * interceptors that are the entire subject of this file. MSW answers the real HTTP the
 * real axios instances send, and every mocked body is built with `errorResponse()` /
 * `pageResponse()` so a handler cannot invent a shape the backend would never produce.
 *
 * The mock API below is faithful about the one thing the old client got wrong:
 * **`/v1/auth/refresh` rotates, and a spent refresh token is refused.** That is what makes
 * the concurrency test meaningful rather than decorative — see its own comment.
 */

/** A stand-in for ANV-26's auth store, recording what the transport asked it to do. */
function fakeTokenStore({ accessToken = null, refreshToken = null } = {}) {
  const store = {
    accessToken,
    refreshToken,
    cleared: 0,
    saved: [],
    getAccessToken: () => store.accessToken,
    getRefreshToken: () => store.refreshToken,
    setTokens: (pair) => {
      store.saved.push(pair)
      store.accessToken = pair.accessToken
      store.refreshToken = pair.refreshToken
    },
    clear: () => {
      store.cleared += 1
      store.accessToken = null
      store.refreshToken = null
    },
  }
  installTokenStore(store)
  return store
}

const PROBE_PATH = '/v1/stocks'

/**
 * A miniature Anvex, with the auth semantics that matter:
 *
 *  - the protected route 401s `token_expired` for anything but the current access token;
 *  - `/v1/auth/refresh` takes a **JSON body**, is **single-use**, and returns a **new pair**;
 *  - presenting an already-rotated refresh token is a 401 `invalid_token`.
 */
function mockApi({ accessToken = 'access-0', refreshToken = 'refresh-0', refreshMs = 25 } = {}) {
  const state = {
    accessToken,
    refreshToken,
    refreshCalls: 0,
    refreshBodies: [],
    probeHeaders: [],
    rotations: 0,
  }

  server.use(
    http.get(apiUrl(PROBE_PATH), ({ request }) => {
      const header = request.headers.get('authorization')
      state.probeHeaders.push(header)
      if (header !== `Bearer ${state.accessToken}`) {
        return errorResponse('token_expired', 'The access token has expired.', { status: 401 })
      }
      return pageResponse([{ id: 'stock-1', ticker: 'AAPL' }])
    }),

    http.post(apiUrl(REFRESH_PATH), async ({ request }) => {
      state.refreshCalls += 1
      const body = await request.json().catch(() => null)
      state.refreshBodies.push(body)

      // The delay is load-bearing: it guarantees the refreshes overlap, so a client
      // without a single-flight guard genuinely races here rather than accidentally
      // serialising and passing.
      await delay(refreshMs)

      if (body?.refresh_token !== state.refreshToken) {
        return errorResponse('invalid_token', 'The refresh token is not valid.', { status: 401 })
      }

      state.rotations += 1
      state.accessToken = `access-${state.rotations}`
      state.refreshToken = `refresh-${state.rotations}`
      return HttpResponse.json({
        access_token: state.accessToken,
        refresh_token: state.refreshToken,
        token_type: 'bearer',
      })
    }),
  )

  return state
}

afterEach(() => {
  resetTokenStore()
  resetRefreshState()
})

// --------------------------------------------------------------------------------------

describe('the authenticated instance', () => {
  it('attaches the bearer token', async () => {
    const api = mockApi()
    fakeTokenStore({ accessToken: api.accessToken, refreshToken: api.refreshToken })

    const response = await authApi.get(PROBE_PATH)

    expect(response.status).toBe(200)
    expect(response.data.items).toEqual([{ id: 'stock-1', ticker: 'AAPL' }])
    expect(api.probeHeaders).toEqual(['Bearer access-0'])
    expect(api.refreshCalls).toBe(0)
  })

  it('sends no Authorization header at all when there is no token', async () => {
    const api = mockApi()
    fakeTokenStore()

    // No token and no refresh token: the 401 is terminal, and the header was never
    // `Bearer null`, which is a request the API would have had to parse and reject.
    await expect(authApi.get(PROBE_PATH)).rejects.toMatchObject({ code: 'unauthorized' })
    expect(api.probeHeaders).toEqual([null])
    expect(api.refreshCalls).toBe(0)
  })
})

describe('refresh on 401', () => {
  it('refreshes once and replays the original request with the new token', async () => {
    const api = mockApi()
    const store = fakeTokenStore({ accessToken: 'access-stale', refreshToken: 'refresh-0' })

    const response = await authApi.get(PROBE_PATH)

    expect(response.data.items).toHaveLength(1)
    expect(api.refreshCalls).toBe(1)
    // A JSON body — the old client posted FormData to a query-parameter endpoint.
    expect(api.refreshBodies).toEqual([{ refresh_token: 'refresh-0' }])
    // The stale attempt, then the replay carrying the rotated token.
    expect(api.probeHeaders).toEqual(['Bearer access-stale', 'Bearer access-1'])
    // Both halves stored: the endpoint rotates, so keeping only the access token would
    // break the next refresh.
    expect(store.saved).toEqual([{ accessToken: 'access-1', refreshToken: 'refresh-1' }])
    expect(store.cleared).toBe(0)
  })

  /**
   * **The headline test.** Five requests expire together; exactly one refresh goes out.
   *
   * Two independent assertions fail if the single-flight guard is removed, which is what
   * stops this being a test that passes either way:
   *
   *  1. `refreshCalls` would be 5, not 1 — a direct count of the network calls.
   *  2. Four of the five requests would *fail*. The mock refresh endpoint is single-use,
   *     exactly like the real one: the second refresh presents a token the first has
   *     already rotated away, gets a 401 `invalid_token`, and takes its request down with
   *     it. That is the user-visible bug in the old app — a dashboard firing three calls
   *     on mount logs you out on the first expiry.
   *
   * Verified by removing the guard and re-running: `refreshCalls` was 5 and four requests
   * rejected with `invalid_token`.
   */
  it('fires exactly ONE refresh for N concurrent 401s and replays all of them', async () => {
    const api = mockApi()
    const store = fakeTokenStore({ accessToken: 'access-stale', refreshToken: 'refresh-0' })
    const CONCURRENCY = 5

    const responses = await Promise.all(
      Array.from({ length: CONCURRENCY }, () => authApi.get(PROBE_PATH)),
    )

    expect(api.refreshCalls).toBe(1)
    expect(api.rotations).toBe(1)
    expect(responses).toHaveLength(CONCURRENCY)
    for (const response of responses) {
      expect(response.status).toBe(200)
      expect(response.data.items).toHaveLength(1)
    }

    expect(api.probeHeaders.filter((h) => h === 'Bearer access-stale')).toHaveLength(CONCURRENCY)
    expect(api.probeHeaders.filter((h) => h === 'Bearer access-1')).toHaveLength(CONCURRENCY)
    expect(store.saved).toHaveLength(1)
    expect(store.cleared).toBe(0)
  })

  it('does not hold a spent promise: a later expiry refreshes again', async () => {
    const api = mockApi()
    fakeTokenStore({ accessToken: 'access-stale', refreshToken: 'refresh-0' })

    await authApi.get(PROBE_PATH)
    // The server rotates behind our back — a second expiry, well after the first settled.
    api.accessToken = 'access-rotated-elsewhere'
    api.refreshToken = 'refresh-1'
    await authApi.get(PROBE_PATH)

    expect(api.refreshCalls).toBe(2)
  })
})

describe('401s a refresh cannot fix', () => {
  it.each(['invalid_token', 'wrong_token_type'])('ends the session on %s', async (code) => {
    const api = mockApi()
    server.use(
      http.get(apiUrl(PROBE_PATH), () => errorResponse(code, 'No.', { status: 401 })),
    )
    const store = fakeTokenStore({ accessToken: 'access-0', refreshToken: 'refresh-0' })

    await expect(authApi.get(PROBE_PATH)).rejects.toMatchObject({ code, status: 401 })

    expect(api.refreshCalls).toBe(0)
    expect(store.cleared).toBe(1)
  })
})

describe('403', () => {
  it('is passed straight through and never triggers a refresh', async () => {
    // The old `useAxiosPrivate` refreshed on exactly this status and never on 401.
    const api = mockApi()
    server.use(
      http.get(apiUrl(PROBE_PATH), () =>
        errorResponse('forbidden', 'You do not have permission to perform this action.', {
          status: 403,
        }),
      ),
    )
    const store = fakeTokenStore({ accessToken: 'access-0', refreshToken: 'refresh-0' })

    await expect(authApi.get(PROBE_PATH)).rejects.toMatchObject({
      code: 'forbidden',
      status: 403,
    })

    expect(api.refreshCalls).toBe(0)
    expect(store.cleared).toBe(0)
    expect(store.accessToken).toBe('access-0')
  })
})

describe('a refused refresh', () => {
  it('clears auth, rejects the original request, and does not loop', async () => {
    const api = mockApi()
    server.use(
      http.post(apiUrl(REFRESH_PATH), () => {
        api.refreshCalls += 1
        return errorResponse('invalid_token', 'The refresh token is not valid.', { status: 401 })
      }),
    )
    const store = fakeTokenStore({ accessToken: 'access-stale', refreshToken: 'refresh-bad' })

    await expect(authApi.get(PROBE_PATH)).rejects.toMatchObject({
      code: 'invalid_token',
      status: 401,
    })

    expect(api.refreshCalls).toBe(1)
    expect(store.cleared).toBe(1)
    expect(store.accessToken).toBeNull()
    // One attempt at the protected route. A retry loop would show up here as many.
    expect(api.probeHeaders).toEqual(['Bearer access-stale'])
  })

  it('takes every queued request down with it, still refreshing once', async () => {
    const api = mockApi()
    server.use(
      http.post(apiUrl(REFRESH_PATH), async () => {
        api.refreshCalls += 1
        await delay(25)
        return errorResponse('invalid_token', 'The refresh token is not valid.', { status: 401 })
      }),
    )
    const store = fakeTokenStore({ accessToken: 'access-stale', refreshToken: 'refresh-bad' })

    const results = await Promise.allSettled(
      Array.from({ length: 4 }, () => authApi.get(PROBE_PATH)),
    )

    expect(results.every((r) => r.status === 'rejected')).toBe(true)
    expect(results.map((r) => r.reason.code)).toEqual(Array(4).fill('invalid_token'))
    expect(api.refreshCalls).toBe(1)
    expect(store.cleared).toBe(1)
  })

  it('does NOT clear the session when the refresh never reached the API', async () => {
    // A blipped connection is not a refusal. Clearing here would sign a user out because
    // their wifi dropped, discarding tokens that are still perfectly valid.
    const api = mockApi()
    server.use(http.post(apiUrl(REFRESH_PATH), () => HttpResponse.error()))
    const store = fakeTokenStore({ accessToken: 'access-stale', refreshToken: 'refresh-0' })

    await expect(authApi.get(PROBE_PATH)).rejects.toMatchObject({
      code: CLIENT_ERROR_CODES.NETWORK,
      status: null,
    })

    expect(store.cleared).toBe(0)
    expect(store.refreshToken).toBe('refresh-0')
    expect(api.probeHeaders).toEqual(['Bearer access-stale'])
  })

  it('does NOT clear the session when the refresh endpoint 500s', async () => {
    mockApi()
    server.use(
      http.post(apiUrl(REFRESH_PATH), () =>
        errorResponse('internal_error', 'An unexpected error occurred.', { status: 500 }),
      ),
    )
    const store = fakeTokenStore({ accessToken: 'access-stale', refreshToken: 'refresh-0' })

    await expect(authApi.get(PROBE_PATH)).rejects.toMatchObject({
      code: 'internal_error',
      status: 500,
    })

    expect(store.cleared).toBe(0)
    expect(store.refreshToken).toBe('refresh-0')
  })

  it('refuses a refresh response that is not a token pair', async () => {
    mockApi()
    server.use(
      http.post(apiUrl(REFRESH_PATH), () => HttpResponse.json({ access_token: 'only-half' })),
    )
    const store = fakeTokenStore({ accessToken: 'access-stale', refreshToken: 'refresh-0' })

    await expect(authApi.get(PROBE_PATH)).rejects.toMatchObject({
      code: CLIENT_ERROR_CODES.MALFORMED,
    })
    expect(store.saved).toEqual([])
    expect(store.cleared).toBe(1)
  })
})

describe('the error envelope', () => {
  it('reaches the caller as a code, a message, details and a request id', async () => {
    server.use(
      http.get(apiUrl('/v1/stocks/by-ticker/ZZZZ'), () =>
        errorResponse('not_found', "stock 'ZZZZ' was not found.", {
          status: 404,
          details: { resource: 'stock', identifier: 'ZZZZ' },
        }),
      ),
    )
    fakeTokenStore({ accessToken: 'access-0', refreshToken: 'refresh-0' })

    const error = await authApi.get('/v1/stocks/by-ticker/ZZZZ').then(
      () => {
        throw new Error('expected a rejection')
      },
      (err) => err,
    )

    expect(error.name).toBe('ApiError')
    expect(error.code).toBe('not_found')
    expect(error.status).toBe(404)
    expect(error.message).toBe("stock 'ZZZZ' was not found.")
    expect(error.details).toEqual({ resource: 'stock', identifier: 'ZZZZ' })
    expect(error.requestId).toMatch(/^msw-/)
    // No axios internals leak out.
    expect(error.response).toBeUndefined()
    expect(error.isAxiosError).toBeUndefined()
  })
})

describe('a network failure', () => {
  it('is a code and a null status, not a missing `response` a caller has to notice', async () => {
    server.use(http.get(apiUrl(PROBE_PATH), () => HttpResponse.error()))
    fakeTokenStore({ accessToken: 'access-0', refreshToken: 'refresh-0' })

    const error = await authApi.get(PROBE_PATH).then(
      () => {
        throw new Error('expected a rejection')
      },
      (err) => err,
    )

    expect(error.name).toBe('ApiError')
    expect(error.code).toBe(CLIENT_ERROR_CODES.NETWORK)
    expect(error.status).toBeNull()
    expect(error.details).toEqual({})
    expect(error.isTransportFailure).toBe(true)
    expect(isNetworkError(error)).toBe(true)
  })

  it('reaches the public instance the same way', async () => {
    server.use(http.post(apiUrl('/v1/auth/login'), () => HttpResponse.error()))

    await expect(publicApi.post('/v1/auth/login', {})).rejects.toMatchObject({
      code: CLIENT_ERROR_CODES.NETWORK,
      status: null,
    })
  })
})

describe('the public instance', () => {
  it('sends no credentials and never refreshes on a 401', async () => {
    const api = mockApi()
    let seenAuthHeader
    server.use(
      http.post(apiUrl('/v1/auth/login'), ({ request }) => {
        seenAuthHeader = request.headers.get('authorization')
        return errorResponse('unauthorized', 'Incorrect username or password.', { status: 401 })
      }),
    )
    // A signed-in store, to prove the public instance ignores it.
    fakeTokenStore({ accessToken: 'access-0', refreshToken: 'refresh-0' })

    await expect(publicApi.post('/v1/auth/login', {})).rejects.toMatchObject({
      code: 'unauthorized',
      status: 401,
    })

    expect(seenAuthHeader).toBeNull()
    expect(api.refreshCalls).toBe(0)
  })
})
