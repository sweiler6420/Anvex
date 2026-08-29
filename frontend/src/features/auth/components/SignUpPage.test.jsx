import { RouterProvider, createMemoryHistory } from '@tanstack/react-router'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from '@/App'
import { USERS_PATH } from '@features/auth/api'
import { AUTH_STORAGE_KEYS } from '@features/auth/authStorage'
import { readSignUpHandoff } from '@features/auth/handoff'
import { apiUrl } from '@lib/env'
import { createAppRouter } from '@lib/router'
import { AuthContext } from '@providers/AuthContext'
import { ThemeProvider } from '@providers/ThemeProvider'
import { THEME_STORAGE_KEY } from '@providers/themeStorage'
import { SIGNUP_ROUTE } from '@routes/paths'
import { errorResponse } from '@test/msw/handlers'
import { server } from '@test/msw/server'

/**
 * The sign-up page (ANV-30).
 *
 * Two harnesses, the same split ANV-29 established:
 *
 *  - **`renderSignUp`** mounts the real router over a memory history with a stubbed
 *    `AuthContext` (the shell's header needs one). Unlike the login page there is no
 *    injectable operation to stand in for the network — `register` is imported directly by
 *    the component, which is the point of a feature `api.js` — so the seam is **MSW**, and
 *    every test installs a handler that *records* what it was sent. "An invalid submit
 *    never reaches the network" is then an assertion on an empty array rather than on the
 *    absence of a message, which is the half a message-only assertion misses.
 *  - **`renderAppAt`** mounts the whole application over the real browser history, because
 *    the two things this page is most likely to get wrong — *where the user lands with what
 *    in their hands* and *what is in storage* — are invisible from inside the component.
 *
 * Nothing here stubs `axios` or `fetch` (CLAUDE.md §5): the real client, its interceptors
 * and its error mapping are all under test, which is what makes the 409 assertions mean
 * something.
 */

// ---------------------------------------------------------------------------- harnesses

function renderSignUp(path = SIGNUP_ROUTE) {
  const history = createMemoryHistory({ initialEntries: [path] })
  const router = createAppRouter({ history })
  const auth = {
    isAuthenticated: false,
    login: vi.fn(),
    logout: vi.fn(),
    restore: vi.fn(),
  }

  render(
    <ThemeProvider>
      <AuthContext.Provider value={auth}>
        <RouterProvider router={router} context={{ auth }} />
      </AuthContext.Provider>
    </ThemeProvider>,
  )

  return { router, location: () => router.state.location }
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

const email = () => screen.getByLabelText('Email:')
const username = () => screen.getByLabelText('Username:')
const password = () => screen.getByLabelText('Password:')
const submit = () => screen.getByRole('button', { name: /Sign Up|Try Again|Signing Up/ })
const banner = () => screen.getByTestId('signup-error')
const emailMessage = () => screen.getByTestId('signup-email-error')
const usernameMessage = () => screen.getByTestId('signup-username-error')
const passwordMessage = () => screen.getByTestId('signup-password-error')
const passwordRules = () => screen.getByTestId('signup-password-rules')

/** A registration that satisfies every rule, so exactly one thing can be broken per test. */
const VALID = Object.freeze({
  email: 'ada@example.com',
  username: 'adalovelace',
  password: 'Hunter2!x',
})

/** What `POST /v1/users` answers with — the backend's `UserOut`, which carries no password. */
const CREATED_USER = {
  user_id: '3f1c4d2e-0000-4000-8000-000000000001',
  username: VALID.username,
  email: VALID.email,
  created_at: '2026-01-01T00:00:00Z',
}

/**
 * Record every registration attempt and answer 201.
 *
 * Installed by **every** test, including the ones that must not reach the network: the
 * assertion "the request was never made" needs a recorder that would have caught it.
 */
function mockRegister(respond = () => HttpResponse.json(CREATED_USER, { status: 201 })) {
  const seen = []
  server.use(
    http.post(apiUrl(USERS_PATH), async ({ request }) => {
      seen.push(await request.json())
      return respond()
    }),
  )
  return seen
}

async function fill(user, { email: address, username: name, password: secret } = VALID) {
  if (address !== '') await user.type(email(), address)
  if (name !== '') await user.type(username(), name)
  if (secret !== '') await user.type(password(), secret)
}

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
  it('renders under the shell at /signup, with all three fields named by their labels', async () => {
    // The old markup had no `htmlFor` on any label and no `id` on any input, so none of the
    // three fields had an accessible name. `getByLabelText` is what fails on that markup.
    renderSignUp()

    expect(await screen.findByTestId('route-sign-up')).toBeInTheDocument()
    expect(email()).toHaveAttribute('type', 'email')
    expect(email()).toHaveAttribute('autocomplete', 'email')
    expect(username()).toHaveAttribute('autocomplete', 'username')
    // `new-password`, so a manager offers to generate one rather than filling an old one in.
    expect(password()).toHaveAttribute('autocomplete', 'new-password')
    // Still inside the shell — the page adds no header of its own (ANV-28).
    expect(screen.getByRole('navigation', { name: 'Main' })).toBeInTheDocument()
  })

  it('hides the password to begin with, where the old page displayed it', async () => {
    // `useState(true)` in the old file: a sign-up form showed the credential being chosen
    // to everyone in the room until the user thought to hide it.
    renderSignUp()
    await screen.findByTestId('route-sign-up')

    expect(password()).toHaveAttribute('type', 'password')
    expect(screen.getByRole('button', { name: 'Show password' })).toHaveAttribute(
      'aria-pressed',
      'false',
    )
  })

  it('reveals and re-hides the password, and says which it will do', async () => {
    const user = userEvent.setup()
    renderSignUp()
    await screen.findByTestId('route-sign-up')

    await user.click(screen.getByRole('button', { name: 'Show password' }))

    expect(password()).toHaveAttribute('type', 'text')
    await user.click(screen.getByRole('button', { name: 'Hide password' }))
    expect(password()).toHaveAttribute('type', 'password')
  })

  it('has a visibility toggle that works from the keyboard, because it is a button', async () => {
    // ANV-29 proved this on the login page; the markup here is a second copy, so it gets a
    // second test. The old page hung `onClick` on an `aria-hidden` `<svg>`: no tab stop, no
    // role. `user.click` alone passes on that shim — `focus()` + `{Enter}` is what does not.
    const user = userEvent.setup()
    renderSignUp()
    await screen.findByTestId('route-sign-up')

    screen.getByRole('button', { name: 'Show password' }).focus()
    await user.keyboard('{Enter}')

    expect(password()).toHaveAttribute('type', 'text')
  })

  it('does not submit the form when the visibility toggle is pressed', async () => {
    // A `<button>` inside a `<form>` defaults to `type="submit"`.
    const user = userEvent.setup()
    const seen = mockRegister()
    renderSignUp()
    await screen.findByTestId('route-sign-up')

    await fill(user)
    await user.click(screen.getByRole('button', { name: 'Show password' }))

    expect(seen).toEqual([])
  })

  it('links back to the login page with <Link>, so the bundle is not reloaded', async () => {
    // The old page used `<p onClick={() => navigate("/login")}>` — not focusable, not
    // keyboard-operable, announced as text. An `href` assertion cannot tell a `<Link>` from
    // an `<a>`, so this clicks and asserts the router moved (ANV-28's rule).
    const user = userEvent.setup()
    const { location } = renderSignUp()
    await screen.findByTestId('route-sign-up')

    const form = screen.getByTestId('route-sign-up')
    await user.click(within(form).getByRole('link', { name: /Log In Now/ }))

    expect(await screen.findByTestId('route-login')).toBeInTheDocument()
    expect(location().pathname).toBe('/login')
  })
})

// ------------------------------------------------------------- the password requirements

describe('the password requirements', () => {
  it('are on the page from the start, with no interaction of any kind', async () => {
    // The old rules lived in a `react-power-tooltip` behind `onMouseOver`, *inside* the
    // `passwordError !== ""` branch — so they did not exist in the DOM until the user had
    // already been rejected, and then only for someone holding a mouse. Every `getByText`
    // below throws on that markup.
    renderSignUp()
    await screen.findByTestId('route-sign-up')

    const rules = within(passwordRules())
    expect(rules.getByText('At least 7 characters')).toBeVisible()
    expect(rules.getByText('At least 1 uppercase letter')).toBeVisible()
    expect(rules.getByText('At least 1 number')).toBeVisible()
    expect(rules.getByText('At least 1 symbol')).toBeVisible()
    // Four rules on screen, and — see the validation block below — four rules enforced.
    expect(rules.getAllByRole('listitem')).toHaveLength(4)
  })

  it('are reachable without a mouse, because they describe the field', async () => {
    // The accessibility claim, stated as the thing a keyboard user actually does: tab to
    // the password box and the rules are read out with it. No `mouseover` is fired anywhere
    // in this test, which is exactly why the old tooltip could not pass it.
    const user = userEvent.setup()
    renderSignUp()
    await screen.findByTestId('route-sign-up')

    password().focus()
    await user.keyboard('Hunter2!x')

    expect(password()).toHaveFocus()
    expect(password().getAttribute('aria-describedby')).toContain(passwordRules().id)
    expect(passwordRules()).toBeVisible()
    expect(passwordRules()).toHaveTextContent('At least 1 symbol')
  })

  it('stay put while the user types, because a description that moves is noise', async () => {
    // Deliberately static rather than a live tick-list: the element is the field's
    // description, and re-announcing it on every keystroke is worse than not helping.
    const user = userEvent.setup()
    renderSignUp()
    await screen.findByTestId('route-sign-up')

    const before = passwordRules().textContent
    await user.type(password(), 'Hunter2!x')

    expect(passwordRules().textContent).toBe(before)
  })
})

// --------------------------------------------------------------------------- validation

describe('validation', () => {
  /** Break exactly one thing, submit, and see what the form says about it. */
  async function submitWith(overrides) {
    const user = userEvent.setup()
    const seen = mockRegister()
    renderSignUp()
    await screen.findByTestId('route-sign-up')

    await fill(user, { ...VALID, ...overrides })
    await user.click(submit())
    return seen
  }

  it('refuses an empty submit, names all three fields, and never reaches the network', async () => {
    const seen = await submitWith({ email: '', username: '', password: '' })

    expect(emailMessage()).toHaveTextContent('Enter your email address')
    expect(usernameMessage()).toHaveTextContent('Enter a username')
    expect(passwordMessage()).toHaveTextContent('Enter a password')
    // The half a message-only assertion misses: a form that showed the messages *and*
    // registered anyway would pass the three lines above.
    expect(seen).toEqual([])
  })

  it('refuses an address that is not one', async () => {
    const seen = await submitWith({ email: 'ada@example' })

    expect(emailMessage()).toHaveTextContent('Enter a valid email address')
    expect(usernameMessage()).toBeEmptyDOMElement()
    expect(seen).toEqual([])
  })

  it('accepts an ordinary address without arguing about the rest of RFC 5322', async () => {
    // The local check is deliberately loose; the server's `EmailStr` is the authority. A
    // regex here that refused a real address would be unarguable from the user's side.
    // Asserted on what was **sent**, because acceptance navigates the page away — there is
    // no empty message slot left on screen to look at.
    const seen = await submitWith({ email: "ada.o'byrne+anvex@sub.example.co.uk" })

    await waitFor(() =>
      expect(seen).toEqual([
        {
          email: "ada.o'byrne+anvex@sub.example.co.uk",
          username: VALID.username,
          password: VALID.password,
        },
      ]),
    )
  })

  it('refuses a username under 7 characters', async () => {
    const seen = await submitWith({ username: 'ada' })

    expect(usernameMessage()).toHaveTextContent('Username must be 7 or more characters')
    expect(seen).toEqual([])
  })

  it('refuses a username over 50 characters, rather than truncating it silently', async () => {
    const seen = await submitWith({ username: 'a'.repeat(51) })

    expect(usernameMessage()).toHaveTextContent('Username must be 50 characters or fewer')
    expect(seen).toEqual([])
  })

  it('refuses a username that is the email address, whatever its case', async () => {
    // The old rule compared the *raw* username against the *raw* email while sending the
    // lowercased address, so `ADA@example.com` sailed through a rule it plainly broke.
    // Both sides differ in case from each other *and* from the normalised form, so the
    // test fails if either half of the comparison stops folding case.
    const seen = await submitWith({ email: 'Ada@Example.com', username: 'ADA@example.COM' })

    expect(usernameMessage()).toHaveTextContent('Username cannot be your email address')
    expect(seen).toEqual([])
  })

  it('refuses a password under 7 characters', async () => {
    const seen = await submitWith({ password: 'Ab1!' })

    expect(passwordMessage()).toHaveTextContent('Password needs 7 characters')
    expect(seen).toEqual([])
  })

  it('refuses a password with no uppercase letter', async () => {
    const seen = await submitWith({ password: 'hunter2!x' })

    expect(passwordMessage()).toHaveTextContent('Password needs an uppercase letter')
    expect(seen).toEqual([])
  })

  it('refuses a password with no number', async () => {
    const seen = await submitWith({ password: 'Hunterx!' })

    expect(passwordMessage()).toHaveTextContent('Password needs a number')
    expect(seen).toEqual([])
  })

  it('refuses a password with no symbol', async () => {
    // The rule the ticket calls out. It was *already* enforced by the old page — see the
    // component docstring: `minSybols` was a stray key and `validator`'s own `minSymbols`
    // default is 1 — so this asserts the rule survived the port, not that a hole was
    // plugged. What was actually broken is the two tests below.
    const seen = await submitWith({ password: 'Hunter2x' })

    expect(passwordMessage()).toHaveTextContent('Password needs a symbol')
    expect(seen).toEqual([])
  })

  it('names every unmet rule at once, not just the first', async () => {
    // `isStrongPassword` returns a boolean, so the old page could only ever say "Password
    // Must Obey Rules" — which tells a user nothing they can act on.
    const seen = await submitWith({ password: 'password' })

    expect(passwordMessage()).toHaveTextContent(
      'Password needs an uppercase letter, a number and a symbol',
    )
    expect(seen).toEqual([])
  })

  it('refuses a password over 72 characters, rather than truncating it silently', async () => {
    // No `maxLength` on the input, on purpose: truncating a pasted 80-character password
    // would create an account with a password the user does not have.
    const seen = await submitWith({ password: `A1!${'a'.repeat(70)}` })

    expect(passwordMessage()).toHaveTextContent('Password must be 72 characters or fewer')
    expect(seen).toEqual([])
  })

  it('accepts a password with no lowercase letter — the rule the tooltip never claimed', async () => {
    // **The defect being fixed.** `minLowerCase` was misspelled, so `validator`'s default
    // `minLowercase: 1` applied and the form enforced a fifth rule that appeared nowhere on
    // screen. `PASSWORD1!` meets all four listed rules and was refused with "Password Must
    // Obey Rules" beside a tooltip that gave no hint why.
    const seen = await submitWith({ password: 'PASSWORD1!' })

    await waitFor(() => expect(seen.map((body) => body.password)).toEqual(['PASSWORD1!']))
  })

  it('counts a non-ASCII letter as uppercase and a non-ASCII mark as a symbol', async () => {
    // `validator`'s `upperCaseRegex` is `/^[A-Z]$/` and its `symbolRegex` is a fixed ASCII
    // punctuation set, so `ÄÖÜÑÇÉ1€` had neither an uppercase letter nor a symbol as far as
    // the old check was concerned — while the tooltip said it had both. Not one ASCII
    // capital and not one listed symbol in it, on purpose: a password that happened to
    // contain either would pass this test against `[A-Z]` too.
    const seen = await submitWith({ password: 'ÄÖÜÑÇÉ1€' })

    await waitFor(() => expect(seen.map((body) => body.password)).toEqual(['ÄÖÜÑÇÉ1€']))
  })

  it('marks each offending field aria-invalid and describes it with its own message', async () => {
    // The old per-field message was a second `<label>` associated with nothing, so the
    // reason was on screen and absent from the accessibility tree.
    await submitWith({ email: '', username: '', password: '' })

    expect(email()).toHaveAttribute('aria-invalid', 'true')
    expect(email().getAttribute('aria-describedby')).toBe(emailMessage().id)
    expect(username()).toHaveAttribute('aria-invalid', 'true')
    expect(username().getAttribute('aria-describedby')).toBe(usernameMessage().id)
    expect(password()).toHaveAttribute('aria-invalid', 'true')
    // The password carries two descriptions: the standing rules and this failure.
    expect(password().getAttribute('aria-describedby')).toContain(passwordMessage().id)
    expect(password().getAttribute('aria-describedby')).toContain(passwordRules().id)
  })

  it('keeps the message slots in the DOM while they are empty, as live regions', async () => {
    // A `role="alert"` region inserted together with its text is the case screen readers
    // announce least reliably; the region has to be there first. Empty, it is invisible.
    renderSignUp()
    await screen.findByTestId('route-sign-up')

    for (const slot of [emailMessage(), usernameMessage(), passwordMessage(), banner()]) {
      expect(slot).toBeEmptyDOMElement()
      expect(slot).toHaveAttribute('role', 'alert')
    }
    expect(email()).toHaveAttribute('aria-invalid', 'false')
  })

  it('clears one field message as soon as the user starts fixing it', async () => {
    const user = userEvent.setup()
    mockRegister()
    renderSignUp()
    await screen.findByTestId('route-sign-up')

    await user.click(submit())
    expect(emailMessage()).toHaveTextContent('Enter your email address')

    await user.type(email(), 'a')

    expect(emailMessage()).toBeEmptyDOMElement()
    expect(email()).toHaveAttribute('aria-invalid', 'false')
    // The others are untouched — clearing one must not clear all three.
    expect(usernameMessage()).toHaveTextContent('Enter a username')
    expect(passwordMessage()).toHaveTextContent('Enter a password')
  })
})

// ----------------------------------------------------------------------------- the call

describe('submitting', () => {
  it('sends JSON with the address trimmed and lowercased and the username trimmed', async () => {
    const user = userEvent.setup()
    const seen = mockRegister()
    renderSignUp()
    await screen.findByTestId('route-sign-up')

    await fill(user, {
      email: '  Ada@Example.COM  ',
      username: '  adalovelace  ',
      password: ' Hunter2!x ',
    })
    await user.click(submit())

    // The password is **not** trimmed or case-folded — every character of it is the secret.
    await waitFor(() =>
      expect(seen).toEqual([
        { email: 'ada@example.com', username: 'adalovelace', password: ' Hunter2!x ' },
      ]),
    )
  })

  it('puts a duplicate email beside the email field, not in a banner', async () => {
    // CLAUDE.md §4: registration is the one endpoint allowed to say *which* field clashed,
    // and `details.field` is the machine-readable half. Branching on `code` + `details`,
    // never on the sentence.
    const user = userEvent.setup()
    mockRegister(() =>
      errorResponse('conflict', 'That email address is already registered.', {
        status: 409,
        details: { resource: 'user', field: 'email' },
      }),
    )
    renderSignUp()
    await screen.findByTestId('route-sign-up')

    await fill(user)
    await user.click(submit())

    expect(await screen.findByText('That email address is already registered.')).toBeInTheDocument()
    expect(emailMessage()).toHaveTextContent('That email address is already registered.')
    expect(email()).toHaveAttribute('aria-invalid', 'true')
    expect(usernameMessage()).toBeEmptyDOMElement()
    expect(banner()).toBeEmptyDOMElement()
  })

  it('puts a duplicate username beside the username field', async () => {
    const user = userEvent.setup()
    mockRegister(() =>
      errorResponse('conflict', 'That username is already taken.', {
        status: 409,
        details: { resource: 'user', field: 'username' },
      }),
    )
    renderSignUp()
    await screen.findByTestId('route-sign-up')

    await fill(user)
    await user.click(submit())

    expect(await screen.findByText('That username is already taken.')).toBeInTheDocument()
    expect(usernameMessage()).toHaveTextContent('That username is already taken.')
    expect(username()).toHaveAttribute('aria-invalid', 'true')
    expect(emailMessage()).toBeEmptyDOMElement()
    expect(banner()).toBeEmptyDOMElement()
  })

  it('routes a duplicate by details.field, not by what the message happens to say', async () => {
    // CLAUDE.md §4: **branch on `code`, never on `message`** — a message is prose somebody
    // will reword. This 409 names no field in its sentence at all, so an implementation
    // that matched on the text would have nothing to match and would fall through to the
    // banner. `details.field` still says exactly which box to mark.
    const user = userEvent.setup()
    mockRegister(() =>
      errorResponse('conflict', 'That is already taken.', {
        status: 409,
        details: { resource: 'user', field: 'email' },
      }),
    )
    renderSignUp()
    await screen.findByTestId('route-sign-up')

    await fill(user)
    await user.click(submit())

    await waitFor(() => expect(emailMessage()).toHaveTextContent('That is already taken.'))
    expect(email()).toHaveAttribute('aria-invalid', 'true')
    expect(banner()).toBeEmptyDOMElement()
  })

  it('falls back to the banner for a conflict on a field this form does not have', async () => {
    // Routing a message to a field that is not on screen would hide it completely.
    const user = userEvent.setup()
    mockRegister(() =>
      errorResponse('conflict', 'Something else is taken.', {
        status: 409,
        details: { resource: 'user', field: 'phone' },
      }),
    )
    renderSignUp()
    await screen.findByTestId('route-sign-up')

    await fill(user)
    await user.click(submit())

    expect(await screen.findByText('Something else is taken.')).toBeInTheDocument()
    expect(banner()).toHaveTextContent('Something else is taken.')
    expect(emailMessage()).toBeEmptyDOMElement()
    expect(usernameMessage()).toBeEmptyDOMElement()
  })

  it('shows any other failure in the banner and turns the button into "Try Again"', async () => {
    // `details.field` is only a routing instruction on a **409**. A 422 carrying the same
    // key means the client and the server disagree about what is valid, which is a defect
    // rather than something the user typed wrong, so it is reported whole rather than
    // annotated onto a box the user has no reason to doubt. The `field: 'email'` here is
    // what makes the `code === 'conflict'` check load-bearing.
    const user = userEvent.setup()
    mockRegister(() =>
      errorResponse('validation_error', 'That email address is not acceptable.', {
        status: 422,
        details: { field: 'email' },
      }),
    )
    renderSignUp()
    await screen.findByTestId('route-sign-up')

    await fill(user)
    await user.click(submit())

    expect(await screen.findByText('That email address is not acceptable.')).toBeInTheDocument()
    expect(banner()).toHaveTextContent('That email address is not acceptable.')
    expect(emailMessage()).toBeEmptyDOMElement()
    expect(screen.getByRole('button', { name: 'Try Again' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Sign Up' })).not.toBeInTheDocument()
  })

  it('reports a transport failure the same way, because it is the same ApiError', async () => {
    // CLAUDE.md §5: the five client-side codes are disjoint from the backend's, so one
    // surface handles both origins and nobody writes `if (!err.response)`.
    const user = userEvent.setup()
    mockRegister(() => HttpResponse.error())
    renderSignUp()
    await screen.findByTestId('route-sign-up')

    await fill(user)
    await user.click(submit())

    expect(await screen.findByText('Could not reach the Anvex API.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try Again' })).toBeInTheDocument()
  })

  it('keeps what the user typed when the attempt fails', async () => {
    // The old page had a second effect that wiped all three fields whenever the global
    // error changed, so one taken username threw away the address and the password too.
    const user = userEvent.setup()
    mockRegister(() =>
      errorResponse('conflict', 'That username is already taken.', {
        status: 409,
        details: { resource: 'user', field: 'username' },
      }),
    )
    renderSignUp()
    await screen.findByTestId('route-sign-up')

    await fill(user)
    await user.click(submit())
    await screen.findByText('That username is already taken.')

    expect(email()).toHaveValue(VALID.email)
    expect(username()).toHaveValue(VALID.username)
    expect(password()).toHaveValue(VALID.password)
  })

  it('does not send a second request while the first is in flight', async () => {
    let release
    const gate = new Promise((resolve) => {
      release = resolve
    })
    const user = userEvent.setup()
    const seen = mockRegister(async () => {
      await gate
      return HttpResponse.json(CREATED_USER, { status: 201 })
    })
    renderSignUp()
    await screen.findByTestId('route-sign-up')

    await fill(user)
    await user.click(submit())

    // Half one: the button is disabled, so a double click cannot resubmit.
    expect(await screen.findByRole('button', { name: 'Signing Up…' })).toBeDisabled()
    await user.click(submit())

    // Half two: a form can be submitted without its button — Enter in a text field does it
    // in a real browser — so the handler's own `if (submitting) return` is the other guard,
    // and it fails differently. `fireEvent.submit` is how a disabled button is bypassed.
    fireEvent.submit(screen.getByTestId('route-sign-up').querySelector('form'))

    // Long enough for a second request to have been recorded if one had gone out. An
    // immediate `expect(seen).toHaveLength(1)` would pass with the guard removed, because
    // the second request would still be in flight when it ran.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50))
    })
    expect(seen).toHaveLength(1)

    await act(async () => {
      release()
    })
  })

  /**
   * **There is no `request_cancelled` test here, and that is a reported gap rather than an
   * oversight.** The branch exists (ANV-29's pattern: a cancellation is this component
   * unmounting, so it clears nothing and shows nothing), but nothing in this page passes an
   * `AbortSignal`, and MSW cannot make axios raise `ERR_CANCELED` — it mocks the server, not
   * the caller. `LoginPage` could test its copy because `login` arrives through
   * `useAuth()` and a test can substitute a rejecting stub; `register` is imported directly,
   * which is what a feature `api.js` is for. The alternatives were both worse than an
   * honest gap: `vi.mock('@features/auth/api')` would take the transport, the interceptors
   * and the error mapping out of every 409 test in this file, and adding a signal to the
   * component for the sake of the test would be behaviour written for a test to observe.
   */
})

// ------------------------------------------------------ the hand-off, for real, to /login

describe('with the real router and a real request', () => {
  it('lands on /login with the username prefilled and the password field empty', async () => {
    const user = userEvent.setup()
    mockRegister()
    renderAppAt(SIGNUP_ROUTE)
    await screen.findByTestId('route-sign-up')

    await fill(user)
    await user.click(submit())

    expect(await screen.findByTestId('route-login')).toBeInTheDocument()
    expect(appLocation()).toBe('/login')
    expect(screen.getByLabelText('Username or Email:')).toHaveValue(VALID.username)
    expect(screen.getByLabelText('Password:')).toHaveValue('')
  })

  it('hands over the username and provably not the password', async () => {
    // The decision ANV-29 recorded and this ticket implements: a browser persists
    // session-history state to disk for tab restore, so a password handed this way can
    // outlive the tab. The assertion is on the **whole** state object and the **whole**
    // contents of storage — "we did not offer an API that stores it" is a far weaker claim.
    const user = userEvent.setup()
    mockRegister()
    renderAppAt(SIGNUP_ROUTE)
    await screen.findByTestId('route-sign-up')

    await fill(user)
    await user.click(submit())
    await screen.findByTestId('route-login')

    expect(readSignUpHandoff(window.history.state)).toEqual({
      username: VALID.username,
      password: '',
    })
    expect(JSON.stringify(window.history.state)).not.toContain(VALID.password)

    // Registration is not a session: nothing auth-related is persisted at all, and the only
    // key in storage is the one `ThemeProvider` owns.
    const stored = storageContents()
    expect(Object.keys(stored)).toEqual([THEME_STORAGE_KEY])
    for (const key of AUTH_STORAGE_KEYS) expect(stored[key]).toBeUndefined()
    expect(JSON.stringify(stored)).not.toContain(VALID.password)
  })

  it('replaces the sign-up entry, so Back does not return to a form for an account that exists', async () => {
    const user = userEvent.setup()
    mockRegister()
    renderAppAt(SIGNUP_ROUTE)
    await screen.findByTestId('route-sign-up')
    const entriesBefore = window.history.length

    await fill(user)
    await user.click(submit())
    await screen.findByTestId('route-login')

    expect(window.history.length).toBe(entriesBefore)
  })

  it('surfaces the backend’s own 409 envelope and stays on /signup', async () => {
    const user = userEvent.setup()
    server.use(
      http.post(apiUrl(USERS_PATH), () =>
        errorResponse('conflict', 'That email address is already registered.', {
          status: 409,
          details: { resource: 'user', field: 'email' },
        }),
      ),
    )
    renderAppAt(SIGNUP_ROUTE)
    await screen.findByTestId('route-sign-up')

    await fill(user)
    await user.click(submit())

    expect(await screen.findByText('That email address is already registered.')).toBeInTheDocument()
    expect(appLocation()).toBe('/signup')
    expect(screen.queryByTestId('route-login')).not.toBeInTheDocument()
  })
})
