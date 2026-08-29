import { createRoute } from '@tanstack/react-router'

import UnauthorizedPage from '@features/auth/components/UnauthorizedPage'

import { rootRoute } from './root'
import { UNAUTHORIZED_ROUTE } from './paths'

/**
 * `/unauthorized` — public, and public is the point: it is where a *signed-in* user lands
 * when they are refused, so gating it behind a session would be circular.
 *
 * **Nothing routes here yet, and the page says so** (ANV-31). The old `RequireAuth` sent
 * users here on an `allowedPermission` mismatch; the Anvex API has no roles, no service
 * raises `ForbiddenError`, and CLAUDE.md §4 makes a refusal that would confirm a resource
 * exists a 404 rather than a 403. So the destination exists and the trigger does not. When
 * a 403 needs a screen, this is it.
 */
export const unauthorizedRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: UNAUTHORIZED_ROUTE,
  component: () => <UnauthorizedPage />,
})
