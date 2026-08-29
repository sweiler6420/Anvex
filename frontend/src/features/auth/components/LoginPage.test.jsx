import { RouterProvider, createMemoryHistory } from '@tanstack/react-router'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from '@/App'
import { LOGIN_PATH } from '@features/auth/api'
import {
  REFRESH_TOKEN_KEY,
  REMEMBERED_USERNAME_KEY,
  rememberUsername,
} from '@features/auth/authStorage'
import { signUpHandoffState } from '@features/auth/handoff'
import { ApiError, CLIENT_ERROR_CODES, resetTokenStore } from '@lib/api'
import { apiUrl } from '@lib/env'
import { AuthContext } from '@providers/AuthContext'
import { ThemeProvider } from '@providers/ThemeProvider'
import { THEME_STORAGE_KEY } from '@providers/themeStorage'
import { createAppRouter } from '@lib/router'
import { LOGIN_ROUTE } from '@routes/paths'
import { errorResponse } from '@test/msw/handlers'
import { server } from '@test/msw/server'

/**
 * The login page (ANV-29).
 *
 * Two harnesses, and the split is deliberate:
 *
 *  - **`renderLogin`** mounts the real router with a *stubbed* session (ANV-28's `renderAt`,
 *    copied), so `login` is a `vi.fn()` a test can make resolve, reject or hang. That is
 *    what makes "an empty submit never reaches the network" and "a rejection becomes a
 *    banner" assertable without inventing a server.
 *  - **`renderAppAt`** mounts the whole application — the real `AuthProvider`, the real
 *    token store, MSW answering `POST /v1/auth/login` — because the two claims this page
 *    is most likely to get wrong are *where the user lands* and *what ends up in
 *    `localStorage`*, and neither is visible from inside the component.
 */

// ---------------------------------------------------------------------------- harnesses

function renderLogin(
  path = LOGIN_ROUTE,
  { login = vi.fn().mockResolvedValue(undefined), state } = {},
) {
  const history = createMemoryHistory({ initialEntries: [path] })
  // The location *state* — where a sign-up hand-off travels. `createMemoryHistory` takes
  // plain hrefs, so the entry is rewritten with its state before the router reads it,
  // which is the same history entry ANV-30's `navigate({ to, state })` would produce.
  if (state !== undefined) {
    history.replace(path, state)
    history.flush()
  }
  const router = createAppRouter({ history })
  const auth = { isAuthenticated: false, login, logout: vi.fn(), restore: vi.fn() }

  render(
    <ThemeProvider>
      <AuthContext.Provider value={auth}>
        <RouterProvider router={router} context={{ auth }} />
      </AuthContext.Provider>
    </ThemeProvider>,
  )

  return { router, login, location: () => router.state.location }
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

/** Every key/value pair currently in `localStorage`. ANV-26's proof technique. */
function storageContents() {
  return Object.fromEntries(
    Array.from({ length: window.localStorage.length }, (_, index) => {
      const key = window.localStorage.key(index)
      return [key, window.localStorage.getItem(key)]
    }),
  )
}

const identifier = () => screen.getByLabelText('Username or Email:')
const password = () => screen.getByLabelText('Password:')
const submit = () => screen.getByRole('button', { name: /Log In|Try Again|Signing In/ })
const banner = () => screen.getByTestId('login-error')
const usernameMessage = () => screen.getByTestId('login-username-error')
const passwordMessage = () => screen.getByTestId('login-password-error')

/** The API's answer to good credentials. */
const TOKEN_PAIR = { access_token: 'access-1', refresh_token: 'refresh-1', token_type: 'bearer' }

function mockLoginSuccess() {
  const seen = []
  server.use(
    http.post(apiUrl(LOGIN_PATH), async ({ request }) => {
      seen.push(Object.fromEntries(new URLSearchParams(await request.text())))
      return HttpResponse.json(TOKEN_PAIR)
    }),
  )
  return seen
}

beforeEach(() => {
  window.localStorage.clear()
  window.history.replaceState(null, '', '/')
  document.documentElement.className = ''
})

afterEach(() => {
  resetTokenStore()
  window.localStorage.clear()
})

// -------------------------------------------------------------------------------- shape

describe('the form', () => {
  it('renders under the shell at /login, with both fields named by their labels', async () => {
    // The old markup had no `htmlFor` on either label and no `id` on either input, so
    // neither field had an accessible name at all — a screen reader announced "edit text,
    // blank" twice. `getByLabelText` is the assertion that fails on that markup.
    renderLogin()

    expect(await screen.findByTestId('route-login')).toBeInTheDocument()
    expect(identifier()).toBeInTheDocument()
    expect(password()).toHaveAttribute('type', 'password')
    // One field for either credential: the API's login accepts a username or an email.
    expect(identifier()).toHaveAttribute('autocomplete', 'username')
    expect(password()).toHaveAttribute('autocomplete', 'current-password')
    // Still inside the shell — the page adds no header of its own (ANV-28).
    expect(screen.getByRole('navigation', { name: 'Main' })).toBeInTheDocument()
  })

  it('links onward with <Link>, so following one keeps the in-memory access token', async () => {
    // The old page used `<p onClick={() => navigate("/signup")}>` — not focusable, not
    // keyboard-operable, announced as text. An `href` assertion cannot tell a `<Link>` from
    // an `<a>`, so this clicks and asserts the router moved (ANV-28's rule).
    const user = userEvent.setup()
    const { location } = renderLogin()
    await screen.findByTestId('route-login')

    const form = screen.getByTestId('route-login')
    expect(within(form).getByRole('link', { name: 'Forgot Password' })).toHaveAttribute(
      'href',
      '/recovery',
    )

    await user.click(within(form).getByRole('link', { name: /Sign Up Now/ }))

    expect(await screen.findByTestId('route-sign-up')).toBeInTheDocument()
    expect(location().pathname).toBe('/signup')
  })
})

// --------------------------------------------------------------------------- validation

describe('validation', () => {
  it('refuses an empty submit, names both fields, and never reaches the network', async () => {
    const user = userEvent.setup()
    const { login } = renderLogin()
    await screen.findByTestId('route-login')

    await user.click(submit())

    expect(usernameMessage()).toHaveTextContent('Enter your username or email')
    expect(passwordMessage()).toHaveTextContent('Enter your password')
    // The half a message-only assertion misses: a form that showed the messages *and*
    // submitted anyway would pass the two lines above.
    expect(login).not.toHaveBeenCalled()
  })

  it('complains about the password alone when only the identifier is filled', async () => {
    const user = userEvent.setup()
    const { login } = renderLogin()
    await screen.findByTestId('route-login')

    await user.type(identifier(), 'ada')
    await user.click(submit())

    expect(usernameMessage()).toBeEmptyDOMElement()
    expect(passwordMessage()).toHaveTextContent('Enter your password')
    expect(login).not.toHaveBeenCalled()
  })

  it('complains about the identifier alone when only the password is filled', async () => {
    const user = userEvent.setup()
    const { login } = renderLogin()
    await screen.findByTestId('route-login')

    await user.type(password(), 'hunter2')
    await user.click(submit())

    expect(usernameMessage()).toHaveTextContent('Enter your username or email')
    expect(passwordMessage()).toBeEmptyDOMElement()
    expect(login).not.toHaveBeenCalled()
  })

  it('treats a whitespace-only identifier as empty', async () => {
    const user = userEvent.setup()
    const { login } = renderLogin()
    await screen.findByTestId('route-login')

    await user.type(identifier(), '   ')
    await user.type(password(), 'hunter2')
    await user.click(submit())

    expect(usernameMessage()).toHaveTextContent('Enter your username or email')
    expect(login).not.toHaveBeenCalled()
  })

  it('marks the offending field aria-invalid and describes it with its own message', async () => {
    // The old page's message was a second `<label>` associated with nothing, so the reason
    // was on screen and absent from the accessibility tree.
    const user = userEvent.setup()
    renderLogin()
    await screen.findByTestId('route-login')

    await user.click(submit())

    expect(identifier()).toHaveAttribute('aria-invalid', 'true')
    expect(identifier().getAttribute('aria-describedby')).toBe(usernameMessage().id)
    expect(password()).toHaveAttribute('aria-invalid', 'true')
    expect(password().getAttribute('aria-describedby')).toBe(passwordMessage().id)
  })

  it('keeps the message slots in the DOM while they are empty, as live regions', async () => {
    // A `role="alert"` region inserted together with its text is the case screen readers
    // announce least reliably; the region has to be there first. Empty, it is invisible.
    renderLogin()
    await screen.findByTestId('route-login')

    expect(usernameMessage()).toBeEmptyDOMElement()
    expect(usernameMessage()).toHaveAttribute('role', 'alert')
    expect(banner()).toBeEmptyDOMElement()
    expect(banner()).toHaveAttribute('role', 'alert')
    expect(identifier()).toHaveAttribute('aria-invalid', 'false')
  })

  it('clears a field message as soon as the user starts fixing it', async () => {
    const user = userEvent.setup()
    renderLogin()
    await screen.findByTestId('route-login')

    await user.click(submit())
    expect(usernameMessage()).toHaveTextContent('Enter your username or email')

    await user.type(identifier(), 'a')

    expect(usernameMessage()).toBeEmptyDOMElement()
    expect(identifier()).toHaveAttribute('aria-invalid', 'false')
    // The other field is untouched — clearing one must not clear both.
    expect(passwordMessage()).toHaveTextContent('Enter your password')
  })
})

// ------------------------------------------------------------------------------ signing in

describe('submitting', () => {
  it('sends the identifier trimmed, and accepts an email as one', async () => {
    const user = userEvent.setup()
    const { login } = renderLogin()
    await screen.findByTestId('route-login')

    await user.type(identifier(), '  ada@example.com  ')
    await user.type(password(), ' hunter2 ')
    await user.click(submit())

    // The password is *not* trimmed — leading or trailing spaces are part of it.
    expect(login).toHaveBeenCalledWith({ username: 'ada@example.com', password: ' hunter2 ' })
  })

  it('shows the server’s message and turns the button into "Try Again"', async () => {
    const user = userEvent.setup()
    const login = vi.fn().mockRejectedValue(
      new ApiError({
        code: 'unauthorized',
        message: 'Incorrect username or password.',
        status: 401,
      }),
    )
    renderLogin(LOGIN_ROUTE, { login })
    await screen.findByTestId('route-login')

    await user.type(identifier(), 'ada')
    await user.type(password(), 'wrong')
    await user.click(submit())

    expect(await screen.findByText('Incorrect username or password.')).toBeInTheDocument()
    expect(banner()).toHaveTextContent('Incorrect username or password.')
    expect(screen.getByRole('button', { name: 'Try Again' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Log In' })).not.toBeInTheDocument()
  })

  it('reports a transport failure the same way, because it is the same ApiError', async () => {
    // CLAUDE.md §5: the five client-side codes are disjoint from the backend's, so one
    // surface handles both origins and nobody writes `if (!err.response)`.
    const user = userEvent.setup()
    const login = vi
      .fn()
      .mockRejectedValue(new ApiError({ code: CLIENT_ERROR_CODES.NETWORK, status: null }))
    renderLogin(LOGIN_ROUTE, { login })
    await screen.findByTestId('route-login')

    await user.type(identifier(), 'ada')
    await user.type(password(), 'hunter2')
    await user.click(submit())

    expect(await screen.findByText('Could not reach the Anvex API.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try Again' })).toBeInTheDocument()
  })

  it('swallows a cancelled request rather than accusing the user of anything', async () => {
    // ANV-24/25's rule: `request_cancelled` is a component unmounting, not a failure.
    const user = userEvent.setup()
    const login = vi
      .fn()
      .mockRejectedValue(new ApiError({ code: CLIENT_ERROR_CODES.CANCELLED, status: null }))
    renderLogin(LOGIN_ROUTE, { login })
    await screen.findByTestId('route-login')

    await user.type(identifier(), 'ada')
    await user.type(password(), 'hunter2')
    await user.click(submit())

    await waitFor(() => expect(screen.getByRole('button', { name: 'Log In' })).toBeEnabled())
    expect(banner()).toBeEmptyDOMElement()
  })

  it('does not send a second request while the first is in flight', async () => {
    let release
    const login = vi.fn().mockImplementation(() => new Promise((resolve) => (release = resolve)))
    const user = userEvent.setup()
    renderLogin(LOGIN_ROUTE, { login })
    await screen.findByTestId('route-login')

    await user.type(identifier(), 'ada')
    await user.type(password(), 'hunter2')
    await user.click(submit())

    expect(screen.getByRole('button', { name: 'Signing In…' })).toBeDisabled()
    await user.click(submit())
    expect(login).toHaveBeenCalledTimes(1)

    await act(async () => {
      release()
    })
  })
})

// ------------------------------------------------------------------- password visibility

describe('the visibility toggle', () => {
  it('reveals and re-hides the password, and says which it will do', async () => {
    const user = userEvent.setup()
    renderLogin()
    await screen.findByTestId('route-login')

    expect(password()).toHaveAttribute('type', 'password')

    await user.click(screen.getByRole('button', { name: 'Show password' }))

    expect(password()).toHaveAttribute('type', 'text')
    const hide = screen.getByRole('button', { name: 'Hide password' })
    expect(hide).toHaveAttribute('aria-pressed', 'true')

    await user.click(hide)

    expect(password()).toHaveAttribute('type', 'password')
    expect(screen.getByRole('button', { name: 'Show password' })).toHaveAttribute(
      'aria-pressed',
      'false',
    )
  })

  it('is operable from the keyboard, because it is a button and not an SVG', async () => {
    // The old page put `onClick` on the `<svg>` itself: no tab stop, no role, `aria-hidden`,
    // so a keyboard user could not reveal their password at all. `user.click` alone would
    // pass on that markup — typing Enter into a focused element is what does not.
    const user = userEvent.setup()
    renderLogin()
    await screen.findByTestId('route-login')

    screen.getByRole('button', { name: 'Show password' }).focus()
    await user.keyboard('{Enter}')

    expect(password()).toHaveAttribute('type', 'text')
  })

  it('does not submit the form when pressed', async () => {
    // A `<button>` inside a `<form>` defaults to `type="submit"`.
    const user = userEvent.setup()
    const { login } = renderLogin()
    await screen.findByTestId('route-login')

    await user.type(identifier(), 'ada')
    await user.type(password(), 'hunter2')
    await user.click(screen.getByRole('button', { name: 'Show password' }))

    expect(login).not.toHaveBeenCalled()
  })
})

// -------------------------------------------------------------------------- remember me

describe('remember me', () => {
  it('is off with an empty identifier when nothing is remembered', async () => {
    renderLogin()
    await screen.findByTestId('route-login')

    expect(screen.getByRole('checkbox', { name: 'Remember Me' })).not.toBeChecked()
    expect(identifier()).toHaveValue('')
  })

  it('is on and prefilled when a username was remembered', async () => {
    rememberUsername('ada')

    renderLogin()
    await screen.findByTestId('route-login')

    expect(screen.getByRole('checkbox', { name: 'Remember Me' })).toBeChecked()
    expect(identifier()).toHaveValue('ada')
    // The bug being removed: there is no password to prefill, and no API that could store one.
    expect(password()).toHaveValue('')
  })

  it('forgets the username the moment the box is unticked', async () => {
    // Not on the next successful login: a user who unticks and walks away has asked to be
    // forgotten now.
    const user = userEvent.setup()
    rememberUsername('ada')
    renderLogin()
    await screen.findByTestId('route-login')

    await user.click(screen.getByRole('checkbox', { name: 'Remember Me' }))

    expect(window.localStorage.getItem(REMEMBERED_USERNAME_KEY)).toBeNull()
    // ...and it does not wipe what the user has typed, which the old effect did.
    expect(identifier()).toHaveValue('ada')
  })
})

// ------------------------------------------------------------------- the sign-up hand-off

describe('the sign-up hand-off', () => {
  /** What ANV-30 will do: arrive at /login carrying the credentials in router state. */
  async function handOff(credentials) {
    const harness = renderLogin(LOGIN_ROUTE, { state: signUpHandoffState(credentials) })
    await screen.findByTestId('route-login')
    return harness
  }

  it('arrives with both fields filled in', async () => {
    await handOff({ username: 'ada', password: 'correct-horse' })

    expect(identifier()).toHaveValue('ada')
    expect(password()).toHaveValue('correct-horse')
  })

  it('fills the identifier alone when the hand-off carries no password', async () => {
    await handOff({ username: 'ada' })

    expect(identifier()).toHaveValue('ada')
    expect(password()).toHaveValue('')
  })

  it('wins over the remembered username', async () => {
    // Somebody who has just created a *second* account must not be handed the first one's
    // name. The hand-off is what they chose seconds ago.
    rememberUsername('grace')

    await handOff({ username: 'ada', password: 'correct-horse' })

    expect(identifier()).toHaveValue('ada')
  })

  /**
   * **A test that was deleted for not discriminating.** It read
   *
   *     await handOff({username: 'ada', password: 'correct-horse'})
   *     expect(JSON.stringify(storageContents())).not.toContain('correct-horse')
   *
   * and it passed under *every* mutation tried, including reintroducing
   * `localStorage.setItem("pass", …)` — because nothing in it ever signs in, so the only
   * code that could write a password never runs. It also passed with the prefill removed
   * entirely, which is the definition of asserting nothing. The claim it was reaching for is
   * real and is made properly in "carries a handed-off password to the API and not to
   * storage" below, which signs in for real and therefore fails on both mutations.
   */
})

// ------------------------------------------------------ the real store, the real router

describe('with the real session and a real request', () => {
  it('lands the user where the redirect param says, not on the default', async () => {
    // `DEFAULT_AUTHENTICATED_ROUTE` is `/research`, so a page that hardcoded a destination
    // — as the old `Login.jsx` did with `navigate("/research")` — fails here.
    const user = userEvent.setup()
    mockLoginSuccess()
    renderAppAt('/login?redirect=%2Fportfolio')
    await screen.findByTestId('route-login')

    await user.type(identifier(), 'ada')
    await user.type(password(), 'correct-horse')
    await user.click(submit())

    expect(await screen.findByTestId('route-portfolio')).toBeInTheDocument()
    expect(appLocation()).toBe('/portfolio')
  })

  it('falls back to the default when there is no redirect param', async () => {
    const user = userEvent.setup()
    mockLoginSuccess()
    renderAppAt('/login')
    await screen.findByTestId('route-login')

    await user.type(identifier(), 'ada')
    await user.type(password(), 'correct-horse')
    await user.click(submit())

    expect(await screen.findByTestId('route-research')).toBeInTheDocument()
    expect(appLocation()).toBe('/research')
  })

  it('completes the round trip a guard started', async () => {
    // /portfolio refused -> /login?redirect=/portfolio -> sign in -> /portfolio. Every
    // decision about the destination is the guard's; the form contributes nothing.
    const user = userEvent.setup()
    mockLoginSuccess()
    renderAppAt('/portfolio')
    await screen.findByTestId('route-login')
    expect(appLocation()).toBe('/login?redirect=%2Fportfolio')

    await user.type(identifier(), 'ada')
    await user.type(password(), 'correct-horse')
    await user.click(submit())

    expect(await screen.findByTestId('route-portfolio')).toBeInTheDocument()
  })

  it('form-encodes the credentials the way the OAuth2 endpoint wants them', async () => {
    const user = userEvent.setup()
    const seen = mockLoginSuccess()
    renderAppAt('/login')
    await screen.findByTestId('route-login')

    await user.type(identifier(), 'ada@example.com')
    await user.type(password(), 'correct-horse')
    await user.click(submit())

    await screen.findByTestId('route-research')
    expect(seen).toEqual([{ username: 'ada@example.com', password: 'correct-horse' }])
  })

  it('remembers the username and provably not the password', async () => {
    // ANV-26's proof technique, applied to the checkbox that replaces
    // `localStorage.setItem("pass", JSON.stringify(password))`. The assertion is on the
    // **whole** of `localStorage` — every key and every value — because "we did not expose
    // an API that stores it" is a far weaker claim than "after a real sign-in, nothing in
    // storage is the password".
    const user = userEvent.setup()
    mockLoginSuccess()
    renderAppAt('/login')
    await screen.findByTestId('route-login')

    await user.click(screen.getByRole('checkbox', { name: 'Remember Me' }))
    await user.type(identifier(), 'ada')
    await user.type(password(), 'correct-horse')
    await user.click(submit())

    await screen.findByTestId('route-research')

    const stored = storageContents()
    expect(Object.keys(stored).sort()).toEqual(
      [REFRESH_TOKEN_KEY, REMEMBERED_USERNAME_KEY, THEME_STORAGE_KEY].sort(),
    )
    expect(stored[REMEMBERED_USERNAME_KEY]).toBe('ada')
    expect(stored[REFRESH_TOKEN_KEY]).toBe('refresh-1')
    // Neither the password nor the access token, in any key, under any encoding we use.
    const dump = JSON.stringify(stored)
    expect(dump).not.toContain('correct-horse')
    expect(dump).not.toContain('access-1')
  })

  it('stores nothing but the refresh token when the box is left unticked', async () => {
    const user = userEvent.setup()
    mockLoginSuccess()
    renderAppAt('/login')
    await screen.findByTestId('route-login')

    await user.type(identifier(), 'ada')
    await user.type(password(), 'correct-horse')
    await user.click(submit())

    await screen.findByTestId('route-research')

    expect(Object.keys(storageContents()).sort()).toEqual(
      [REFRESH_TOKEN_KEY, THEME_STORAGE_KEY].sort(),
    )
  })

  it('carries a handed-off password to the API and not to storage', async () => {
    // The replacement for a hand-off test that could not discriminate (see above). This one
    // arrives with ANV-30's credentials in history state — which is what `renderAppAt` gives
    // TanStack, since it reads the browser history it is handed — ticks "remember me", and
    // signs in for real. It fails if the prefill is dropped (the form is empty, validation
    // refuses, the sign-in never happens) *and* if the password is persisted.
    const user = userEvent.setup()
    const seen = mockLoginSuccess()
    // The TSR bookkeeping keys are part of the fixture, not decoration: `createBrowserHistory`
    // **overwrites** an entry's whole state on startup when it finds neither `key` nor
    // `__TSR_key` on it, so a hand-made entry without them arrives empty. A hand-off created
    // by a real `navigate({state})` already carries them (`assignKeyAndIndex` spreads the
    // caller's state), so this is the test rig catching up with the router, not a caveat for
    // ANV-30.
    window.history.replaceState(
      {
        ...signUpHandoffState({ username: 'ada', password: 'correct-horse' }),
        key: 'handoff',
        __TSR_key: 'handoff',
        __TSR_index: 0,
      },
      '',
      '/login',
    )
    render(
      <ThemeProvider>
        <App />
      </ThemeProvider>,
    )
    await screen.findByTestId('route-login')

    await user.click(screen.getByRole('checkbox', { name: 'Remember Me' }))
    await user.click(submit())

    await screen.findByTestId('route-research')
    expect(seen).toEqual([{ username: 'ada', password: 'correct-horse' }])
    expect(JSON.stringify(storageContents())).not.toContain('correct-horse')
    expect(window.localStorage.getItem(REMEMBERED_USERNAME_KEY)).toBe('ada')
  })

  it('surfaces the backend’s own error envelope and stays put', async () => {
    const user = userEvent.setup()
    server.use(
      http.post(apiUrl(LOGIN_PATH), () =>
        errorResponse('unauthorized', 'Incorrect username or password.', { status: 401 }),
      ),
    )
    renderAppAt('/login?redirect=%2Fportfolio')
    await screen.findByTestId('route-login')

    await user.type(identifier(), 'ada')
    await user.type(password(), 'wrong')
    await user.click(submit())

    expect(await screen.findByText('Incorrect username or password.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try Again' })).toBeInTheDocument()
    expect(appLocation()).toBe('/login?redirect=%2Fportfolio')
    // A failed sign-in is not a session, so it must not have written one.
    expect(window.localStorage.getItem(REFRESH_TOKEN_KEY)).toBeNull()
  })

  it('does not remember the username for a sign-in that failed', async () => {
    const user = userEvent.setup()
    server.use(
      http.post(apiUrl(LOGIN_PATH), () =>
        errorResponse('unauthorized', 'Incorrect username or password.', { status: 401 }),
      ),
    )
    renderAppAt('/login')
    await screen.findByTestId('route-login')

    await user.click(screen.getByRole('checkbox', { name: 'Remember Me' }))
    await user.type(identifier(), 'ada')
    await user.type(password(), 'wrong')
    await user.click(submit())

    await screen.findByRole('button', { name: 'Try Again' })
    expect(window.localStorage.getItem(REMEMBERED_USERNAME_KEY)).toBeNull()
  })
})

// --------------------------------------------------------------- the header, from here

describe('the header while the login page is on screen', () => {
  it('marks its own "Log In" link as the current page rather than hiding it', async () => {
    // ANV-28 left this open. The link stays: a nav that reshuffles itself per route is
    // harder to use than one that does not, and TanStack's `<Link>` already writes
    // `aria-current="page"` when it is the current route — so a screen reader announces
    // "Log In, link, current page" and nothing pretends to be an action it is not. The
    // decision is therefore "keep it, and check that it says so", which is this assertion.
    renderLogin()
    await screen.findByTestId('route-login')

    const actions = within(screen.getByTestId('header-desktop-actions'))
    expect(actions.getByRole('link', { name: 'Log In' })).toHaveAttribute(
      'aria-current',
      'page',
    )
    expect(actions.getByRole('link', { name: 'Create an Account' })).not.toHaveAttribute(
      'aria-current',
    )
  })
})
