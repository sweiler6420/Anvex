import { createRoute } from '@tanstack/react-router'

import SignUpPage from '@features/auth/components/SignUpPage'

import { rootRoute } from './root'
import { SIGNUP_ROUTE } from './paths'

/**
 * `/signup` — public, and deliberately **unguarded**.
 *
 * `/login` bounces a user who already has a session, because arriving there with one means
 * they are trying to do something they have already done. This route does not: an
 * authenticated user reaching `/signup` may well be creating a *second* account, which is a
 * thing the API allows and no guard here should decide against.
 *
 * The page is `SignUpPage` (ANV-30). It keeps the placeholder's `route-sign-up` testid, so
 * the ANV-27/28 routing tests need no edit.
 */
export const signupRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: SIGNUP_ROUTE,
  component: () => <SignUpPage />,
})
