import { RouterProvider, createMemoryHistory } from '@tanstack/react-router'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { createAppRouter } from '@lib/router'
import { AuthContext } from '@providers/AuthContext'
import { ThemeProvider } from '@providers/ThemeProvider'
import { HOME_ROUTE, LOGIN_ROUTE, UNAUTHORIZED_ROUTE } from '@routes/paths'

/**
 * `/unauthorized` (ANV-31).
 *
 * One harness is enough here, unlike the form pages: the screen has no state, makes no
 * request and stores nothing, so the only thing worth asserting beyond "it renders" is that
 * its two links **move the router** rather than reload the document — and a memory history
 * shows that as well as a browser one does.
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

const page = () => screen.getByTestId('route-unauthorized')

beforeEach(() => {
  window.localStorage.clear()
  document.documentElement.className = ''
})

describe('the page', () => {
  it.each([
    ['anonymous', false],
    ['signed in', true],
  ])('renders under the shell for a %s visitor', async (_label, isAuthenticated) => {
    // Public in both directions, deliberately: it is where a *signed-in* user would be
    // refused, so a guard on it would be circular, and an anonymous visitor who typed the
    // address is owed the explanation rather than a bounce to /login.
    const { location } = renderAt(UNAUTHORIZED_ROUTE, { isAuthenticated })

    expect(await screen.findByTestId('route-unauthorized')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Unauthorized' })).toBeInTheDocument()
    expect(location().pathname).toBe(UNAUTHORIZED_ROUTE)
  })

  it('says that Anvex has no permission levels rather than implying one', async () => {
    // The honesty assertion, and the reason it is worth a test. The old copy — "You do not
    // have access to the requested page" — describes a permission system this API does not
    // have: no role is stored, no token claim carries one, and no service raises
    // `ForbiddenError`. Copy that sends a user looking for an administrator who does not
    // exist is a defect in the same way a broken link is.
    renderAt(UNAUTHORIZED_ROUTE)
    await screen.findByTestId('route-unauthorized')

    expect(page()).toHaveTextContent(/no roles, groups or permission levels/)
    expect(page()).not.toHaveTextContent(/do not have (access|permission)/i)
  })

  it('does not offer the old Go Back button', async () => {
    // `navigate(-1)` went back to the page that had just refused the user — a loop — and on
    // a tab opened directly on this URL there is no previous entry, so it did nothing at
    // all. Two links to destinations that always exist replace it.
    renderAt(UNAUTHORIZED_ROUTE)
    await screen.findByTestId('route-unauthorized')

    expect(within(page()).queryByRole('button')).not.toBeInTheDocument()
  })
})

describe('its links', () => {
  it.each([
    ['Back to home', HOME_ROUTE, 'route-home'],
    ['Go to the login page', LOGIN_ROUTE, 'route-login'],
  ])('moves the router to %s when clicked', async (name, path, testId) => {
    // ANV-28's rule, and the assertion that discriminates: an `href` check passes for a
    // bare `<a>` too, and a bare `<a>` is a document navigation that reloads the bundle and
    // throws away the in-memory access token of whoever is reading this page.
    const user = userEvent.setup()
    const { location } = renderAt(UNAUTHORIZED_ROUTE)
    await screen.findByTestId('route-unauthorized')

    await user.click(within(page()).getByRole('link', { name }))

    expect(await screen.findByTestId(testId)).toBeInTheDocument()
    expect(location().pathname).toBe(path)
  })

  it('reaches both of them from the keyboard', async () => {
    // They are real `<a>`s rendered by `<Link>`, so they are in the tab order — which is
    // the half the old page's `<p onClick>` navigation elsewhere in the app got wrong.
    const user = userEvent.setup()
    renderAt(UNAUTHORIZED_ROUTE)
    await screen.findByTestId('route-unauthorized')

    const links = within(page()).getAllByRole('link')
    links[0].focus()
    expect(links[0]).toHaveFocus()
    await user.tab()
    expect(links[1]).toHaveFocus()
  })
})
