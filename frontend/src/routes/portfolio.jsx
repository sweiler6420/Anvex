import { createRoute } from '@tanstack/react-router'

import PortfolioPage from '@features/portfolio/components/PortfolioPage'

import { requireAuth } from './guards'
import { rootRoute } from './root'
import { PORTFOLIO_ROUTE } from './paths'

/** `/portfolio` — **protected**. **ANV-36** replaced the placeholder with `PortfolioPage`. */
export const portfolioRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: PORTFOLIO_ROUTE,
  beforeLoad: requireAuth,
  component: () => <PortfolioPage />,
})
