import { Outlet, createRootRouteWithContext } from '@tanstack/react-router'

import NotFound from './NotFound'

/**
 * The root route (ANV-27) — the one place the router's auth context is declared, and the
 * shell every other route renders inside.
 *
 * `createRootRouteWithContext()` is what makes `context.auth` exist for every descendant's
 * `beforeLoad`. The context itself is supplied at render time by `App.jsx`
 * (`<RouterProvider context={{ auth }} />`), not baked into the router, because it is React
 * state: the router instance is created once and the auth object changes as the session
 * does.
 *
 * The component is a bare `<Outlet />` on purpose. **ANV-28 owns the shell** — `Layout`,
 * `Header`, the dark-mode switcher — and this is the element it wraps the outlet in. There
 * is nothing else to remove first.
 */
export const rootRoute = createRootRouteWithContext()({
  /**
   * The root declares **no** search params, and that is what makes a child's
   * `validateSearch` authoritative.
   *
   * TanStack builds a match's search by merging its parent's into its own, and a route
   * without a `validateSearch` passes the raw query string straight through. So without
   * this line the root would hand every child the unparsed URL search, and `/login`'s
   * sanitiser would clean its own key while `?redirect=//evil.example` survived beside it
   * in `Route.useSearch()` — a validated route whose invalid input is still readable.
   * Returning `{}` here means a param exists only where some route declared it.
   */
  validateSearch: () => ({}),
  component: () => <Outlet />,
  notFoundComponent: NotFound,
})
