import { RouterProvider, createMemoryHistory } from '@tanstack/react-router'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { createAppRouter } from '@lib/router'
import { AuthContext } from '@providers/AuthContext'
import { ThemeProvider } from '@providers/ThemeProvider'
import { THEME_STORAGE_KEY } from '@providers/themeStorage'

import { ANONYMOUS_NAV_ITEMS, isNavItemActive } from './navItems'

/**
 * The shell: `Layout`, `Header` and `DarkModeSwitcher` (ANV-28).
 *
 * These render the **real router**, because `Header` is the root route's component — it
 * reads `useRouterState()` for the active item and its links are TanStack `<Link>`s, so a
 * bare `render(<Header/>)` would need a router mocked around it and would then prove
 * nothing about the hrefs. The session is supplied as a plain object through
 * `AuthContext`, the same shape `useAuth()` returns; `App.test.jsx` covers the wiring to
 * the real `AuthProvider`, including the logout round trip.
 */
function renderAt(path, { isAuthenticated = false } = {}) {
  const router = createAppRouter({ history: createMemoryHistory({ initialEntries: [path] }) })
  const auth = { isAuthenticated, login: vi.fn(), logout: vi.fn(), restore: vi.fn() }

  render(
    <ThemeProvider>
      <AuthContext.Provider value={auth}>
        <RouterProvider router={router} context={{ auth }} />
      </AuthContext.Provider>
    </ThemeProvider>,
  )

  return { router, auth, location: () => router.state.location }
}

const desktopNav = () => within(screen.getByTestId('header-desktop-nav'))
const desktopActions = () => within(screen.getByTestId('header-desktop-actions'))
const drawer = () => within(screen.getByTestId('header-drawer'))
const root = () => document.documentElement

/**
 * `cleanup()` unmounts the tree but `<html>` and `localStorage` are the *document's*, not
 * React's — so a test that switches to dark would otherwise persist `theme: 'dark'` and
 * leave the class behind for the next one, which would then pass or fail depending on the
 * order it ran in.
 */
beforeEach(() => {
  window.localStorage.clear()
  root().className = ''
})

describe('the layout wraps every route', () => {
  it.each([
    ['/', 'route-home'],
    ['/login', 'route-login'],
    ['/signup', 'route-sign-up'],
    ['/nope', 'route-not-found'],
  ])('renders the header above %s', async (path, testId) => {
    // Public routes and the 404 included — the old app's `<Route path="/"
    // element={<Layout/>}>` covered them all, and a visitor needs the header most when they
    // are signed out, because that is where "Log In" is.
    renderAt(path)

    expect(await screen.findByTestId(testId)).toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: 'Main' })).toBeInTheDocument()
  })

  it('renders it above a protected route too', async () => {
    renderAt('/research', { isAuthenticated: true })

    expect(await screen.findByTestId('route-research')).toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: 'Main' })).toBeInTheDocument()
  })

  it('puts the outlet in a <main> that is a sibling of the nav, not a child', async () => {
    // The old shell wrapped the header *inside* `<main>`, which makes the document's
    // primary content contain the site navigation.
    renderAt('/')
    await screen.findByTestId('route-home')

    const main = document.querySelector('main')
    expect(main).not.toBeNull()
    expect(within(main).getByTestId('route-home')).toBeInTheDocument()
    expect(within(main).queryByRole('navigation', { name: 'Main' })).not.toBeInTheDocument()
  })
})

describe('the nav is auth-aware', () => {
  it('shows the marketing sections and both calls to action when signed out', async () => {
    renderAt('/')
    await screen.findByTestId('route-home')

    for (const label of ['Home', 'Features', 'Workflow', 'Pricing', 'Contact Us']) {
      expect(desktopNav().getByRole('link', { name: label })).toBeInTheDocument()
    }
    expect(desktopActions().getByRole('link', { name: 'Log In' })).toBeInTheDocument()
    expect(desktopActions().getByRole('link', { name: 'Create an Account' })).toBeInTheDocument()

    expect(desktopNav().queryByRole('link', { name: 'Research' })).not.toBeInTheDocument()
    expect(desktopActions().queryByRole('button', { name: 'Log Out' })).not.toBeInTheDocument()
  })

  it('shows the app and a way out when signed in', async () => {
    renderAt('/research', { isAuthenticated: true })
    await screen.findByTestId('route-research')

    expect(desktopNav().getByRole('link', { name: 'Research' })).toBeInTheDocument()
    expect(desktopNav().getByRole('link', { name: 'Portfolio' })).toBeInTheDocument()
    expect(desktopActions().getByRole('button', { name: 'Log Out' })).toBeInTheDocument()

    for (const label of ['Features', 'Pricing', 'Contact Us']) {
      expect(desktopNav().queryByRole('link', { name: label })).not.toBeInTheDocument()
    }
    expect(desktopActions().queryByRole('link', { name: 'Log In' })).not.toBeInTheDocument()
    expect(desktopActions().queryByRole('link', { name: 'Create an Account' })).not.toBeInTheDocument()
  })

  it('builds every href from @routes/paths rather than a string in the markup', async () => {
    renderAt('/')
    await screen.findByTestId('route-home')

    // The values come from `paths.js`; the point of asserting them is that a `<Link to>`
    // was used at all. The old header wrote `<a href='/login'>` — a full document
    // navigation, which reloads the bundle and (since ANV-26 keeps the access token in
    // memory) throws away the session of anyone who had one.
    expect(desktopActions().getByRole('link', { name: 'Log In' })).toHaveAttribute(
      'href',
      '/login',
    )
    expect(desktopActions().getByRole('link', { name: 'Create an Account' })).toHaveAttribute(
      'href',
      '/signup',
    )
    expect(desktopNav().getByRole('link', { name: 'Features' })).toHaveAttribute(
      'href',
      '/#features',
    )
    expect(screen.getByRole('link', { name: 'Anvex home' })).toHaveAttribute('href', '/')
  })

  it('marks only the current item, fragment included', async () => {
    // Five of the anonymous items share one route. Comparing the path alone — which is what
    // TanStack's own `activeProps` does by default — lights all five up at once on `/`.
    renderAt('/#pricing')
    await screen.findByTestId('route-home')

    expect(desktopNav().getByRole('link', { name: 'Pricing' })).toHaveAttribute(
      'aria-current',
      'page',
    )
    for (const label of ['Home', 'Features', 'Workflow', 'Contact Us']) {
      expect(desktopNav().getByRole('link', { name: label })).not.toHaveAttribute('aria-current')
    }
  })

  it('navigates in-app rather than reloading the document', async () => {
    // The assertion the href check above cannot make. `<a href='/login'>` — what the old
    // header used — produces an identical `href` and a *full document navigation*: the
    // bundle reloads and ANV-26's in-memory access token is gone, so following "Log In"
    // ended the session of anyone who had one. Clicking a real `<Link>` moves the router;
    // clicking a bare `<a>` under jsdom moves nothing, so this fails on the old markup.
    const user = userEvent.setup()
    const { location } = renderAt('/')
    await screen.findByTestId('route-home')

    await user.click(desktopActions().getByRole('link', { name: 'Log In' }))

    expect(await screen.findByTestId('route-login')).toBeInTheDocument()
    expect(location().pathname).toBe('/login')
  })

  it.each(['/', '/#features', '/#pricing', '/#contact'])(
    'agrees with isNavItemActive at %s, item for item',
    async (path) => {
      // The rendered markup obeys `NAV_ACTIVE_OPTIONS`; `isNavItemActive` states the same
      // rule as a function. This is what stops the two being two rules: every item is
      // compared, at four locations, against what the pure rule says — so a change to the
      // options that broke the fragment comparison would fail here as well as above.
      const { location } = renderAt(path)
      await screen.findByTestId('route-home')
      const { pathname, hash } = location()

      for (const item of ANONYMOUS_NAV_ITEMS) {
        const link = desktopNav().getByRole('link', { name: item.label })
        if (isNavItemActive(item, { pathname, hash })) {
          expect(link).toHaveAttribute('aria-current', 'page')
        } else {
          expect(link).not.toHaveAttribute('aria-current')
        }
      }
    },
  )

  it('marks Home on the bare route and nothing else', async () => {
    renderAt('/')
    await screen.findByTestId('route-home')

    expect(desktopNav().getByRole('link', { name: 'Home' })).toHaveAttribute(
      'aria-current',
      'page',
    )
    expect(desktopNav().getByRole('link', { name: 'Features' })).not.toHaveAttribute(
      'aria-current',
    )
  })
})

describe('the mobile drawer', () => {
  it('is closed to begin with, and says so', async () => {
    renderAt('/')
    await screen.findByTestId('route-home')

    const toggle = screen.getByRole('button', { name: 'Open main menu' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByTestId('header-drawer')).not.toBeInTheDocument()
  })

  it('opens and closes on the toggle, and names the panel it controls', async () => {
    const user = userEvent.setup()
    renderAt('/')
    await screen.findByTestId('route-home')

    await user.click(screen.getByRole('button', { name: 'Open main menu' }))

    const opened = screen.getByRole('button', { name: 'Close main menu' })
    expect(opened).toHaveAttribute('aria-expanded', 'true')
    const panel = screen.getByTestId('header-drawer')
    // Not just "an id exists" — the toggle has to point at *this* panel, or a screen
    // reader's "go to the thing this controls" lands nowhere.
    expect(opened.getAttribute('aria-controls')).toBe(panel.getAttribute('id'))
    expect(drawer().getByRole('link', { name: 'Features' })).toBeInTheDocument()

    await user.click(opened)
    expect(screen.queryByTestId('header-drawer')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Open main menu' })).toHaveAttribute(
      'aria-expanded',
      'false',
    )
  })

  it('opens from the keyboard alone', async () => {
    const user = userEvent.setup()
    renderAt('/')
    await screen.findByTestId('route-home')

    const toggle = screen.getByRole('button', { name: 'Open main menu' })
    toggle.focus()
    await user.keyboard('{Enter}')

    expect(screen.getByTestId('header-drawer')).toBeInTheDocument()
  })

  it('closes on Escape and puts focus back on the toggle', async () => {
    const user = userEvent.setup()
    renderAt('/')
    await screen.findByTestId('route-home')

    const toggle = screen.getByRole('button', { name: 'Open main menu' })
    await user.click(toggle)
    // Focus is inside the panel, as it would be after tabbing into it.
    drawer().getByRole('link', { name: 'Features' }).focus()

    await user.keyboard('{Escape}')

    expect(screen.queryByTestId('header-drawer')).not.toBeInTheDocument()
    // Without the restore, focus is on an element that has just been unmounted and the
    // browser drops it on <body> — the keyboard user starts again from the top of the page.
    expect(screen.getByRole('button', { name: 'Open main menu' })).toHaveFocus()
  })

  it('lets Tab out of the panel rather than trapping focus in it', async () => {
    // A disclosure navigation menu deliberately does not trap: the panel follows its toggle
    // in the DOM, so tabbing past its last control must reach the page. This is what makes
    // hand-rolling it defensible instead of reaching for `@headlessui`'s `Dialog`, which
    // would add the trap and the inert background the pattern does not want.
    const user = userEvent.setup()
    renderAt('/research', { isAuthenticated: true })
    await screen.findByTestId('route-research')

    await user.click(screen.getByRole('button', { name: 'Open main menu' }))
    const panel = screen.getByTestId('header-drawer')
    const last = drawer().getByRole('button', { name: 'Log Out' })
    last.focus()

    await user.tab()

    expect(panel.contains(document.activeElement)).toBe(false)
  })

  it('closes when the location changes, not when a link says so', async () => {
    const user = userEvent.setup()
    renderAt('/research', { isAuthenticated: true })
    await screen.findByTestId('route-research')

    await user.click(screen.getByRole('button', { name: 'Open main menu' }))
    expect(screen.getByTestId('header-drawer')).toBeInTheDocument()

    // A navigation that came from *outside* the panel. The old header closed only from each
    // drawer link's own `onClick`, so a guard's redirect, a Back press, or `onSignOut` all
    // left the panel hanging open over the new page. `App.test.jsx`'s "works from inside
    // the mobile drawer" covers the `onSignOut` case with the real store behind it.
    await user.click(desktopNav().getByRole('link', { name: 'Portfolio' }))

    expect(await screen.findByTestId('route-portfolio')).toBeInTheDocument()
    expect(screen.queryByTestId('header-drawer')).not.toBeInTheDocument()
  })

  it('closes when one of its own links is followed', async () => {
    const user = userEvent.setup()
    renderAt('/research', { isAuthenticated: true })
    await screen.findByTestId('route-research')

    await user.click(screen.getByRole('button', { name: 'Open main menu' }))
    await user.click(drawer().getByRole('link', { name: 'Portfolio' }))

    expect(await screen.findByTestId('route-portfolio')).toBeInTheDocument()
    expect(screen.queryByTestId('header-drawer')).not.toBeInTheDocument()
  })
})

describe('the dark-mode switcher', () => {
  it('flips the theme and the root class follows', async () => {
    const user = userEvent.setup()
    renderAt('/')
    await screen.findByTestId('route-home')

    expect(root()).toHaveClass('light')

    await user.click(desktopActions().getByRole('button', { name: 'Switch to dark theme' }))

    // Tailwind's `darkMode: 'class'` reads exactly this, so the class *is* the feature.
    expect(root()).toHaveClass('dark')
    expect(root()).not.toHaveClass('light')
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark')

    await user.click(desktopActions().getByRole('button', { name: 'Switch to light theme' }))

    expect(root()).toHaveClass('light')
    expect(root()).not.toHaveClass('dark')
  })

  it('is the same context in the drawer, so the two instances cannot disagree', async () => {
    const user = userEvent.setup()
    renderAt('/')
    await screen.findByTestId('route-home')

    await user.click(screen.getByRole('button', { name: 'Open main menu' }))
    await user.click(drawer().getByRole('button', { name: 'Switch to dark theme' }))

    expect(root()).toHaveClass('dark')
    // The desktop copy re-rendered from the same provider; it is not holding its own state.
    expect(
      desktopActions().getByRole('button', { name: 'Switch to light theme' }),
    ).toBeInTheDocument()
  })

  it('names what pressing it does, so the change is announced', async () => {
    // The old button's `aria-label` was the constant 'Toggle theme': same name before and
    // after, and the only thing that changed was a decorative glyph, so a screen-reader
    // user got no feedback that anything had happened at all.
    const user = userEvent.setup()
    renderAt('/')
    await screen.findByTestId('route-home')

    const before = desktopActions().getByRole('button', { name: 'Switch to dark theme' })
    await user.click(before)
    expect(
      desktopActions().getByRole('button', { name: 'Switch to light theme' }),
    ).toBeInTheDocument()
  })
})
