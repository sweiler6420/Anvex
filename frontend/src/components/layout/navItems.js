import {
  HOME_ROUTE,
  LOGIN_ROUTE,
  PORTFOLIO_ROUTE,
  RESEARCH_ROUTE,
  SIGNUP_ROUTE,
} from '@routes/paths'

/**
 * What the header links to (ANV-28), and the one place it is decided.
 *
 * A plain `.js` module beside `Header.jsx` rather than an array inside it, for the reason
 * ANV-25 gives for `XContext.js`: a `.jsx` file exporting both a component and a
 * non-literal constant loses React Fast Refresh. It also means the desktop nav and the
 * mobile drawer render **the same list** — the old header had two copies of the `.map()`
 * and would have grown a third when someone added an item to one of them.
 *
 * **Every destination is a `@routes/paths` import.** Nothing here is a URL string; a route
 * rename is still one edit in `paths.js`. The marketing items add a `hash`, which is a
 * fragment inside the home *route* rather than a route of its own — ANV-32 ports the
 * `Hero`/`Features`/`Workflow`/`Pricing`/`Contact` sections that carry those ids.
 *
 * The old header wrote the anonymous items as `<a href='/#features'>` and the auth actions
 * as `<a href='/login'>`. Both are ported as TanStack `<Link>`s and that is a **fix, not a
 * translation**: a bare `<a>` to an in-app URL is a full document navigation, so clicking
 * "Login In" reloaded the bundle and — since ANV-26 keeps the access token in memory —
 * threw away the session of anyone who happened to have one.
 */

/** @typedef {{key: string, label: string, to: string, hash?: string}} NavItem */

/**
 * Signed out: the marketing page's sections. `Home` is the same route with no fragment,
 * which is what makes it distinguishable from the others (see `Header.jsx`'s active rule).
 *
 * @type {readonly NavItem[]}
 */
export const ANONYMOUS_NAV_ITEMS = Object.freeze([
  { key: 'home', label: 'Home', to: HOME_ROUTE },
  { key: 'features', label: 'Features', to: HOME_ROUTE, hash: 'features' },
  { key: 'workflow', label: 'Workflow', to: HOME_ROUTE, hash: 'workflow' },
  { key: 'pricing', label: 'Pricing', to: HOME_ROUTE, hash: 'pricing' },
  { key: 'contact', label: 'Contact Us', to: HOME_ROUTE, hash: 'contact' },
])

/**
 * Signed in: the app itself. Both are protected routes, so this list is only ever rendered
 * for a session — but `isAuthenticated` is **provisional** (ANV-26), so rendering the link
 * is not a claim that the route will admit them. `requireAuth` is still the authority, and
 * a user whose refresh token the server has already killed follows one of these and lands
 * on `/login` with a redirect param. The nav is a menu, not a permission check.
 *
 * @type {readonly NavItem[]}
 */
export const AUTHENTICATED_NAV_ITEMS = Object.freeze([
  { key: 'research', label: 'Research', to: RESEARCH_ROUTE },
  { key: 'portfolio', label: 'Portfolio', to: PORTFOLIO_ROUTE },
])

/** The two calls to action shown to a signed-out visitor. */
export const LOGIN_ITEM = Object.freeze({ key: 'login', label: 'Log In', to: LOGIN_ROUTE })
export const SIGNUP_ITEM = Object.freeze({
  key: 'signup',
  label: 'Create an Account',
  to: SIGNUP_ROUTE,
})

/** Which list a visitor sees. */
export function navItemsFor(isAuthenticated) {
  return isAuthenticated ? AUTHENTICATED_NAV_ITEMS : ANONYMOUS_NAV_ITEMS
}

/**
 * How TanStack should decide which item is current.
 *
 * `Link` computes "am I active" itself and writes both `aria-current="page"` and the
 * `activeProps` className from it, so this is where the rule is set rather than in a
 * competing prop on the element. **Both of TanStack's defaults are wrong for this nav:**
 *
 *  - `exact: false` treats a link's path as a *prefix*, so `HOME_ROUTE` — which is `/` —
 *    matches every route in the app and "Home" would stay underlined on `/research`.
 *  - `includeHash: false` ignores the fragment, and four of the five anonymous items differ
 *    from each other *only* by fragment, so the whole marketing nav would light up at once
 *    on the home page.
 *
 * `includeSearch` keeps its default (`true`); the root declares `validateSearch: () => ({})`
 * (ANV-27), so a nav route's search is `{}` and comparing it costs nothing.
 *
 * **This is deliberately not the old header's rule.** That one ran an `IntersectionObserver`
 * plus a `scroll` listener over the home page's sections and underlined whichever was most
 * visible. Not ported: those sections arrive with ANV-32, and jsdom has neither layout nor
 * `IntersectionObserver`, so it would be ~80 lines of unverifiable code observing elements
 * that are not there. Scroll-spying belongs with the page being spied on.
 *
 * **ANV-32 looked again, with the sections finally on the page, and still declines it.**
 * The verdict is not "it is hard to test" — it is that scroll-spy would be a *second*
 * authority on a question this file already answers, and the two would disagree:
 *
 *  - `Link` computes "am I current" itself and writes `aria-current="page"` from it, so
 *    overriding the answer from outside means passing a competing `activeProps` and
 *    stripping `aria-current` back off — two rules for one attribute, which is the drift
 *    every convention in CLAUDE.md §5 exists to prevent.
 *  - The nav would then contradict the address bar. `/#pricing` is a real, shareable,
 *    Back-button-able statement of where the reader is; scroll-spy would move the underline
 *    off "Pricing" while the URL still said `#pricing`.
 *  - The header renders on **every** route, and only one route has anything to spy on. The
 *    observer would be constructed, and its "no section is visible" branch exercised, on
 *    `/login`, `/research` and the 404.
 *  - And it remains untestable *in kind*, not merely in this environment. jsdom has no
 *    layout, so every section's bounding box is 0×0 and identical; a polyfilled
 *    `IntersectionObserver` cannot produce a meaningful answer from it, and the only test
 *    left to write is one that hand-fires the callback we wrote and asserts our own
 *    `setState` ran. That is a test of the mock.
 *
 * **What the reader loses, stated rather than glossed:** clicking a nav item still
 * highlights it (the hash lands in the location), but *free-scrolling* past a section does
 * not move the highlight. Someone who reads the page top to bottom without touching the nav
 * sees "Home" underlined throughout. That is the whole cost, and it is smaller than one
 * `aria-current` attribute with two authors.
 */
export const NAV_ACTIVE_OPTIONS = Object.freeze({ exact: true, includeHash: true })

/**
 * The same rule as a pure function, so it can be stated without a router.
 *
 * `Header` does not call it — `NAV_ACTIVE_OPTIONS` is what the rendered markup obeys, and
 * two implementations of "which one is current" is the drift this repo keeps designing out.
 * It exists because the *reason* the options are set that way is a property of this item
 * list (five entries sharing one route), and a two-line function makes it assertable
 * directly rather than only through a rendered `aria-current`.
 *
 * Both sides compare "no fragment" as `''`. TanStack stores `location.hash` without the
 * leading `#` — see `routes/guards.js`, which slices it off before handing one back — so an
 * item's `hash` is written the same way.
 */
export function isNavItemActive(item, { pathname, hash }) {
  return item.to === pathname && (item.hash ?? '') === (hash ?? '')
}
