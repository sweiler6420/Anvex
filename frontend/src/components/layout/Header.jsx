import { Link, useRouterState } from '@tanstack/react-router'
import { useEffect, useId, useRef, useState } from 'react'

import Logo from '@assets/Changed_Logo.svg?react'
import { Bars3Icon, XMarkIcon } from '@components/ui/icons'
import { useAuth } from '@hooks/useAuth'
import { HOME_ROUTE } from '@routes/paths'

import DarkModeSwitcher from './DarkModeSwitcher'
import { LOGIN_ITEM, NAV_ACTIVE_OPTIONS, navItemsFor, SIGNUP_ITEM } from './navItems'

/**
 * The application header (ANV-28), ported from
 * `AverageInvestorWeb/src/components/header/Header.jsx` (~277 lines).
 *
 * ---------------------------------------------------------------------------------------
 * ## What was ported, and what was not
 *
 * Ported: the sticky blurred bar, the wordmark/logo swap at `md`, the desktop nav at `lg`,
 * the hamburger below it, the drawer, the auth-aware link set, the theme toggle in both
 * places, and every Tailwind class that produced them.
 *
 * **Not ported: the scroll-spy.** Roughly eighty of the old file's lines were an
 * `IntersectionObserver` plus a `scroll` handler that underlined whichever home-page
 * section was most visible. The sections it watches (`#features`, `#workflow`, `#pricing`,
 * `#contact`) arrive with ANV-32; jsdom has neither layout nor `IntersectionObserver`, so
 * porting it now would mean shipping unverifiable code that observes elements which do not
 * exist. Active state comes from the router's own location instead (`isNavItemActive`), and
 * scroll-spying belongs to the page being spied on. Said plainly rather than quietly
 * dropped.
 *
 * **Not ported: `logout()`'s implementation.** The old one did
 * `setAuth({}); localStorage.removeItem('refresh_token'); navigate('/#')` — the session
 * store, the storage policy and the destination, in a nav component, and then its callers
 * wrote `onClick={() => {logout(); navigate("/#")}}`, navigating twice. Here it is
 * `useAuth().logout()` and nothing else: ANV-26 owns clearing the tokens, and ANV-26's
 * `onSignOut` seam (via `signOutNavigation`) owns where a sign-out lands. A header that
 * navigates as well would race it, and the race is exactly how a plain `/login` becomes a
 * `/login?redirect=…`.
 *
 * ---------------------------------------------------------------------------------------
 * ## The drawer is a disclosure, not a dialog
 *
 * It renders **immediately after its own toggle button, inside the same `<nav>`**, so the
 * natural tab order already runs button → drawer links → page. That is the WAI-ARIA
 * *disclosure navigation* pattern, and the pattern deliberately does **not** trap focus:
 * tabbing past the last link should reach the page, not loop. Three behaviours the old
 * header was missing make it keyboard-operable, and each has a test that fails without it:
 *
 *  1. **`aria-expanded` and `aria-controls` on the toggle.** The old button had neither,
 *     and no `aria-label` either — it was a bare `<button>` wrapping an SVG, so a screen
 *     reader announced "button" and nothing more, with no way to know a menu had opened.
 *  2. **Escape closes it and returns focus to the toggle.** Without the return, focus is
 *     left on a node that has just been unmounted and lands back on `<body>` — the user
 *     starts again from the top of the document.
 *  3. **A navigation closes it.** The old header called `setDrawerOpen(false)` on each
 *     link's own `onClick`, which misses every other way the location can change: the Back
 *     button, a redirect thrown by a guard, or `onSignOut` after the logout button in the
 *     drawer itself. Closing on the location makes it uniform.
 *
 * There is deliberately no focus *trap* and no `inert` background. See
 * `@components/ui/icons` for why `@headlessui/react` is not the answer to a widget that
 * must not trap focus.
 */
export default function Header() {
  const { isAuthenticated, logout } = useAuth()
  const [drawerOpen, setDrawerOpen] = useState(false)

  // `useId` rather than a literal, because two headers on one page (a test rendering twice
  // before cleanup, a future print layout) must not share one `aria-controls` target.
  const drawerId = useId()
  const toggleRef = useRef(null)

  // The location, not the match: the hash changes without the route changing, and the
  // marketing nav is entirely fragments of one route.
  const { pathname, hash } = useRouterState({ select: (state) => state.location })

  const items = navItemsFor(isAuthenticated)

  /**
   * Close on *any* completed navigation.
   *
   * Keyed on the location rather than wired to each link's `onClick`, so a guard's
   * redirect, a Back press and the drawer's own logout button all close it too. Running on
   * mount is harmless — it is already closed.
   */
  useEffect(() => {
    setDrawerOpen(false)
  }, [pathname, hash])

  /**
   * Escape closes the drawer and puts focus back on the button that opened it.
   *
   * Bound only while open, so the app has no keydown listener the rest of the time. The
   * focus return is the half that is easy to forget and impossible to notice with a mouse:
   * without it the focused element unmounts and the browser falls back to `<body>`.
   */
  useEffect(() => {
    if (!drawerOpen) return

    const onKeyDown = (event) => {
      if (event.key !== 'Escape') return
      setDrawerOpen(false)
      toggleRef.current?.focus()
    }

    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [drawerOpen])

  const navLink = (item, extraClassName = '') => (
    <Link
      key={item.key}
      to={item.to}
      {...(item.hash === undefined ? {} : { hash: item.hash })}
      // `Link` decides "am I current" itself and writes `aria-current="page"` from it, so
      // the answer has to be corrected here rather than overridden downstream. Both
      // defaults are wrong for this nav: `exact: false` makes `/` a prefix of every route,
      // so "Home" would light up on `/research`; `includeHash: false` makes all five
      // marketing items — which share one route and differ only by fragment — active at
      // once on the home page. `NAV_ACTIVE_OPTIONS` fixes both, and `isNavItemActive` is
      // the same rule as a pure function, so a test can state it without a router.
      activeOptions={NAV_ACTIVE_OPTIONS}
      className={`font-gothic font-demi text-xl ${extraClassName}`}
      activeProps={{
        className: 'font-bold text-brand-600 underline dark:text-brand-400',
      }}
      inactiveProps={{
        className:
          'text-neutral-700 hover:text-brand-600 hover:underline dark:text-neutral-300 dark:hover:text-brand-400',
      }}
    >
      {item.label}
    </Link>
  )

  /**
   * The actions to the right of the nav: the theme toggle, then either "Log Out" or the
   * two calls to action. Rendered twice — desktop bar and drawer — from one function, so
   * the two cannot drift the way the old file's two copies could.
   */
  const actions = (testId) => (
    <div data-testid={testId} className="flex items-center gap-4">
      <DarkModeSwitcher />
      {isAuthenticated ? (
        <button
          type="button"
          onClick={logout}
          className="rounded-md border border-neutral-300 px-3 py-2 font-gothic font-demi hover:scale-105 hover:underline dark:border-neutral-700"
        >
          Log Out
        </button>
      ) : (
        <>
          <Link
            to={LOGIN_ITEM.to}
            className="rounded-md border border-neutral-300 px-3 py-2 font-gothic font-demi hover:scale-105 hover:underline dark:border-neutral-700"
          >
            {LOGIN_ITEM.label}
          </Link>
          <Link
            to={SIGNUP_ITEM.to}
            className="rounded-md border bg-gradient-to-r from-brand-500 to-brand-700 px-3 py-2 font-gothic font-demi text-white hover:opacity-90 hover:underline dark:from-brand-400 dark:to-brand-600"
          >
            {SIGNUP_ITEM.label}
          </Link>
        </>
      )}
    </div>
  )

  return (
    <nav
      aria-label="Main"
      className="sticky top-0 z-50 border-b border-neutral-200 py-3 backdrop-blur-lg dark:border-neutral-800"
    >
      <div className="relative mx-auto px-3 text-sm">
        <div className="flex items-center justify-between">
          {/*
            The brand goes home. The old header rendered the wordmark and the logo as inert
            markup, so the one link every site has in the top-left was missing. One `<Link>`
            wraps both halves; only one of them is ever displayed, so the accessible name
            comes from `aria-label` and both glyphs are hidden from the tree.
          */}
          <Link
            to={HOME_ROUTE}
            aria-label="Anvex home"
            className="flex flex-shrink-0 items-center"
          >
            <h1 aria-hidden="true" className="hidden font-gothic text-2xl md:block">
              <span className="text-brand-600 dark:text-brand-400">Anvex</span>
            </h1>
            <Logo
              aria-hidden="true"
              focusable="false"
              className="h-7 w-auto text-brand-600 md:hidden dark:text-brand-400"
              fill="currentColor"
            />
          </Link>

          <ul data-testid="header-desktop-nav" className="ml-14 hidden space-x-12 lg:flex">
            {items.map((item) => (
              <li key={item.key}>{navLink(item)}</li>
            ))}
          </ul>

          <div className="hidden lg:flex">{actions('header-desktop-actions')}</div>

          <div className="lg:hidden">
            <button
              type="button"
              ref={toggleRef}
              onClick={() => setDrawerOpen((open) => !open)}
              aria-expanded={drawerOpen}
              aria-controls={drawerId}
              aria-label={drawerOpen ? 'Close main menu' : 'Open main menu'}
              className="rounded-md p-1 text-neutral-700 hover:bg-neutral-100 dark:text-neutral-300 dark:hover:bg-neutral-800"
            >
              {drawerOpen ? (
                <XMarkIcon className="block h-6 w-6" />
              ) : (
                <Bars3Icon className="block h-6 w-6" />
              )}
            </button>
          </div>
        </div>

        {drawerOpen && (
          <div
            id={drawerId}
            data-testid="header-drawer"
            className="fixed right-0 z-20 mt-3 flex w-full flex-col items-center justify-center border-t border-neutral-200 bg-neutral-100 p-12 lg:hidden dark:border-neutral-800 dark:bg-neutral-900"
          >
            <ul>
              {items.map((item) => (
                <li key={item.key} className="py-2">
                  {navLink(item, 'block')}
                </li>
              ))}
            </ul>
            <div className="mt-4">{actions('header-drawer-actions')}</div>
          </div>
        )}
      </div>
    </nav>
  )
}
