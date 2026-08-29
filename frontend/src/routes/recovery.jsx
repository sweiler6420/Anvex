import { createRoute } from '@tanstack/react-router'

import RoutePlaceholder from './RoutePlaceholder'
import { rootRoute } from './root'
import { RECOVERY_ROUTE } from './paths'

/**
 * `/recovery` — public. **ANV-31** replaces the placeholder; `requestRecovery` in
 * `features/auth/api.js` is already written and tested for it.
 */
export const recoveryRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: RECOVERY_ROUTE,
  component: () => <RoutePlaceholder title="Recovery" ticket="ANV-31" />,
})
