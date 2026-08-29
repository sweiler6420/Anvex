import { describe, expect, it } from 'vitest'

import { sanitiseRedirect, validateRedirectSearch } from './guards'

/**
 * The `redirect` search param arrives from the URL bar, so these are the tests that keep
 * "return to where you came from" from also meaning "go wherever a link says".
 */
describe('sanitiseRedirect', () => {
  it.each([
    ['/research', '/research'],
    ['/research?tab=news', '/research?tab=news'],
    ['/research?q=a%20b#top', '/research?q=a%20b#top'],
    ['/portfolio/', '/portfolio/'],
  ])('keeps the same-site path %s', (input, expected) => {
    expect(sanitiseRedirect(input)).toBe(expected)
  })

  it.each([
    ['an absolute http URL', 'http://evil.example/harvest'],
    ['an absolute https URL', 'https://evil.example/harvest'],
    ['a protocol-relative URL', '//evil.example/harvest'],
    ['a backslash-escaped protocol-relative URL', '/\\evil.example/harvest'],
    ['a javascript: URL', 'javascript:alert(1)'],
    ['a data: URL', 'data:text/html,<script>alert(1)</script>'],
    ['a bare word', 'research'],
    ['the empty string', ''],
  ])('refuses %s', (_label, input) => {
    expect(sanitiseRedirect(input)).toBeUndefined()
  })

  it.each([undefined, null, 42, {}, ['/research']])('refuses the non-string %s', (input) => {
    expect(sanitiseRedirect(input)).toBeUndefined()
  })

  it('refuses the login route itself, so the bounce cannot loop', () => {
    expect(sanitiseRedirect('/login')).toBeUndefined()
    expect(sanitiseRedirect('/login?redirect=%2Flogin')).toBeUndefined()
  })
})

describe('validateRedirectSearch', () => {
  it('passes a same-site path through under the declared key', () => {
    expect(validateRedirectSearch({ redirect: '/portfolio' })).toEqual({ redirect: '/portfolio' })
  })

  it('drops the key entirely when there is nothing safe to keep', () => {
    // Not `{redirect: undefined}` — an absent key is what makes `search.redirect ?? default`
    // read the same way whether the param was missing or refused.
    expect(validateRedirectSearch({ redirect: 'https://evil.example' })).toEqual({})
    expect(validateRedirectSearch({})).toEqual({})
    expect(validateRedirectSearch(undefined)).toEqual({})
  })

  it('drops params the route did not declare', () => {
    expect(validateRedirectSearch({ redirect: '/research', tracking: 'x' })).toEqual({
      redirect: '/research',
    })
  })
})
