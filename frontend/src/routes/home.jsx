import { createRoute } from '@tanstack/react-router'

import HomePage from '@features/home/components/HomePage'

import { rootRoute } from './root'
import { HOME_ROUTE } from './paths'

/**
 * `/` — the marketing home page. Public.
 *
 * An index route (`path: '/'` under the root), so it matches the bare origin and nothing
 * else. ANV-32 replaced the `<RoutePlaceholder>` with the ported `HomePage`; nothing else
 * in this file changed. (The placeholder's comment said ANV-33 — an ANV-27 numbering slip,
 * the same one that put `/unauthorized` in ANV-32's name.)
 */
export const homeRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: HOME_ROUTE,
  component: () => <HomePage />,
})
