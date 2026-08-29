import { createRoute } from '@tanstack/react-router'

import RoutePlaceholder from './RoutePlaceholder'
import { rootRoute } from './root'
import { SIGNUP_ROUTE } from './paths'

/** `/signup` — public. **ANV-30** replaces the placeholder with the sign-up form. */
export const signupRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: SIGNUP_ROUTE,
  component: () => <RoutePlaceholder title="Sign up" ticket="ANV-30" />,
})
