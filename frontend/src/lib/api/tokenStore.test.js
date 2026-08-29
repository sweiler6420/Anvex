import { afterEach, describe, expect, it, vi } from 'vitest'

import { getTokenStore, installTokenStore, resetTokenStore } from './tokenStore'

const validStore = () => ({
  getAccessToken: vi.fn(() => null),
  getRefreshToken: vi.fn(() => null),
  setTokens: vi.fn(),
  clear: vi.fn(),
})

afterEach(() => {
  resetTokenStore()
})

describe('the token seam', () => {
  it('behaves as a signed-out client until something is installed', () => {
    const store = getTokenStore()
    expect(store.getAccessToken()).toBeNull()
    expect(store.getRefreshToken()).toBeNull()
    // The no-op default must be safe to call, not merely present: `client.js` calls
    // `clear()` on a terminal 401 whether or not a provider has mounted yet.
    expect(() => store.clear()).not.toThrow()
    expect(() => store.setTokens({ accessToken: 'a', refreshToken: 'r' })).not.toThrow()
  })

  it('installs, and hands back an uninstall', () => {
    const store = validStore()
    const uninstall = installTokenStore(store)

    expect(getTokenStore()).toBe(store)
    uninstall()
    expect(getTokenStore()).not.toBe(store)
    expect(getTokenStore().getAccessToken()).toBeNull()
  })

  it('rejects an incomplete store at install time, not mid-401', () => {
    // The failure this prevents: a store missing `setTokens` refreshes successfully and
    // then throws while saving the pair, which reads to the user as a random logout.
    for (const missing of ['getAccessToken', 'getRefreshToken', 'setTokens', 'clear']) {
      const store = validStore()
      delete store[missing]
      expect(() => installTokenStore(store)).toThrow(TypeError)
      expect(() => installTokenStore(store)).toThrow(missing)
    }
    expect(() => installTokenStore(null)).toThrow(TypeError)
  })

  it('does not undo a newer install when an older uninstall runs late', () => {
    const first = validStore()
    const second = validStore()
    const undoFirst = installTokenStore(first)
    installTokenStore(second)

    undoFirst()

    expect(getTokenStore()).toBe(second)
  })
})
