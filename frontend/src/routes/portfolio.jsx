import { createRoute } from '@tanstack/react-router'

import RoutePlaceholder from './RoutePlaceholder'
import { requireAuth } from './guards'
import { rootRoute } from './root'
import { PORTFOLIO_ROUTE } from './paths'

/** `/portfolio` — **protected**. **ANV-36** replaces the placeholder. */
export const portfolioRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: PORTFOLIO_ROUTE,
  beforeLoad: requireAuth,
  component: () => <RoutePlaceholder title="Portfolio" ticket="ANV-36" />,
})
