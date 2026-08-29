import { homeRoute } from './home'
import { loginRoute } from './login'
import { portfolioRoute } from './portfolio'
import { recoveryRoute } from './recovery'
import { researchRoute } from './research'
import { rootRoute } from './root'
import { signupRoute } from './signup'
import { unauthorizedRoute } from './unauthorized'

/**
 * The route tree (ANV-27).
 *
 * Code-based rather than file-based routing, deliberately. TanStack's file-based mode
 * generates `routeTree.gen.ts` from a Vite plugin, which would mean a generated file in
 * the source tree, a build step before `vitest` can resolve a route, and a second place
 * (the plugin config) that decides what a route is. Eight routes do not need it, and the
 * explicit tree is what lets a test import `routeTree` and build a router against a memory
 * history with no codegen at all.
 *
 * Public: `/`, `/login`, `/signup`, `/recovery`, `/unauthorized`.
 * Protected (`beforeLoad: requireAuth`): `/research`, `/portfolio`.
 * Anything else falls through to the root route's `notFoundComponent`.
 *
 * The tree is flat under one root because ANV-28's `Layout` wraps *every* route including
 * the public ones — exactly as the old app's `<Route path="/" element={<Layout/>}>` did.
 * A pathless "protected" parent route holding the guard once was considered and rejected:
 * with two protected routes it hides the guard from the file that is guarded, and the
 * grouping it saves is one line per route.
 */
export const routeTree = rootRoute.addChildren([
  homeRoute,
  loginRoute,
  signupRoute,
  recoveryRoute,
  unauthorizedRoute,
  researchRoute,
  portfolioRoute,
])
