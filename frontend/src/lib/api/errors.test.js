import { describe, expect, it } from 'vitest'

import {
  ApiError,
  AUTH_ERROR_CODES,
  CLIENT_ERROR_CODES,
  isApiError,
  isAuthError,
  isNetworkError,
  toApiError,
} from './errors'

/**
 * `errors.js` is pure, so these are plain unit tests — the *behaviour* of the mapping
 * against real axios-shaped inputs is covered end-to-end through MSW in `client.test.js`.
 * Both matter: this file pins the shape, that one proves the shape is what actually
 * arrives.
 */

describe('ApiError', () => {
  it('always has all five fields, with details {} and never null', () => {
    const err = new ApiError({ code: 'not_found' })

    expect(err).toBeInstanceOf(Error)
    expect(err.name).toBe('ApiError')
    expect(err.code).toBe('not_found')
    expect(err.details).toEqual({})
    expect(err.requestId).toBeNull()
    expect(err.status).toBeNull()
    // Mirrors the backend's rule: a consumer indexes `details` unconditionally.
    expect(err.details.anything).toBeUndefined()
  })

  it('reports a transport failure by the absence of a status, not by a missing field', () => {
    expect(new ApiError({ code: CLIENT_ERROR_CODES.NETWORK }).isTransportFailure).toBe(true)
    expect(new ApiError({ code: 'not_found', status: 404 }).isTransportFailure).toBe(false)
  })

  it('supplies a sentence for every client-side code', () => {
    for (const code of Object.values(CLIENT_ERROR_CODES)) {
      expect(new ApiError({ code }).message.length).toBeGreaterThan(0)
    }
  })
})

describe('the client-side code namespace', () => {
  it('does not collide with any code the backend can emit', () => {
    // `app/domain/errors.py` plus the token codes plus the middleware's HTTP fallback.
    const backendCodes = [
      'internal_error',
      'not_found',
      'conflict',
      'validation_error',
      'unauthorized',
      'forbidden',
      'external_service_error',
      'invalid_token',
      'token_expired',
      'wrong_token_type',
      'http_error',
    ]
    for (const code of Object.values(CLIENT_ERROR_CODES)) {
      expect(backendCodes).not.toContain(code)
    }
  })
})

describe('toApiError', () => {
  it('is idempotent', () => {
    const original = new ApiError({ code: 'conflict', status: 409 })
    expect(toApiError(original)).toBe(original)
  })

  it('reads the envelope off a response', () => {
    const err = toApiError({
      response: {
        status: 404,
        data: {
          error: {
            code: 'not_found',
            message: "stock 'ZZZZ' was not found.",
            details: { resource: 'stock', identifier: 'ZZZZ' },
            request_id: 'abc123',
          },
        },
      },
    })

    expect(err.code).toBe('not_found')
    expect(err.status).toBe(404)
    expect(err.details).toEqual({ resource: 'stock', identifier: 'ZZZZ' })
    expect(err.requestId).toBe('abc123')
  })

  it('refuses to guess a code for a body that is not the envelope', () => {
    // A proxy's HTML 502, or the old API's `{"detail": ...}`. Claiming a code here would
    // be a lie a caller would then branch on.
    for (const data of ['<html>502</html>', { detail: 'Not authenticated' }, null, { error: {} }]) {
      const err = toApiError({ response: { status: 502, data } })
      expect(err.code).toBe(CLIENT_ERROR_CODES.MALFORMED)
      expect(err.status).toBe(502)
    }
  })

  it('turns a response-less axios error into network_error with a null status', () => {
    const err = toApiError({ request: {}, code: 'ERR_NETWORK', message: 'Network Error' })
    expect(err.code).toBe(CLIENT_ERROR_CODES.NETWORK)
    expect(err.status).toBeNull()
    expect(isNetworkError(err)).toBe(true)
  })

  it('distinguishes a timeout from a dead host', () => {
    const err = toApiError({ request: {}, code: 'ECONNABORTED', message: 'timeout of 20000ms' })
    expect(err.code).toBe(CLIENT_ERROR_CODES.TIMEOUT)
    expect(isNetworkError(err)).toBe(true)
  })

  it('does not report a cancellation as a network failure', () => {
    const err = toApiError({ code: 'ERR_CANCELED', name: 'CanceledError' })
    expect(err.code).toBe(CLIENT_ERROR_CODES.CANCELLED)
    expect(isNetworkError(err)).toBe(false)
  })

  it('falls back to unknown_error rather than pretending it was the network', () => {
    const err = toApiError(new TypeError('config.headers is not a function'))
    expect(err.code).toBe(CLIENT_ERROR_CODES.UNKNOWN)
    expect(err.status).toBeNull()
  })
})

describe('the predicates', () => {
  it('isApiError only accepts our own type', () => {
    expect(isApiError(new ApiError({ code: 'x' }))).toBe(true)
    expect(isApiError(new Error('x'))).toBe(false)
    expect(isApiError({ code: 'not_found' })).toBe(false)
  })

  it('isAuthError covers exactly the four 401 codes', () => {
    for (const code of Object.values(AUTH_ERROR_CODES)) {
      expect(isAuthError(new ApiError({ code, status: 401 }))).toBe(true)
    }
    expect(isAuthError(new ApiError({ code: 'forbidden', status: 403 }))).toBe(false)
  })
})
