import { Link, useRouterState } from '@tanstack/react-router'
import { useId, useState } from 'react'

import { EyeIcon, EyeSlashIcon } from '@components/ui/icons'
import { useAuth } from '@hooks/useAuth'
import { CLIENT_ERROR_CODES, toApiError } from '@lib/api'
import { RECOVERY_ROUTE, SIGNUP_ROUTE } from '@routes/paths'

import { readRememberedUsername, rememberUsername } from '../authStorage'
import { readSignUpHandoff } from '../handoff'

/**
 * The login page (ANV-29), ported from
 * `AverageInvestorWeb/src/components/authenticate/Login.jsx` (192 lines).
 *
 * It is also **the pattern every other form page in this app copies** — ANV-30 (sign up)
 * and ANV-31 (recovery) are the same five parts in the same order, so the reasoning is
 * written out here once rather than three times:
 *
 *  1. **local state, not a store.** A half-typed form is not application state; nothing
 *     outside this component can see it and nothing needs to.
 *  2. **`validate()` first, then one `await`.** Validation is a pure function of the form
 *     returning a `{field: message}` map, so an empty submit never reaches the network and
 *     the messages are testable without one.
 *  3. **`catch (err)` branches on `err.code`, never on `err.message`** (CLAUDE.md §4: the
 *     code is the contract, the message is prose that gets reworded). `err.message` is what
 *     gets *displayed*.
 *  4. **no navigation.** See below.
 *  5. **every field is labelled, described and marked invalid** — see the ARIA section.
 *
 * ---------------------------------------------------------------------------------------
 * ## There is no navigation code in this file, and that is the point
 *
 * ANV-27 put "where does a signed-in user go" in exactly one place: `/login`'s own
 * `beforeLoad` (`redirectIfAuthenticated`). A successful `login()` flips `isAuthenticated`,
 * `App` calls `router.invalidate()`, the guard re-runs and sends the user to the sanitised
 * `redirect` search param — or to `DEFAULT_AUTHENTICATED_ROUTE` when there is not one. The
 * old page instead hardcoded `navigate("/research")` in an effect watching the response,
 * while `RequireAuth` separately remembered somewhere else; the two disagreed, and a deep
 * link into `/portfolio` still landed on `/research`.
 *
 * So this component does not import `useNavigate`, does not know `DEFAULT_AUTHENTICATED_ROUTE`
 * exists, and reads `redirect` nowhere. The `<Link>`s to `/signup` and `/recovery` are
 * ordinary in-app destinations, not decisions about where a login lands — and they are
 * `<Link>`s rather than the old page's `onClick={() => navigate(...)}` on a `<p>`, which
 * was a non-focusable, non-keyboard-operable element pretending to be a link.
 *
 * ## What the port deliberately does *not* preserve
 *
 *  - **`localStorage.setItem("pass", JSON.stringify(password))`.** The old "remember me"
 *    persisted the user's **plaintext password**, forever, and re-read it into the password
 *    field on every visit. `authStorage.js` offers no way to do that; the checkbox here
 *    persists the username and nothing else, and there is no password prefill.
 *  - **The `rememberMe` localStorage key.** The checkbox's state is *derived* from whether
 *    a username is remembered, so there is no third key to fall out of step with the first
 *    two and `AUTH_STORAGE_KEYS` stays the complete list of what auth persists (ANV-26).
 *  - **Clearing the form when the box is unticked.** The old effect wiped both fields,
 *    which throws away what the user has typed to answer a question about the *next* visit.
 *    Unticking forgets the stored username immediately — that half is worth keeping, because
 *    "forget me" should not require a successful login to take effect — and leaves the
 *    inputs alone.
 *  - **`autoComplete={rememberMe ? "current-password" : "off"}`.** `off` on a password field
 *    is a request that browsers and password managers largely ignore, and to the extent it
 *    works it breaks the managers that make long passwords possible. It is
 *    `current-password`, always.
 *
 * ## ARIA — what the old page had, and what it needed
 *
 * The old markup had **no `htmlFor` on any label**, so neither field had an accessible name
 * at all: a screen reader announced "edit text, blank". Its per-field messages were a
 * *second* `<label>` absolutely positioned at the end of the label row, associated with
 * nothing, and its form-level error was a bare `<p>` — inserted into the page silently, so
 * a user who could not see it was told nothing about why their password had not been
 * accepted. Four things fix that, and each has a test:
 *
 *  - `htmlFor`/`id` on every control, so the label names it and clicking the label focuses it;
 *  - `aria-invalid` on a field whose validation failed;
 *  - `aria-describedby` pointing at that field's message, so the reason is read out *with*
 *    the field rather than needing to be hunted for;
 *  - `role="alert"` on all three message slots, which are **rendered unconditionally and
 *    left empty**. A live region has to exist in the accessibility tree *before* its text
 *    arrives; inserting the region and the text together is the case screen readers are
 *    least reliable about announcing. Empty, they are invisible and take no space.
 *
 * The visibility toggle is a real `<button type="button">` with an `aria-label` that says
 * what pressing it does (ANV-28's rule for the theme switcher, same reason) and an
 * `aria-pressed` that says what state it is in. The old page put `onClick` on the `<svg>`
 * itself: not focusable, not operable from a keyboard, and invisible to assistive tech
 * because the icon was `aria-hidden`.
 */
export default function LoginPage() {
  const { login } = useAuth()

  /**
   * The credentials `/signup` handed over, read **once**.
   *
   * In a `useState` initialiser rather than an effect, for the reason the old page's three
   * competing effects demonstrate: they raced. `rememberMe` seeded the fields on mount, the
   * sign-up hand-off overwrote them when `location.state` changed, and both could land after
   * the user had started typing. Here the initial value is computed once, in priority order,
   * and after that the form belongs to the user.
   */
  // The **raw** location state, not `readSignUpHandoff(...)` of it. `useRouterState`'s
  // `select` result is compared to decide whether to re-render, so selecting a freshly
  // built object would make every store notification look like a change.
  const locationState = useRouterState({ select: (state) => state.location.state })

  const [form, setForm] = useState(() => {
    const handoff = readSignUpHandoff(locationState)
    return {
      // The hand-off wins over the remembered username: it is what the user chose seconds
      // ago, on a page they reached deliberately.
      username: handoff?.username || readRememberedUsername() || '',
      password: handoff?.password || '',
    }
  })

  /** `{username, password}` of message strings; `''` means "no problem with this field". */
  const [fieldErrors, setFieldErrors] = useState(EMPTY_FIELD_ERRORS)

  /** The failed sign-in, as an `ApiError` — or `null`. Drives the banner and the button. */
  const [error, setError] = useState(null)

  const [submitting, setSubmitting] = useState(false)
  const [passwordVisible, setPasswordVisible] = useState(false)

  /**
   * "Remember me" is on when there *is* a remembered username.
   *
   * Derived rather than stored, so the checkbox cannot claim to be remembering a username
   * that is not there (the old app kept a separate `rememberMe` key and the two drifted the
   * first time storage was cleared by hand).
   */
  const [rememberMe, setRememberMe] = useState(() => readRememberedUsername() !== null)

  const usernameId = useId()
  const passwordId = useId()
  const usernameErrorId = useId()
  const passwordErrorId = useId()
  const formErrorId = useId()

  /** Update one field and clear its message — the user is already fixing it. */
  const setField = (field) => (event) => {
    const { value } = event.target
    setForm((previous) => ({ ...previous, [field]: value }))
    setFieldErrors((previous) => (previous[field] === '' ? previous : { ...previous, [field]: '' }))
  }

  /**
   * Ticking the box promises nothing until the credentials are known to be good; unticking
   * takes effect **now**. A user who unticks and walks away has asked to be forgotten, and
   * making that conditional on a successful sign-in would be the wrong way round.
   */
  const onRememberChange = (event) => {
    const next = event.target.checked
    setRememberMe(next)
    if (!next) rememberUsername(null)
  }

  async function onSubmit(event) {
    event.preventDefault()
    if (submitting) return

    // Trimmed for the check *and* for the request: a leading space pasted along with an
    // email address is not a different account, and letting it through turns a typo into
    // "incorrect username or password".
    const username = form.username.trim()
    const problems = validateCredentials({ username, password: form.password })
    setFieldErrors(problems)
    if (problems.username !== '' || problems.password !== '') return

    setSubmitting(true)
    setError(null)

    try {
      await login({ username, password: form.password })
      rememberUsername(rememberMe ? username : null)
      // No navigation, deliberately — see the header. `isAuthenticated` has just flipped,
      // and `/login`'s guard is already on its way to unmount this component, which is why
      // `submitting` is never cleared on the success path: the button must not become
      // pressable again during the bounce.
    } catch (caught) {
      const apiError = toApiError(caught)
      setSubmitting(false)
      // ANV-24/25's rule: a cancelled request is this component unmounting, not a failure,
      // and swallowing it means changing nothing rather than clearing what is on screen.
      if (apiError.code === CLIENT_ERROR_CODES.CANCELLED) return
      setError(apiError)
    }
  }

  return (
    // `data-testid` matches the `RoutePlaceholder` this replaces (`route-` + the title,
    // lowercased), so every ANV-27/28 routing test that asserts "/login resolved" keeps
    // asserting it against the real page.
    <section data-testid="route-login" className="mt-6 flex flex-col items-center lg:mt-20">
      <h1 className="mb-5 font-gothic text-4xl font-xl">Log In</h1>

      <div className="rounded-xl border border-neutral-200 p-4 sm:w-1/2 md:w-1/2 lg:w-1/3 xl:w-1/4 dark:border-neutral-700">
        {/*
          `noValidate` with `required` still on the inputs: the attribute is what tells
          assistive tech the field is mandatory, while our own messages — which are
          positioned, styled, dark-mode aware and wired to `aria-describedby` — are the ones
          the user sees. A native validation bubble is none of those and vanishes on blur.
        */}
        <form className="mx-5" onSubmit={onSubmit} noValidate>
          <div className="flex flex-col py-2 text-neutral-900 dark:text-neutral-200">
            <div className="relative">
              <label htmlFor={usernameId} className="mb-2 font-gothic font-medium">
                {/* One field, either credential — CLAUDE.md §4: the API's login accepts a
                    username or an email, and the old label said only "Username", which is
                    how a user with an email account concludes they have not got one. */}
                Username or Email:
              </label>
              <span
                id={usernameErrorId}
                role="alert"
                data-testid="login-username-error"
                className="absolute right-1 bottom-0 font-gothic text-sm font-medium text-red-600"
              >
                {fieldErrors.username}
              </span>
            </div>
            <input
              id={usernameId}
              name="username"
              type="text"
              autoComplete="username"
              maxLength={50}
              required
              aria-invalid={fieldErrors.username !== ''}
              aria-describedby={usernameErrorId}
              className="mt-2 w-full rounded-xl border border-neutral-300 bg-white p-2 dark:border-neutral-700 dark:bg-neutral-900"
              value={form.username}
              onChange={setField('username')}
            />
          </div>

          <div className="flex flex-col py-2 text-neutral-900 dark:text-neutral-200">
            <div className="relative">
              <label htmlFor={passwordId} className="mb-2 font-gothic font-medium">
                Password:
              </label>
              <span
                id={passwordErrorId}
                role="alert"
                data-testid="login-password-error"
                className="absolute right-1 bottom-0 font-gothic text-sm font-medium text-red-600"
              >
                {fieldErrors.password}
              </span>
            </div>
            <div className="relative">
              <input
                id={passwordId}
                name="password"
                type={passwordVisible ? 'text' : 'password'}
                autoComplete="current-password"
                required
                aria-invalid={fieldErrors.password !== ''}
                aria-describedby={passwordErrorId}
                className="mt-2 w-full rounded-xl border border-neutral-300 bg-white p-2 pr-10 dark:border-neutral-700 dark:bg-neutral-900"
                value={form.password}
                onChange={setField('password')}
              />
              <button
                type="button"
                onClick={() => setPasswordVisible((visible) => !visible)}
                // The name says what pressing it *does* and changes with the state, so the
                // change is announced; `aria-pressed` says which state it is in. The old
                // page hung `onClick` on the `<svg>`, which no keyboard can reach.
                aria-label={passwordVisible ? 'Hide password' : 'Show password'}
                aria-pressed={passwordVisible}
                aria-controls={passwordId}
                className="absolute top-1 right-1 text-brand-600 dark:text-brand-400"
              >
                {passwordVisible ? (
                  <EyeIcon className="h-12 w-6 pr-1" />
                ) : (
                  <EyeSlashIcon className="h-12 w-6 pr-1" />
                )}
              </button>
            </div>
          </div>

          <div className="flex justify-between py-2 text-neutral-900 dark:text-neutral-200">
            <label className="flex items-center font-gothic font-medium hover:cursor-pointer">
              <input
                className="mr-2"
                type="checkbox"
                checked={rememberMe}
                onChange={onRememberChange}
              />
              Remember Me
            </label>
            {/*
              A `<Link>`, not the old `<p onClick={() => navigate("/recovery")}>`. Two
              separate defects there: a paragraph is not focusable or operable by keyboard
              and is announced as text, and ANV-28's rule — an in-app destination reached by
              anything other than `<Link>` risks a document navigation, which discards the
              in-memory access token.
            */}
            <Link
              to={RECOVERY_ROUTE}
              className="ml-5 font-gothic font-medium hover:cursor-pointer hover:underline"
            >
              Forgot Password
            </Link>
          </div>

          {/*
            The form-level failure. Present in the DOM at all times and empty when there is
            nothing to say — a `role="alert"` region inserted *together with* its text is the
            case screen readers handle least consistently, and the old page rendered the
            whole `<p>` conditionally with no role at all.
          */}
          <div
            className="relative"
            id={formErrorId}
            role="alert"
            data-testid="login-error"
          >
            {error === null ? null : (
              <p className="text-center font-gothic text-sm font-demi text-red-600">
                {error.message}
              </p>
            )}
          </div>

          <button
            type="submit"
            disabled={submitting}
            aria-describedby={formErrorId}
            className="my-5 w-full rounded-xl border bg-gradient-to-r from-brand-500 to-brand-700 py-2 font-gothic font-demi text-white hover:opacity-90 hover:underline disabled:cursor-not-allowed disabled:opacity-60 dark:from-brand-400 dark:to-brand-600"
          >
            {submitting ? 'Signing In…' : error === null ? 'Log In' : 'Try Again'}
          </button>

          <p className="mb-2 text-center font-gothic text-sm font-medium">
            <Link to={SIGNUP_ROUTE} className="hover:cursor-pointer hover:underline">
              {"Don't Have an Account? Sign Up Now!"}
            </Link>
          </p>
        </form>
      </div>
    </section>
  )
}

/** The "nothing is wrong" map, frozen so a `setFieldErrors(EMPTY_FIELD_ERRORS)` cannot alias. */
const EMPTY_FIELD_ERRORS = Object.freeze({ username: '', password: '' })

/**
 * What is wrong with this form, as a `{field: message}` map. Pure, so it is testable
 * without a render and without a network.
 *
 * The messages replace the old page's `"Invalid Input"` (for a *missing* username, which is
 * neither what happened nor a hint at what to do) and say which field and what to do about
 * it. They are deliberately about *presence only*: guessing at a password policy here would
 * put a second, drifting copy of the backend's rules in the browser, and refusing to send a
 * credential the server would have accepted is worse than a round trip.
 *
 * **Not exported**, on purpose: a `.jsx` module exporting both a component and a plain
 * function loses React Fast Refresh (`react-refresh/only-export-components` — the rule
 * ANV-25 and ANV-27 both hit). It is exercised through the rendered form, which is the
 * stronger test anyway: what matters is that an empty submit is refused **and never reaches
 * the network**, and only a rendered test can assert the second half.
 */
function validateCredentials({ username, password }) {
  return {
    username: username.trim() === '' ? 'Enter your username or email' : '',
    password: password === '' ? 'Enter your password' : '',
  }
}
