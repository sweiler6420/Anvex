import { createRoute } from '@tanstack/react-router'

import RoutePlaceholder from './RoutePlaceholder'
import { requireAuth } from './guards'
import { rootRoute } from './root'
import { RESEARCH_ROUTE } from './paths'

/**
 * `/research` — **protected**. **ANV-34/35** replace the placeholder with the research
 * dashboard.
 *
 * The whole guard is the one `beforeLoad` line: no wrapper element, no `<Outlet/>` gate,
 * and no branch inside the component. See `guards.js` for why that is not the same thing
 * as the old `RequireAuth`.
 */
export const researchRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: RESEARCH_ROUTE,
  beforeLoad: requireAuth,
  component: () => <RoutePlaceholder title="Research" ticket="ANV-34" />,
})
