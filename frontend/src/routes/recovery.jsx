import { createRoute } from '@tanstack/react-router'

import RecoveryPage from '@features/auth/components/RecoveryPage'

import { rootRoute } from './root'
import { RECOVERY_ROUTE } from './paths'

/**
 * `/recovery` — public. The page is `features/auth/components/RecoveryPage.jsx` (ANV-31);
 * it calls `requestRecovery` from `features/auth/api.js`, which ANV-26 wrote and tested.
 */
export const recoveryRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: RECOVERY_ROUTE,
  component: () => <RecoveryPage />,
})
