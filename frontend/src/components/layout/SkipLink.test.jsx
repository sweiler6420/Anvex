import { RouterProvider, createMemoryHistory } from '@tanstack/react-router'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { createAppRouter } from '@lib/router'
import { AuthContext } from '@providers/AuthContext'
import { ThemeProvider } from '@providers/ThemeProvider'

import { MAIN_CONTENT_ID } from './SkipLink'

/**
 * The skip link (ANV-32) — the decision ANV-28 deferred.
 *
 * These tests are about the *shape* of the answer, not about the button. A fragment skip
 * link (`<a href="#main-content">`) is the obvious implementation and it is the one that
 * breaks the header: the hash is part of the router's location, and the location is what
 * `NAV_ACTIVE_OPTIONS` reads to decide which marketing item is current. So there are two
 * assertions and they fail for different mutations — focus moves (drop `tabIndex={-1}` from
 * `<main>` and this fails), and the location does not change (implement it as a `<Link>` and
 * *both* fail, the second by un-highlighting "Home").
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

  return { router, location: () => router.state.location }
}

const skipButton = () => screen.getByRole('button', { name: 'Skip to main content' })
const main = () => document.getElementById(MAIN_CONTENT_ID)

beforeEach(() => {
  window.localStorage.clear()
  document.documentElement.className = ''
})

describe('the skip link', () => {
  it('is the first thing a keyboard reaches, on every route', async () => {
    // A skip control after the header skips nothing: the header is up to nine tab stops and
    // they are all in front of the page.
    const user = userEvent.setup()
    renderAt('/')
    await screen.findByTestId('route-home')

    await user.tab()

    expect(document.activeElement).toBe(skipButton())
  })

  it('moves focus into the page content', async () => {
    // Activated from the keyboard, not `user.click`: a `<div onClick>` shim would pass a
    // click and fail this, which is ANV-29's rule for an icon that is also a control.
    const user = userEvent.setup()
    renderAt('/')
    await screen.findByTestId('route-home')

    skipButton().focus()
    await user.keyboard('{Enter}')

    expect(document.activeElement).toBe(main())
    expect(main()).toContainElement(screen.getByTestId('route-home'))
  })

  it('does not touch the location, so the nav stays current', async () => {
    // The whole reason this is a button. `<a href="#main-content">` would put
    // `main-content` in the hash, which matches no nav item — so pressing the very first
    // control on the page would un-highlight the nav.
    const user = userEvent.setup()
    const { location } = renderAt('/')
    await screen.findByTestId('route-home')

    const homeLink = within(screen.getByTestId('header-desktop-nav')).getByRole('link', {
      name: 'Home',
    })
    expect(homeLink).toHaveAttribute('aria-current', 'page')

    skipButton().focus()
    await user.keyboard('{Enter}')

    expect(location().hash).toBe('')
    expect(location().pathname).toBe('/')
    expect(homeLink).toHaveAttribute('aria-current', 'page')
  })

  it('leaves the main content out of the tab order', async () => {
    // `tabIndex={-1}` is programmatically focusable, never tabbable: tabbing off the skip
    // button must reach the header, not a container nobody asked to focus.
    const user = userEvent.setup()
    renderAt('/')
    await screen.findByTestId('route-home')

    skipButton().focus()
    await user.tab()

    expect(document.activeElement).not.toBe(main())
  })

  it('works on a route that is not the home page', async () => {
    const user = userEvent.setup()
    renderAt('/login')
    await screen.findByTestId('route-login')

    skipButton().focus()
    await user.keyboard('{Enter}')

    expect(document.activeElement).toBe(main())
  })
})
