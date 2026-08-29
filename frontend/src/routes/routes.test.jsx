import { RouterProvider, createMemoryHistory } from '@tanstack/react-router'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { createAppRouter } from '@lib/router'

/**
 * The route tree and its guards (ANV-27).
 *
 * These mount the **real** router against a memory history, with the auth context supplied
 * directly — no `AuthProvider`, no storage, no transport. That is the point of ANV-26
 * making the router's dependency a plain `{isAuthenticated}` object: the guard is testable
 * as the routing decision it is. `App.test.jsx` covers the wiring to the real store.
 *
 * A fresh router per test (`createAppRouter` is a factory, not a singleton) so a test that
 * navigates cannot decide where the next one starts.
 */
function renderAt(path, { isAuthenticated = false } = {}) {
  const router = createAppRouter({ history: createMemoryHistory({ initialEntries: [path] }) })
  const auth = { isAuthenticated, login: vi.fn(), logout: vi.fn(), restore: vi.fn() }

  render(<RouterProvider router={router} context={{ auth }} />)

  return { router, location: () => router.state.location }
}

describe('protected routes', () => {
  it.each(['/research', '/portfolio'])(
    'sends an anonymous visitor from %s to /login',
    async (path) => {
      const { location } = renderAt(path)

      expect(await screen.findByTestId('route-login')).toBeInTheDocument()
      expect(location().pathname).toBe('/login')
    },
  )

  it('carries where they were going in the redirect search param', async () => {
    const { location } = renderAt('/research')

    await screen.findByTestId('route-login')
    // The param, not just "some search" — this is the assertion that fails if the guard
    // redirects but forgets what it interrupted.
    expect(location().search).toEqual({ redirect: '/research' })
    expect(location().searchStr).toBe('?redirect=%2Fresearch')
  })

  it('preserves a deep link whole, query string and hash included', async () => {
    const { location } = renderAt('/portfolio?range=1y#holdings')

    await screen.findByTestId('route-login')
    expect(location().search).toEqual({ redirect: '/portfolio?range=1y#holdings' })
  })

  it('replaces rather than pushes, so Back does not re-enter the guard', async () => {
    const { router } = renderAt('/research')

    await screen.findByTestId('route-login')
    // The memory history started with one entry; a push would make it two.
    expect(router.history.length).toBe(1)
  })

  it.each([
    ['/research', 'route-research'],
    ['/portfolio', 'route-portfolio'],
  ])('lets an authenticated user into %s', async (path, testId) => {
    const { location } = renderAt(path, { isAuthenticated: true })

    expect(await screen.findByTestId(testId)).toBeInTheDocument()
    expect(location().pathname).toBe(path)
  })
})

describe('/login', () => {
  it('renders for an anonymous visitor', async () => {
    const { location } = renderAt('/login')

    expect(await screen.findByTestId('route-login')).toBeInTheDocument()
    expect(location().pathname).toBe('/login')
  })

  it('bounces an authenticated user to the default destination', async () => {
    const { location } = renderAt('/login', { isAuthenticated: true })

    expect(await screen.findByTestId('route-research')).toBeInTheDocument()
    expect(location().pathname).toBe('/research')
  })

  it('honours the redirect param instead of the default', async () => {
    // The half of the round trip that a "redirect to /research on success" implementation
    // would pass anyway; the assertion is that they land on /portfolio.
    const { location } = renderAt('/login?redirect=%2Fportfolio', { isAuthenticated: true })

    expect(await screen.findByTestId('route-portfolio')).toBeInTheDocument()
    expect(location().pathname).toBe('/portfolio')
  })

  it('rebuilds the query string and hash of the destination it returns to', async () => {
    const { location } = renderAt('/login?redirect=%2Fportfolio%3Frange%3D1y%23holdings', {
      isAuthenticated: true,
    })

    expect(await screen.findByTestId('route-portfolio')).toBeInTheDocument()
    expect(location().pathname).toBe('/portfolio')
    expect(location().search).toEqual({ range: '1y' })
    expect(location().hash).toBe('holdings')
  })

  it('refuses an off-site redirect and falls back to the default destination', async () => {
    const { location } = renderAt('/login?redirect=https%3A%2F%2Fevil.example%2Fharvest', {
      isAuthenticated: true,
    })

    expect(await screen.findByTestId('route-research')).toBeInTheDocument()
    expect(location().pathname).toBe('/research')
    expect(location().href).not.toContain('evil.example')
  })

})

describe('public routes', () => {
  it.each([
    ['/', 'route-home'],
    ['/signup', 'route-sign-up'],
    ['/recovery', 'route-recovery'],
    ['/unauthorized', 'route-unauthorized'],
  ])('%s stays reachable while anonymous', async (path, testId) => {
    const { location } = renderAt(path)

    expect(await screen.findByTestId(testId)).toBeInTheDocument()
    expect(location().pathname).toBe(path)
  })

  it.each([
    ['/', 'route-home'],
    ['/signup', 'route-sign-up'],
    ['/recovery', 'route-recovery'],
    ['/unauthorized', 'route-unauthorized'],
  ])('%s stays reachable while authenticated too', async (path, testId) => {
    // Only /login bounces. A signed-in user reading the home page is not a mistake, and
    // /unauthorized is where a *signed-in* user is refused — guarding it would be circular.
    const { location } = renderAt(path, { isAuthenticated: true })

    expect(await screen.findByTestId(testId)).toBeInTheDocument()
    expect(location().pathname).toBe(path)
  })
})

describe('an unknown path', () => {
  it('renders the not-found page and keeps the URL', async () => {
    const { location } = renderAt('/reserch')

    expect(await screen.findByTestId('route-not-found')).toBeInTheDocument()
    // Deliberately *not* a redirect to `/`: a silent bounce makes a broken link
    // indistinguishable from a working one and loses the address that was wrong.
    expect(location().pathname).toBe('/reserch')
  })

  it('is public, so it cannot be used to probe which paths are protected', async () => {
    // An anonymous visitor gets "not found" for a typo and "sign in" for a real protected
    // route only because the real one exists — the 404 itself never asks about the session.
    const { location } = renderAt('/portfolioo')

    expect(await screen.findByTestId('route-not-found')).toBeInTheDocument()
    expect(location().pathname).toBe('/portfolioo')
  })
})
