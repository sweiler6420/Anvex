import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  AUTH_STORAGE_KEYS,
  REFRESH_TOKEN_KEY,
  REMEMBERED_USERNAME_KEY,
  readRefreshToken,
  readRememberedUsername,
  rememberUsername,
  writeRefreshToken,
} from './authStorage'

/**
 * ANV-26's storage policy, tested against the real `localStorage` jsdom provides — nothing
 * here mocks the storage API, so the assertions are about what a browser would actually
 * hold.
 */

beforeEach(() => {
  window.localStorage.clear()
})

afterEach(() => {
  vi.restoreAllMocks()
  window.localStorage.clear()
})

describe('authStorage', () => {
  it('round-trips the refresh token', () => {
    writeRefreshToken('refresh-1')

    expect(readRefreshToken()).toBe('refresh-1')
    expect(window.localStorage.getItem(REFRESH_TOKEN_KEY)).toBe('refresh-1')
  })

  it('removes the key rather than storing "null" when forgetting a token', () => {
    writeRefreshToken('refresh-1')
    writeRefreshToken(null)

    expect(readRefreshToken()).toBeNull()
    // The distinction matters: `String(null)` is a seven-character truthy string, which a
    // naive `if (stored)` would happily send to /v1/auth/refresh.
    expect(window.localStorage.getItem(REFRESH_TOKEN_KEY)).toBeNull()
  })

  it('treats an empty stored value as absent', () => {
    // The old app wrote "" to mean "forget this" — and then read it back with JSON.parse.
    window.localStorage.setItem(REFRESH_TOKEN_KEY, '')

    expect(readRefreshToken()).toBeNull()
  })

  it('remembers a username and nothing else', () => {
    rememberUsername('ada')

    expect(readRememberedUsername()).toBe('ada')
    // The security fix, asserted on the storage contents: whatever "remember me" writes,
    // the set of keys it may write does not include one for a password.
    expect(AUTH_STORAGE_KEYS).toEqual([REFRESH_TOKEN_KEY, REMEMBERED_USERNAME_KEY])
    expect(Object.keys(window.localStorage)).toEqual([REMEMBERED_USERNAME_KEY])
  })

  it('forgets the username on null', () => {
    rememberUsername('ada')
    rememberUsername(null)

    expect(readRememberedUsername()).toBeNull()
    expect(Object.keys(window.localStorage)).toEqual([])
  })

  it('survives storage that refuses to read', () => {
    // Spied on the **prototype**, not on `window.localStorage`. jsdom's storage object is a
    // Proxy whose `defineProperty` trap stores a *value* under the given key, so
    // `vi.spyOn(window.localStorage, 'getItem')` installs an item called "getItem" and
    // leaves the real method in place — a mock that never fires, in a test that then passes
    // for the wrong reason. This one is seeded first so it cannot pass vacuously.
    window.localStorage.setItem(REFRESH_TOKEN_KEY, 'refresh-1')
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('The operation is insecure.', 'SecurityError')
    })

    expect(readRefreshToken()).toBeNull()
    expect(readRememberedUsername()).toBeNull()
  })

  it('survives storage that refuses to write', () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('QuotaExceededError', 'QuotaExceededError')
    })

    // The whole point: it does not throw. Persistence is the only thing lost.
    expect(() => writeRefreshToken('refresh-1')).not.toThrow()
    expect(setItem).toHaveBeenCalledWith(REFRESH_TOKEN_KEY, 'refresh-1')

    setItem.mockRestore()
    expect(readRefreshToken()).toBeNull()
  })

  it('survives storage that refuses to remove', () => {
    const removeItem = vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
      throw new DOMException('The operation is insecure.', 'SecurityError')
    })

    expect(() => writeRefreshToken(null)).not.toThrow()
    expect(removeItem).toHaveBeenCalledWith(REFRESH_TOKEN_KEY)
  })
})
