import { Link } from '@tanstack/react-router'

import { HOME_ROUTE, LOGIN_ROUTE } from '@routes/paths'

/**
 * `/unauthorized` (ANV-31), ported from
 * `AverageInvestorWeb/src/components/authenticate/Unauthorized.jsx` (19 lines).
 *
 * ---------------------------------------------------------------------------------------
 * ## What this page is actually for, and what it must not pretend
 *
 * The old page said "You do not have access to the requested page", and it was reached by
 * `RequireAuth`'s `allowedPermission` check — a per-route permission the old app compared
 * against a claim in the token. **Anvex has no analogue for any part of that.** Three facts,
 * each checkable rather than remembered:
 *
 *  - There are no roles. No model, schema or token claim carries one — CLAUDE.md §4's JWT
 *    claims are `sub`, `exp`, `iat` and `type`, and `type` distinguishes an access token
 *    from a refresh token, not a person from a person.
 *  - No service raises `ForbiddenError`. The 403 mapping exists in
 *    `app/middleware/errors.py` and nothing in `app/services/` reaches it, because §4 makes
 *    a refusal that would confirm a resource exists a **404**: "this belongs to somebody
 *    else" is answered byte-identically to "there is no such thing".
 *  - Nothing in the frontend navigates here. ANV-27's `requireAuth` sends an *anonymous*
 *    visitor to `/login?redirect=…`, which is a different screen for a different reason,
 *    and no `err.code === 'forbidden'` branch exists anywhere in `src/`.
 *
 * So the honest description of this route is: **a destination that exists and that nothing
 * currently reaches.** The page says so. Writing "you do not have permission to view this
 * page" would describe a permission system this application does not have, and would send
 * a user looking for a settings screen, an administrator or an upgrade — none of which
 * exist. Telling somebody the truth about why they are stuck is the whole job of a page
 * like this one, and the truth here is that they are almost certainly not stuck at all.
 *
 * It stays a real route rather than being deleted, and it stays **public**: it is where a
 * *signed-in* user would be refused, so gating it behind a session would be circular, and
 * a 403 the API is not yet in a position to send is exactly the kind of thing that gets
 * added later. When one does, this is the screen, and the copy changes with it.
 *
 * ## The "Go Back" button is not ported
 *
 * The old page's only control was `<button onClick={() => navigate(-1)}>Go Back</button>`.
 * Two things are wrong with it and neither is cosmetic: back is *the page that just refused
 * you*, so the button's happy path is a loop; and on a fresh tab opened directly on this
 * URL there is no previous entry, so it silently does nothing at all — a control that
 * looks like the way out and is not. Every browser already has a Back button for the case
 * where Back is what you wanted. What this page owes the user instead is somewhere to
 * actually go, so it offers the two destinations that are always valid: the home page, and
 * the login page (signing in as somebody else being the one action that can change an
 * authorization outcome).
 *
 * Both are TanStack `<Link>`s — ANV-28's rule, and not a style preference: an in-app
 * destination reached through a bare `<a href>` is a full document navigation, which
 * reloads the bundle and discards the in-memory access token (ANV-26), signing out the
 * user this page is talking to.
 *
 * ## Why it lives in `features/auth/`
 *
 * ANV-29's convention (`features/<area>/components/<X>Page.jsx`), and authorization is the
 * auth area — the old app filed it under `components/authenticate/` too. It is *not* in
 * `routes/` beside `NotFound.jsx`, its nearest relative in shape: `routes/unauthorized.jsx`
 * already exists as the route module, and `routes/Unauthorized.jsx` differs from it only by
 * case, which is the same file on Windows and macOS. `NotFound.jsx` gets away with it only
 * because the 404 has no route module of its own.
 */
export default function UnauthorizedPage() {
  return (
    // `data-testid` matches the `RoutePlaceholder` this replaces, so ANV-27's routing tests
    // keep asserting "/unauthorized resolved" against the real page.
    <section
      data-testid="route-unauthorized"
      className="mt-6 flex flex-col items-center px-6 text-center lg:mt-20"
    >
      <h1 className="mb-5 font-gothic text-4xl font-xl">Unauthorized</h1>

      <div className="max-w-xl rounded-xl border border-neutral-200 p-6 text-neutral-900 dark:border-neutral-700 dark:text-neutral-200">
        <p className="font-gothic font-medium">Something refused that request.</p>

        <p className="mt-4 font-base text-sm text-neutral-600 dark:text-neutral-400">
          Anvex has no roles, groups or permission levels, so nothing here is withheld
          because of who you are — there is no access to be granted and no one to ask. If
          you are signed out, signing in is the only thing that changes an authorization
          failure. If you are signed in and landed here from inside Anvex, that is a defect
          worth reporting rather than a setting to change.
        </p>

        <div className="mt-6 flex flex-wrap justify-center gap-4 font-gothic text-sm font-medium">
          <Link to={HOME_ROUTE} className="text-brand-600 hover:underline dark:text-brand-400">
            Back to home
          </Link>
          <Link to={LOGIN_ROUTE} className="text-brand-600 hover:underline dark:text-brand-400">
            Go to the login page
          </Link>
        </div>
      </div>
    </section>
  )
}
