import { describe, expect, it } from 'vitest'

import { API_BASE_URL, apiUrl } from './env'

describe('lib/env', () => {
  it('exposes VITE_API_BASE_URL from the root .env with no trailing slash', () => {
    // envDir in vite.config.js points Vite at the repo root, so this is the *same* file
    // the API and the worker read. A frontend/.env would be a second source of truth.
    expect(API_BASE_URL).toBe(String(import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, ''))
    expect(API_BASE_URL.endsWith('/')).toBe(false)
  })

  it('joins an absolute path onto the base', () => {
    expect(apiUrl('/v1/stocks')).toBe(`${API_BASE_URL}/v1/stocks`)
  })

  it('refuses a relative path rather than producing a plausible wrong URL', () => {
    expect(() => apiUrl('v1/stocks')).toThrow(/absolute path/)
  })
})
