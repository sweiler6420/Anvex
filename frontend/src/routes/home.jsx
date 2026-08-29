import { createRoute } from '@tanstack/react-router'

import RoutePlaceholder from './RoutePlaceholder'
import { rootRoute } from './root'
import { HOME_ROUTE } from './paths'

/**
 * `/` — the marketing home page. Public.
 *
 * An index route (`path: '/'` under the root), so it matches the bare origin and nothing
 * else. **ANV-33 replaces `<RoutePlaceholder>` with the ported `Home` feature component**;
 * nothing else in this file changes.
 */
export const homeRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: HOME_ROUTE,
  component: () => <RoutePlaceholder title="Home" ticket="ANV-33" />,
})
