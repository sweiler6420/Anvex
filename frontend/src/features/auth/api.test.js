import { HttpResponse, http } from 'msw'
import { describe, expect, it } from 'vitest'

import { apiUrl } from '@lib/env'
import { errorResponse } from '@test/msw/handlers'
import { server } from '@test/msw/server'

import { LOGIN_PATH, RECOVERY_PATH, login, requestRecovery } from './api'

/**
 * ANV-26's per-resource calls, at the network boundary (CLAUDE.md §5). MSW answers the real
 * HTTP the real axios instance sends — nothing here stubs `axios`, which is what makes the
 * content-type assertion below meaningful rather than a restatement of the source.
 */

/** Records what the endpoint actually received, so the form encoding can be asserted. */
function mockLogin({ accessToken = 'access-1', refreshToken = 'refresh-1' } = {}) {
  const seen = { contentType: null, body: null, count: 0 }

  server.use(
    http.post(apiUrl(LOGIN_PATH), async ({ request }) => {
      seen.count += 1
      seen.contentType = request.headers.get('content-type')
      seen.body = await request.text()
      return HttpResponse.json({
        access_token: accessToken,
        refresh_token: refreshToken,
        token_type: 'bearer',
      })
    }),
  )

  return seen
}

describe('features/auth/api login', () => {
  it('posts form-encoded credentials and returns the camel-cased pair', async () => {
    const seen = mockLogin()

    const pair = await login({ username: 'ada', password: 'hunter2' })

    expect(pair).toEqual({ accessToken: 'access-1', refreshToken: 'refresh-1' })
    // The override is load-bearing: both axios instances default to application/json, and
    // axios only infers a form content type when nothing has set the header. Without it the
    // API — an OAuth2PasswordRequestForm endpoint — answers 422.
    expect(seen.contentType).toMatch(/^application\/x-www-form-urlencoded/)
    expect(seen.body).toBe('username=ada&password=hunter2')
  })

  it('percent-encodes a password a hand-built body would corrupt', async () => {
    const seen = mockLogin()

    await login({ username: 'a@b.com', password: 'p&ss+w rd=ü' })

    expect(new URLSearchParams(seen.body).get('username')).toBe('a@b.com')
    expect(new URLSearchParams(seen.body).get('password')).toBe('p&ss+w rd=ü')
  })

  it('rejects a bad password as an ApiError with the backend code', async () => {
    server.use(
      http.post(apiUrl(LOGIN_PATH), () =>
        errorResponse('unauthorized', 'Incorrect username or password.', { status: 401 }),
      ),
    )

    // A rejection, not a `{status, message, error}` object the caller has to probe — the
    // old `useApi.js` returned the latter from five differently-shaped functions.
    await expect(login({ username: 'ada', password: 'wrong' })).rejects.toMatchObject({
      name: 'ApiError',
      code: 'unauthorized',
      status: 401,
      message: 'Incorrect username or password.',
    })
  })

  it('rejects a 200 that is not a token pair as malformed_response', async () => {
    server.use(http.post(apiUrl(LOGIN_PATH), () => HttpResponse.json({ token_type: 'bearer' })))

    // Returning `{accessToken: undefined}` here would put `Bearer undefined` on every later
    // request and surface as a mystery 401 three screens away.
    await expect(login({ username: 'ada', password: 'hunter2' })).rejects.toMatchObject({
      code: 'malformed_response',
      status: 200,
    })
  })

  it('reports an unreachable API as network_error rather than hanging', async () => {
    server.use(http.post(apiUrl(LOGIN_PATH), () => HttpResponse.error()))

    await expect(login({ username: 'ada', password: 'hunter2' })).rejects.toMatchObject({
      code: 'network_error',
      status: null,
    })
  })
})

describe('features/auth/api requestRecovery', () => {
  it('posts a JSON body and returns the fixed 202 envelope', async () => {
    let body = null
    server.use(
      http.post(apiUrl(RECOVERY_PATH), async ({ request }) => {
        body = await request.json()
        return HttpResponse.json(
          { status: 'accepted', message: 'If an account matches that username, …' },
          { status: 202 },
        )
      }),
    )

    const result = await requestRecovery({ username: 'ada' })

    expect(body).toEqual({ username: 'ada' })
    // Nothing to branch on, deliberately: the API answers identically whether or not the
    // account exists, so the caller cannot turn recovery into an enumeration oracle.
    expect(result.status).toBe('accepted')
  })
})
