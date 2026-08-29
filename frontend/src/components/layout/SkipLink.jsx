/**
 * "Skip to main content" (ANV-32) — the decision ANV-28 deferred.
 *
 * ---------------------------------------------------------------------------------------
 * ## Why it is a button and not `<a href="#main-content">`
 *
 * ANV-28 left `<main id="main-content">` in place and no skip link, for a specific reason:
 * the conventional implementation is a fragment anchor, and a fragment is part of the
 * *location*. `Header` decides which nav item is current from the router's location, hash
 * included (`NAV_ACTIVE_OPTIONS = {exact: true, includeHash: true}`), because four of the
 * five marketing items differ from one another **only** by fragment. So a skip link that
 * navigated to `/#main-content` would match no nav item at all: pressing the first control
 * on the page would un-highlight the nav, and leave a fragment in the URL that means
 * nothing to anyone the user later sends it to.
 *
 * Three ways out were available, and the other two are worse:
 *
 *  - **Change what "current" is computed from** (scroll position rather than location).
 *    That is the scroll-spy question, and ANV-32 answered it no — see `navItems.js`. It
 *    also solves the smaller problem by taking on a much larger one.
 *  - **No skip link.** Defensible while ANV-28 shipped placeholder pages; not once the
 *    home page is six sections long. The header is up to nine tab stops, and every one of
 *    them is between a keyboard user and the page, on **every** route.
 *  - **Skip without navigating.** WCAG 2.4.1 asks for *a mechanism to bypass blocks*, not
 *    for an anchor. A `<button>` that moves focus to `<main>` is that mechanism, and it is
 *    the ordinary answer in a single-page app for exactly this reason. It is also the more
 *    correct one on its merits: the well-known failure mode of a fragment skip link is
 *    that some browsers scroll without moving focus, so the next Tab continues from the
 *    top of the document and the link achieved nothing. Doing it in one line of script
 *    makes the focus move the *primary* effect rather than a side effect of it.
 *
 * `<main>` therefore carries `tabIndex={-1}` (see `Layout.jsx`) — programmatically
 * focusable, never in the tab order. `scrollIntoView` is optional-chained because jsdom
 * does not implement it; the focus move is the part that matters and it is what
 * `SkipLink.test.jsx` asserts.
 *
 * It renders **before** `Header`, so it is genuinely the first thing Tab reaches, and it is
 * `sr-only` until focused — the standard visually-hidden-until-focus treatment, so sighted
 * mouse users never see it and keyboard users cannot miss it.
 */

/** The id of the `<main>` in `Layout`. One definition, used by both. */
export const MAIN_CONTENT_ID = 'main-content'

export default function SkipLink() {
  const skipToMain = () => {
    const main = document.getElementById(MAIN_CONTENT_ID)
    if (!main) return
    main.focus()
    main.scrollIntoView?.()
  }

  return (
    <button
      type="button"
      onClick={skipToMain}
      className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-[60] focus:rounded-md focus:border focus:border-neutral-300 focus:bg-white focus:px-3 focus:py-2 focus:font-gothic focus:font-demi focus:text-neutral-900 dark:focus:border-neutral-700 dark:focus:bg-neutral-900 dark:focus:text-neutral-100"
    >
      Skip to main content
    </button>
  )
}
