import { RouterProvider, createMemoryHistory } from '@tanstack/react-router'
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from '@/App'
import { RECOVERY_PATH } from '@features/auth/api'
import { apiUrl } from '@lib/env'
import { createAppRouter } from '@lib/router'
import { AuthContext } from '@providers/AuthContext'
import { ThemeProvider } from '@providers/ThemeProvider'
import { LOGIN_ROUTE, RECOVERY_ROUTE } from '@routes/paths'
import { errorResponse } from '@test/msw/handlers'
import { server } from '@test/msw/server'

/**
 * The recovery page (ANV-31).
 *
 * Two harnesses, ANV-29's split. `renderRecovery` mounts the real router over a memory
 * history with a stubbed `AuthContext` (the shell's header needs one) and **MSW as the
 * seam** — `requestRecovery` is imported directly by the component, which is the point of a
 * feature `api.js`, so a handler that *records* what it was sent is how "an invalid submit
 * never reaches the network" becomes an assertion on an empty array rather than on the
 * absence of a message. `renderAppAt` mounts the whole application over the real browser
 * history for the one thing that is invisible from inside the component: whether the page
 * moves the user anywhere.
 *
 * Nothing here stubs `axios` or `fetch` (CLAUDE.md §5).
 */

// ---------------------------------------------------------------------------- harnesses

function renderRecovery(path = RECOVERY_ROUTE) {
  const history = createMemoryHistory({ initialEntries: [path] })
  const router = createAppRouter({ history })
  const auth = { isAuthenticated: false, login: vi.fn(), logout: vi.fn(), restore: vi.fn() }

  const view = render(
    <ThemeProvider>
      <AuthContext.Provider value={auth}>
        <RouterProvider router={router} context={{ auth }} />
      </AuthContext.Provider>
    </ThemeProvider>,
  )

  return { ...view, router, location: () => router.state.location }
}

function renderAppAt(path) {
  window.history.replaceState(null, '', path)
  return render(
    <ThemeProvider>
      <App />
    </ThemeProvider>,
  )
}

const appLocation = () => `${window.location.pathname}${window.location.search}`

const page = () => screen.getByTestId('route-recovery')
const usernameField = () => screen.getByLabelText('Username:')
const submit = () => screen.getByRole('button', { name: /Submit|Try Again|Submitting/ })
const usernameMessage = () => screen.getByTestId('recovery-username-error')
const banner = () => screen.getByTestId('recovery-error')
const confirmation = () => screen.getByTestId('recovery-confirmation')

/**
 * The **real** body `POST /v1/auth/recovery` returns — `app/schemas/auth.py`'s
 * `RecoveryAccepted`, whose two fields both have fixed defaults and no caller-controlled
 * part. 202, and the same bytes for every username in existence and every username not.
 */
const ACCEPTED = Object.freeze({
  status: 'accepted',
  message: 'If an account matches that username, a password reset will be arranged for it.',
})

/**
 * Record every recovery request and answer 202.
 *
 * Installed by **every** test, the tests that must not reach the network included: "the
 * request was never made" needs a recorder that would have caught it if it had been.
 */
function mockRecovery(respond = () => HttpResponse.json(ACCEPTED, { status: 202 })) {
  const seen = []
  server.use(
    http.post(apiUrl(RECOVERY_PATH), async ({ request }) => {
      seen.push(await request.json())
      return respond(seen.length)
    }),
  )
  return seen
}

/**
 * React 18 generates `useId` values from a module-global counter, so two renders of the
 * *same* markup differ in exactly those tokens and in nothing else. Blanking them is what
 * makes "byte-identical" assertable across two renders; it hides nothing, because the token
 * is minted by React and carries nothing from the user or from the wire. If React's format
 * ever changed the regex would match nothing and the comparison would fail — loudly — which
 * is the right failure for a normaliser that has stopped working.
 */
const REACT_ID = /[«:]r[0-9a-z]+[»:]/g
const withoutReactIds = (html) => html.replace(REACT_ID, '<react-id>')

beforeEach(() => {
  window.localStorage.clear()
  window.history.replaceState(null, '', '/')
  document.documentElement.className = ''
})

afterEach(() => {
  window.localStorage.clear()
})

// -------------------------------------------------------------------------------- shape

describe('the form', () => {
  it('renders under the shell at /recovery, with the field named by its label', async () => {
    // The old markup had no `htmlFor` and no `id`, so the field had no accessible name at
    // all. `getByLabelText` is the query that fails on that markup.
    mockRecovery()
    renderRecovery()

    expect(await screen.findByTestId('route-recovery')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Password Recovery' })).toBeInTheDocument()
    expect(usernameField()).toBeInTheDocument()
    expect(submit()).toHaveTextContent('Submit')
  })

  it('renders every message region up front and leaves it empty', async () => {
    // A live region has to be in the accessibility tree *before* its text arrives. The old
    // page rendered its error `<p>` conditionally and gave it no role at all.
    mockRecovery()
    renderRecovery()
    await screen.findByTestId('route-recovery')

    expect(usernameMessage()).toBeEmptyDOMElement()
    expect(usernameMessage()).toHaveAttribute('role', 'alert')
    expect(banner()).toBeEmptyDOMElement()
    expect(banner()).toHaveAttribute('role', 'alert')
    // `role="status"`, not `alert`: a confirmation is an answer, not an interruption. It
    // lives outside the form, so replacing the form on success does not take it with it.
    expect(confirmation()).toBeEmptyDOMElement()
    expect(confirmation()).toHaveAttribute('role', 'status')
  })

  it('marks the field invalid and points at its message only once it has failed', async () => {
    const user = userEvent.setup()
    mockRecovery()
    renderRecovery()
    await screen.findByTestId('route-recovery')

    expect(usernameField()).toHaveAttribute('aria-invalid', 'false')

    await user.click(submit())

    expect(usernameField()).toHaveAttribute('aria-invalid', 'true')
    // The description has to *be* the message, not merely exist — an `aria-describedby`
    // pointing at an empty node reads as a field with no explanation.
    const describedBy = usernameField().getAttribute('aria-describedby')
    expect(document.getElementById(describedBy)).toHaveTextContent('Enter your username')
  })
})

// --------------------------------------------------------------------------- validation

describe('validation', () => {
  it.each([
    ['nothing at all', ''],
    ['only whitespace', '   '],
  ])('refuses a submit of %s without reaching the network', async (_label, typed) => {
    const user = userEvent.setup()
    const seen = mockRecovery()
    renderRecovery()
    await screen.findByTestId('route-recovery')

    if (typed !== '') await user.type(usernameField(), typed)
    await user.click(submit())

    expect(usernameMessage()).toHaveTextContent('Enter your username')
    // The half a message-only assertion misses: a form that showed the message *and*
    // submitted anyway would pass the line above.
    expect(seen).toEqual([])
    expect(confirmation()).toBeEmptyDOMElement()
  })

  it('clears the message as soon as the user starts fixing it', async () => {
    const user = userEvent.setup()
    mockRecovery()
    renderRecovery()
    await screen.findByTestId('route-recovery')

    await user.click(submit())
    expect(usernameMessage()).toHaveTextContent('Enter your username')

    await user.type(usernameField(), 'a')

    expect(usernameMessage()).toBeEmptyDOMElement()
    expect(usernameField()).toHaveAttribute('aria-invalid', 'false')
  })

  it('sends the trimmed username', async () => {
    const user = userEvent.setup()
    const seen = mockRecovery()
    renderRecovery()
    await screen.findByTestId('route-recovery')

    await user.type(usernameField(), '  ada  ')
    await user.click(submit())

    await screen.findByText(/password reset will be arranged/)
    expect(seen).toEqual([{ username: 'ada' }])
  })
})

// ---------------------------------------------------------------- the identical response

describe('the 202 that says nothing', () => {
  it('confirms the request and replaces the form', async () => {
    const user = userEvent.setup()
    mockRecovery()
    renderRecovery()
    await screen.findByTestId('route-recovery')

    await user.type(usernameField(), 'ada')
    await user.click(submit())

    expect(await screen.findByText(/password reset will be arranged/)).toBeInTheDocument()
    expect(confirmation()).not.toBeEmptyDOMElement()
    // There is nothing left to submit twice, and nothing on screen the user typed.
    expect(screen.queryByLabelText('Username:')).not.toBeInTheDocument()
    expect(page()).not.toHaveTextContent('ada')
  })

  it('renders byte-identical markup for an account that exists and one that does not', async () => {
    // The property CLAUDE.md §4 exists to protect: recovery must not be an enumeration
    // oracle. The endpoint answers 202 with the same body either way, and the page must add
    // nothing of its own — so two *different* usernames have to produce the same pixels.
    const user = userEvent.setup()
    const seen = mockRecovery()

    const first = renderRecovery()
    await screen.findByTestId('route-recovery')
    await user.type(usernameField(), 'ada')
    await user.click(submit())
    await screen.findByText(/password reset will be arranged/)
    const known = withoutReactIds(page().outerHTML)
    first.unmount()

    const second = renderRecovery()
    await screen.findByTestId('route-recovery')
    await user.type(usernameField(), 'nobody-has-this-name')
    await user.click(submit())
    await screen.findByText(/password reset will be arranged/)
    const unknown = withoutReactIds(page().outerHTML)
    second.unmount()

    // Load-bearing: without it the assertion below would pass just as well if the test had
    // submitted the same username twice, or nothing at all.
    expect(seen).toEqual([{ username: 'ada' }, { username: 'nobody-has-this-name' }])
    expect(unknown).toBe(known)
  })

  it('is identical even if the response body itself differs, because it never reads one', async () => {
    // Stronger than the contract, on purpose. The test above holds only while the *server*
    // behaves; this one holds regardless, because the page's confirmation is a constant in
    // its own module and `requestRecovery`'s return value is unused. The second response
    // below is a hypothetical regression — the old `/v1/recovery` echoed the username back
    // — and the point is that reintroducing it upstream could not leak through this page.
    const user = userEvent.setup()
    mockRecovery((call) =>
      call === 1
        ? HttpResponse.json(ACCEPTED, { status: 202 })
        : HttpResponse.json(
            { status: 'accepted', message: 'A reset link was sent to ada@example.com.' },
            { status: 202 },
          ),
    )

    const first = renderRecovery()
    await screen.findByTestId('route-recovery')
    await user.type(usernameField(), 'ada')
    await user.click(submit())
    await screen.findByText(/password reset will be arranged/)
    const fixedBody = withoutReactIds(page().outerHTML)
    first.unmount()

    const second = renderRecovery()
    await screen.findByTestId('route-recovery')
    await user.type(usernameField(), 'ada')
    await user.click(submit())
    await screen.findByText(/password reset will be arranged/)
    const leakyBody = withoutReactIds(page().outerHTML)
    second.unmount()

    expect(leakyBody).toBe(fixedBody)
    expect(leakyBody).not.toContain('ada@example.com')
  })
})

// ------------------------------------------------------------------- nothing is on a clock

describe('the redirect that is not there', () => {
  beforeEach(() => {
    // `shouldAdvanceTime` keeps the fake clock ticking in real time, so MSW's own async
    // machinery still completes while `advanceTimersByTime` is available to jump past a
    // pending redirect. The timers must be installed **before** the submit: a `setTimeout`
    // scheduled on the real clock is not advanced by a fake one installed afterwards, so
    // the naive ordering would make this test unable to fail.
    vi.useFakeTimers({ shouldAdvanceTime: true })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('leaves the user on /recovery long past the old three-second timeout', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    mockRecovery()
    const { location } = renderRecovery()
    await screen.findByTestId('route-recovery')

    await user.type(usernameField(), 'ada')
    await user.click(submit())
    await screen.findByText(/password reset will be arranged/)

    await act(async () => {
      vi.advanceTimersByTime(30_000)
    })

    expect(location().pathname).toBe(RECOVERY_ROUTE)
    expect(confirmation()).not.toBeEmptyDOMElement()
  })

  it('has nothing pending after unmount — the old timer fired into a dead component', async () => {
    // The defect this replaces: `setTimeout(() => navigate("/login"), 3000)` with no
    // teardown, so a user who left within three seconds was dragged back to the login page
    // from a component that no longer existed. Advancing the clock *after* unmounting is
    // what tells a cleared timer from an uncleared one.
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    mockRecovery()
    const { location, unmount } = renderRecovery()
    await screen.findByTestId('route-recovery')

    await user.type(usernameField(), 'ada')
    await user.click(submit())
    await screen.findByText(/password reset will be arranged/)

    unmount()
    await act(async () => {
      vi.advanceTimersByTime(30_000)
    })

    expect(location().pathname).toBe(RECOVERY_ROUTE)
  })
})

// ----------------------------------------------------------------------- the way back out

describe('back to the login page', () => {
  it('offers the link before a submit and still after one', async () => {
    // What replaced the auto-redirect. It has to be there in both states, or the success
    // view is a dead end and the redirect was doing real work after all.
    const user = userEvent.setup()
    mockRecovery()
    renderRecovery()
    await screen.findByTestId('route-recovery')

    expect(within(page()).getByRole('link', { name: 'Back to Log In' })).toBeInTheDocument()

    await user.type(usernameField(), 'ada')
    await user.click(submit())
    await screen.findByText(/password reset will be arranged/)

    expect(within(page()).getByRole('link', { name: 'Back to Log In' })).toBeInTheDocument()
  })

  it('moves the router when clicked, rather than reloading the document', async () => {
    // ANV-28's rule, and an `href` assertion cannot tell the two apart: the old page used
    // `<p onClick={() => navigate("/login")}>`, which is not focusable, and a bare `<a>`
    // would be a document navigation that discards the in-memory access token.
    const user = userEvent.setup()
    mockRecovery()
    const { location } = renderRecovery()
    await screen.findByTestId('route-recovery')

    await user.click(within(page()).getByRole('link', { name: 'Back to Log In' }))

    expect(await screen.findByTestId('route-login')).toBeInTheDocument()
    expect(location().pathname).toBe(LOGIN_ROUTE)
  })
})

// -------------------------------------------------------------------------- when it fails

describe('when the request fails', () => {
  it('shows the API message in the banner and leaves the form to retry', async () => {
    const user = userEvent.setup()
    mockRecovery(() =>
      errorResponse('internal_error', 'An unexpected error occurred.', { status: 500 }),
    )
    renderRecovery()
    await screen.findByTestId('route-recovery')

    await user.type(usernameField(), 'ada')
    await user.click(submit())

    expect(await screen.findByText('An unexpected error occurred.')).toBeInTheDocument()
    expect(banner()).toHaveTextContent('An unexpected error occurred.')
    // Recovery has no 409 and therefore no `details.field` to route on: every failure is
    // the banner, and the field's own slot stays empty.
    expect(usernameMessage()).toBeEmptyDOMElement()
    expect(confirmation()).toBeEmptyDOMElement()
    expect(screen.getByRole('button', { name: 'Try Again' })).toBeInTheDocument()
  })

  it('reports a transport failure the same way, because it is the same ApiError', async () => {
    // CLAUDE.md §5: the five client-side codes are disjoint from the backend's, so one
    // surface handles both origins and nobody writes `if (!err.response)`.
    const user = userEvent.setup()
    mockRecovery(() => HttpResponse.error())
    renderRecovery()
    await screen.findByTestId('route-recovery')

    await user.type(usernameField(), 'ada')
    await user.click(submit())

    expect(await screen.findByText('Could not reach the Anvex API.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try Again' })).toBeInTheDocument()
  })

  it('keeps what the user typed', async () => {
    // The old page had an effect that wiped the field whenever the global error changed, so
    // a failure the user could do nothing about also took away their username.
    const user = userEvent.setup()
    mockRecovery(() =>
      errorResponse('internal_error', 'An unexpected error occurred.', { status: 500 }),
    )
    renderRecovery()
    await screen.findByTestId('route-recovery')

    await user.type(usernameField(), 'ada')
    await user.click(submit())
    await screen.findByText('An unexpected error occurred.')

    expect(usernameField()).toHaveValue('ada')
  })

  it('does not send a second request while the first is in flight', async () => {
    let release
    const gate = new Promise((resolve) => {
      release = resolve
    })
    const user = userEvent.setup()
    const seen = mockRecovery(async () => {
      await gate
      return HttpResponse.json(ACCEPTED, { status: 202 })
    })
    renderRecovery()
    await screen.findByTestId('route-recovery')

    await user.type(usernameField(), 'ada')
    await user.click(submit())

    // Half one: the button is disabled, so a double click cannot resubmit.
    expect(await screen.findByRole('button', { name: 'Submitting…' })).toBeDisabled()
    await user.click(submit())

    // Half two: a form can be submitted without its button — Enter in a text field does it
    // in a real browser — so the handler's own `if (submitting) return` is the other guard,
    // and it fails differently.
    fireEvent.submit(page().querySelector('form'))

    // Long enough for a second request to have been recorded if one had gone out. An
    // immediate assertion would pass with the guard removed, because the second request
    // would still be in flight when it ran.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50))
    })
    expect(seen).toHaveLength(1)

    await act(async () => {
      release()
    })
  })

  /**
   * **There is no `request_cancelled` test here, and it is a reported gap rather than an
   * oversight** — the same one ANV-30 recorded for `SignUpPage`, for the same reason. The
   * branch exists (a cancellation is this component unmounting, so it clears nothing and
   * shows nothing), but nothing in this page passes an `AbortSignal` and MSW cannot make
   * axios raise `ERR_CANCELED`: it mocks the server, not the caller. `LoginPage` can test
   * its copy only because `login` arrives through `useAuth()` and a test can substitute a
   * rejecting stub. `vi.mock('@features/auth/api')` would take the transport, the
   * interceptors and the error mapping out of every other test in this file, and adding a
   * signal to the component for a test to observe is behaviour written for a test.
   */
})

// --------------------------------------------------- the real router and the real history

describe('with the whole application mounted', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('is where the login page’s Forgot Password link goes, and the submit stays there', async () => {
    // The one thing that is invisible from inside the component: whether the page moves the
    // user. This is the *browser* history, so unlike the memory-router tests above a
    // redirect would show up in `window.location` — which is where a real user would see
    // it, and the reason this test carries the clock as well as the memory-router one does.
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    mockRecovery()
    renderAppAt(LOGIN_ROUTE)
    await screen.findByTestId('route-login')

    await user.click(screen.getByRole('link', { name: 'Forgot Password' }))
    expect(await screen.findByTestId('route-recovery')).toBeInTheDocument()
    expect(appLocation()).toBe(RECOVERY_ROUTE)

    await user.type(usernameField(), 'ada')
    await user.click(submit())
    await screen.findByText(/password reset will be arranged/)

    await act(async () => {
      vi.advanceTimersByTime(30_000)
    })

    expect(appLocation()).toBe(RECOVERY_ROUTE)
    expect(screen.queryByTestId('route-login')).not.toBeInTheDocument()
  })
})
