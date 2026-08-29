import { Link } from '@tanstack/react-router'

import { HOME_ROUTE } from './paths'

/**
 * What an unknown path renders (ANV-27). It is a **page**, not a redirect to `/`.
 *
 * The decision matters more than it looks. Bouncing a typo to the home page makes a broken
 * link indistinguishable from a working one — the address bar quietly changes, the visitor
 * believes they arrived, and nobody ever reports it. Rendering this keeps the wrong URL in
 * the bar (so it can be pasted into a bug report) and leaves the history stack alone (so
 * Back works). The old app had its catch-all commented out entirely, so an unknown path
 * rendered the layout wrapped around nothing.
 *
 * It is deliberately **public**. Guarding it would leak the shape of the app: "sign in
 * first" for a real protected route and "not found" for a typo is an oracle for which
 * paths exist.
 *
 * Lives in its own file because `root.jsx` exports a route object, and a module that
 * exports both a component and something else loses React Fast Refresh (ANV-25's rule,
 * enforced by `react-refresh/only-export-components`).
 */
export default function NotFound() {
  return (
    <section
      className="flex min-h-screen flex-col items-center justify-center gap-3 bg-neutral-50 p-8 text-neutral-900 dark:bg-neutral-950 dark:text-neutral-300"
      data-testid="route-not-found"
    >
      <h1 className="font-gothic text-4xl text-brand-600 dark:text-brand-400">Page not found</h1>
      <p className="font-base text-sm text-neutral-500">
        That address does not match anything in Anvex.
      </p>
      <Link to={HOME_ROUTE} className="font-base text-sm text-brand-600 underline">
        Back to home
      </Link>
    </section>
  )
}
