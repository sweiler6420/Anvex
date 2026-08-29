import { describe, expect, it } from 'vitest'

import { createAppRouter, signOutNavigation } from '@lib/router'
import { SIGN_OUT_LOGOUT, SIGN_OUT_SESSION_EXPIRED } from '@providers/AuthProvider'

describe('signOutNavigation', () => {
  it('sends an expired session to /login carrying where they were', () => {
    expect(
      signOutNavigation({ reason: SIGN_OUT_SESSION_EXPIRED, currentHref: '/research?tab=news' }),
    ).toEqual({ to: '/login', search: { redirect: '/research?tab=news' }, replace: true })
  })

  it('sends a deliberate logout to a plain /login', () => {
    // No `redirect`. Someone who asked to leave must not be dragged back to the page they
    // walked away from the next time they sign in.
    expect(signOutNavigation({ reason: SIGN_OUT_LOGOUT, currentHref: '/research?tab=news' })).toEqual(
      { to: '/login', search: {}, replace: true },
    )
  })

  it('replaces rather than pushes, whichever reason it was', () => {
    for (const reason of [SIGN_OUT_LOGOUT, SIGN_OUT_SESSION_EXPIRED]) {
      expect(signOutNavigation({ reason, currentHref: '/portfolio' }).replace).toBe(true)
    }
  })
})

describe('createAppRouter', () => {
  it('is a factory — two calls are two routers with independent state', () => {
    const a = createAppRouter()
    const b = createAppRouter()

    expect(a).not.toBe(b)
    expect(a.history).not.toBe(b.history)
  })

  it('seeds an anonymous auth context, so a guard reads a boolean before the first render', () => {
    // `RouterProvider` overwrites this during render. The seed exists so `beforeLoad` never
    // has to null-check `context.auth`, and it is anonymous because that is the safe
    // default for something whose job is refusing.
    expect(createAppRouter().options.context.auth.isAuthenticated).toBe(false)
  })

  it('declares every route in the ticket, and no others', () => {
    const paths = Object.values(createAppRouter().routesByPath)
      .map((route) => route.fullPath)
      .sort()

    expect(paths).toEqual([
      '/',
      '/login',
      '/portfolio',
      '/recovery',
      '/research',
      '/signup',
      '/unauthorized',
    ])
  })

  it('declares no search params at the root, so a child validateSearch is authoritative', () => {
    // Structural on purpose: TanStack merges a parent match's search into its child's, and
    // a route with no `validateSearch` passes the raw query string through — so without
    // this, `/login`'s `Route.useSearch()` would hand ANV-29 the sanitised `redirect`
    // *and* whatever else was in the URL beside it. Nothing consumes a search param yet,
    // so there is no behaviour to assert instead; this at least fails if the line is
    // deleted.
    expect(createAppRouter().routeTree.options.validateSearch({ redirect: '/x', junk: 1 })).toEqual(
      {},
    )
  })

  it('guards exactly the two protected routes in beforeLoad', () => {
    // The property the ticket is really about: guarding lives on the route, so "which
    // routes are protected" is one readable list rather than a wrapper somebody forgot to
    // nest a route inside — which is exactly how the old `RequireAuth` failed silently.
    const guarded = Object.values(createAppRouter().routesByPath)
      .filter((route) => typeof route.options.beforeLoad === 'function')
      .map((route) => route.fullPath)
      .sort()

    expect(guarded).toEqual(['/login', '/portfolio', '/research'])
  })
})
