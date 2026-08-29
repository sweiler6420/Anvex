import { createRoute } from '@tanstack/react-router'

import RoutePlaceholder from './RoutePlaceholder'
import { rootRoute } from './root'
import { UNAUTHORIZED_ROUTE } from './paths'

/**
 * `/unauthorized` — public, and public is the point: it is where a *signed-in* user lands
 * when they are refused, so gating it behind a session would be circular.
 *
 * Nothing routes here yet. The old `RequireAuth` sent users here on an
 * `allowedPermission` mismatch, and the Anvex API has no roles (CLAUDE.md §4 has 401 and
 * 403 but no permission claim), so the destination exists and the trigger does not. When a
 * 403 needs a screen, this is it.
 */
export const unauthorizedRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: UNAUTHORIZED_ROUTE,
  component: () => <RoutePlaceholder title="Unauthorized" ticket="ANV-32" />,
})
