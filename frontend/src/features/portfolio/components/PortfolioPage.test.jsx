import { RouterProvider, createMemoryHistory } from '@tanstack/react-router'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createAppRouter } from '@lib/router'
import { AuthContext } from '@providers/AuthContext'
import { ThemeProvider } from '@providers/ThemeProvider'
import { server } from '@test/msw/server'

/**
 * `/portfolio` (ANV-36).
 *
 * **All real behaviour, and no measurement anywhere** — there is nothing on this page that
 * has a size. It is mounted through the **real router** rather than in isolation, because
 * two of the four things worth asserting are routing facts (the guard admits a session; the
 * link is a `<Link>` and not a document navigation) and because the third — that the page
 * issues no request — is only meaningful for the page as the application actually mounts it.
 *
 * `renderAt` is ANV-28's copyable helper: the *same* `auth` object in the React context and
 * the router context, so the nav and the guards cannot disagree in a way the app cannot.
 */
function renderAt(path, { isAuthenticated = true } = {}) {
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

/** Every request MSW saw, whether or not a handler answered it. */
const requests = []

beforeEach(() => {
  requests.length = 0
  // In the file that cares, never in `src/test/setup.js` — a shared listener is state
  // leaking between tests (ANV-35).
  server.events.on('request:start', ({ request }) => requests.push(request.url))
})

afterEach(() => {
  server.events.removeAllListeners('request:start')
})

describe('the guard', () => {
  it('admits a signed-in user', async () => {
    const { location } = renderAt('/portfolio')

    expect(await screen.findByTestId('route-portfolio')).toBeInTheDocument()
    expect(location().pathname).toBe('/portfolio')
  })

  it('sends an anonymous visitor to /login carrying where they were going', async () => {
    const { location } = renderAt('/portfolio', { isAuthenticated: false })

    expect(await screen.findByTestId('route-login')).toBeInTheDocument()
    expect(location().search).toEqual({ redirect: '/portfolio' })
    expect(screen.queryByTestId('route-portfolio')).not.toBeInTheDocument()
  })
})

describe('what the page says', () => {
  it('keeps the ported heading', async () => {
    renderAt('/portfolio')

    expect(await screen.findByRole('heading', { level: 1, name: 'Portfolio' })).toBeInTheDocument()
  })

  it('names the absence instead of implying an empty portfolio', async () => {
    // ANV-31's rule, and the assertion has two halves for a reason: the page must say the
    // *product* does not track holdings, and must not say anything that reads as "you own
    // nothing yet" — which is what an empty table with column headers would say.
    renderAt('/portfolio')

    expect(await screen.findByText(/does not track holdings yet/i)).toBeInTheDocument()
    expect(screen.getByText(/not empty because your portfolio is empty/i)).toBeInTheDocument()
  })

  it('does not put the old developer placeholder in front of a user', async () => {
    // "Portfolio content goes here..." was a note to the author. Shipping it is the same
    // category of defect as shipping an `<a href="#">`.
    renderAt('/portfolio')
    await screen.findByTestId('route-portfolio')

    expect(screen.queryByText(/content goes here/i)).not.toBeInTheDocument()
  })

  it('offers a destination that exists, as a Link and not a document navigation', async () => {
    // ANV-28: an `href` assertion cannot tell a `<Link>` from an `<a href>`, and a bare
    // anchor reloads the document and discards the in-memory access token. Click it and
    // assert the router moved.
    const user = userEvent.setup()
    const { location } = renderAt('/portfolio')
    await screen.findByTestId('route-portfolio')

    await user.click(screen.getByRole('link', { name: 'price charts and your watchlists' }))

    expect(await screen.findByTestId('route-research')).toBeInTheDocument()
    expect(location().pathname).toBe('/research')
  })
})

describe('the network', () => {
  it('makes no request at all, because there is no endpoint to make one to', async () => {
    // Counted rather than left to `onUnhandledRequest: 'error'` (ANV-35): a component that
    // caught its own failure would absorb the call and leave the suite green. This is also
    // the assertion that fails the day somebody wires a holdings endpoint in without
    // telling this test — which is exactly when it should be reconsidered.
    renderAt('/portfolio')
    await screen.findByTestId('route-portfolio')

    await waitFor(() => expect(screen.getByTestId('route-portfolio')).toBeInTheDocument())
    expect(requests).toEqual([])
  })
})
