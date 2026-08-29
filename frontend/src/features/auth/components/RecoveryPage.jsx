import { Link } from '@tanstack/react-router'
import { useId, useState } from 'react'

import { CLIENT_ERROR_CODES, toApiError } from '@lib/api'
import { LOGIN_ROUTE } from '@routes/paths'

import { requestRecovery } from '../api'

/**
 * The password-recovery page (ANV-31), ported from
 * `AverageInvestorWeb/src/components/authenticate/Recovery.jsx` (117 lines).
 *
 * It is ANV-29's five parts, unchanged — local state, a pure `validate` first, one `await`,
 * `catch (err)` branching on `err.code`, no navigation — so only what is *different* about
 * a recovery form is written out here.
 *
 * ---------------------------------------------------------------------------------------
 * ## The response is fixed, and this page does not read it
 *
 * `POST /v1/auth/recovery` answers **202 with the same body for every username**, existing
 * or not (CLAUDE.md §4's anti-enumeration rule). The old `/v1/recovery` answered
 * `404 "User not found with username: <x>"`, which made password recovery a free account
 * -enumeration API — anybody could confirm a username by watching the status code.
 *
 * So there is nothing to branch on, and this page goes one step further than "does not
 * branch": it **ignores the body entirely**. `requestRecovery(...)` is awaited for its
 * failure, not for its value; the confirmation below is a constant in this module. That
 * distinction is the whole design:
 *
 *  - rendering `response.message` would make the UI a function of the response, so a
 *    backend that ever regressed to echoing the username would leak it *through this page*
 *    without a line of frontend code changing;
 *  - rendering a constant makes "the same thing, every time" true **by construction**
 *    rather than by the server's continued good behaviour, which is the property the
 *    identical-UI test asserts.
 *
 * The same reasoning removes the username from the success view: the form is replaced by
 * the confirmation, so nothing on screen after a submit came from the user or from the
 * wire. That is not secrecy theatre — the user knows what they typed — it is what makes
 * two submissions of two different usernames produce byte-identical markup, which is the
 * only assertable form of "this page tells you nothing".
 *
 * ## There is no timed redirect, and that is a decision
 *
 * The old page set `setTimeout(() => navigate("/login", {replace: true}), 3000)` and
 * **never cleared it**, so it fired after unmount and yanked a user who had already
 * navigated somewhere else back to the login screen. Clearing it in an effect's teardown
 * would fix that bug, but the feature it fixes is not worth having:
 *
 *  - ANV-30's navigation exception is narrow — sign-up creates no session, so no guard
 *    could own the transition. Nothing here is a transition at all: recovery produces no
 *    session, changes no route's admissibility, and leaves the user exactly where they
 *    chose to be.
 *  - Three seconds is a guess about a reading speed. It moves the page out from under a
 *    screen-reader user in the middle of the `role="status"` announcement they are being
 *    read, and out from under anybody who is slow, distracted or looking at their phone.
 *  - The thing the redirect was for — "how do I get back to signing in" — is a link, and a
 *    link is available at every moment of the page's life instead of at second three.
 *
 * So the confirmation stays on screen until the user leaves, and `Back to Log In` is a
 * permanent `<Link>` (not the old `<p onClick={() => navigate("/login")}>`, which is
 * neither focusable nor keyboard-operable, and — ANV-28 — an in-app destination reached by
 * anything but a `<Link>` risks a document navigation that discards the access token).
 * There is no `setTimeout` in this file, and a test advances the clock past the old delay
 * to prove nothing moves.
 *
 * ## The cleanup effect is deleted, not repaired
 *
 * The old page's teardown called `setError(undefined)` on unmount, with
 * `[location.pathname, setError]` as its dependencies — and the old provider rebuilt
 * `setError` on every render, so a dependency that changed every render made that effect
 * *re-run on every render* rather than on unmount. The effect existed only because the
 * failure lived in a global provider that outlived the page.
 *
 * The fix is not a stable `setError`. It is that a failed recovery is **local state**
 * (ANV-26's rule: a bad request belongs beside the form, not in a ten-second global
 * banner), and local state is discarded when the component unmounts, for free. This
 * component has **no `useEffect` at all** — there is nothing left for one to do.
 *
 * ## ARIA
 *
 * The old markup had no `htmlFor` anywhere, so the username field had no accessible name;
 * its field message was a second `<label>` associated with nothing, and its form error was
 * a bare `<p>` inserted silently. Here: `htmlFor`/`id` on the control, `aria-invalid` and
 * `aria-describedby` when it fails, and message slots that are **rendered unconditionally
 * and left empty** — a live region has to be in the accessibility tree *before* its text
 * arrives. The confirmation region is `role="status"` (polite: an answer, not an
 * interruption) and lives *outside* the form, so replacing the form does not take the
 * region with it; the failure slot is `role="alert"`, as on every other form in the app.
 */
export default function RecoveryPage() {
  const [username, setUsername] = useState('')

  /** The message under the field; `''` means "nothing wrong with it". */
  const [usernameError, setUsernameError] = useState('')

  /** The failed request, as an `ApiError` — or `null`. Drives the banner and the button. */
  const [error, setError] = useState(null)

  const [submitting, setSubmitting] = useState(false)

  /** True once the API has accepted a request. Replaces the form with the confirmation. */
  const [submitted, setSubmitted] = useState(false)

  const usernameId = useId()
  const usernameErrorId = useId()
  const formErrorId = useId()
  const confirmationId = useId()

  /** Typing into the field clears its message — the user is already fixing it. */
  const onUsernameChange = (event) => {
    setUsername(event.target.value)
    setUsernameError('')
  }

  async function onSubmit(event) {
    event.preventDefault()
    if (submitting) return

    // Trimmed for the check *and* for the request: a leading space pasted with a username
    // is not a different account, and the API would answer 202 to the padded string just
    // as cheerfully — with nothing arranged for anybody.
    const requested = username.trim()
    const problem = validateUsername(requested)
    setUsernameError(problem)
    // The early return is the point: an empty submit never reaches the network, and the
    // test asserts the request was not made rather than that a message appeared.
    if (problem !== '') return

    setSubmitting(true)
    setError(null)

    try {
      // The return value is deliberately unused — see the header. Awaited for its failure.
      await requestRecovery({ username: requested })
    } catch (caught) {
      const apiError = toApiError(caught)
      setSubmitting(false)
      // ANV-24/25's rule: a cancelled request is this component unmounting, not a failure,
      // so it clears nothing and shows nothing.
      if (apiError.code === CLIENT_ERROR_CODES.CANCELLED) return
      setError(apiError)
      return
    }

    // `submitting` is deliberately left set (ANV-29). Here it is belt and braces: the
    // confirmation replaces the form, so there is no button left to press twice.
    setSubmitted(true)
  }

  return (
    // `data-testid` matches the `RoutePlaceholder` this replaces, so every ANV-27 routing
    // test that asserts "/recovery resolved" keeps asserting it against the real page.
    <section data-testid="route-recovery" className="mt-6 flex flex-col items-center lg:mt-20">
      <h1 className="mb-5 font-gothic text-4xl font-xl">Password Recovery</h1>

      <div className="rounded-xl border border-neutral-200 p-4 sm:w-1/2 md:w-1/2 lg:w-1/3 xl:w-1/4 dark:border-neutral-700">
        <div className="mx-5">
          {/*
            Outside the form on purpose: the form is unmounted on success, and a live region
            that arrives with its own text is the case screen readers announce least
            reliably. `role="status"` rather than `alert` — this is the answer to something
            the user asked for, not an interruption.
          */}
          <div id={confirmationId} role="status" data-testid="recovery-confirmation">
            {submitted ? (
              <p className="py-8 text-center font-gothic font-medium text-neutral-900 dark:text-neutral-200">
                {RECOVERY_CONFIRMATION}
              </p>
            ) : null}
          </div>

          {submitted ? null : (
            /*
              `noValidate` with `required` still on the input: the attribute is what tells
              assistive tech the field is mandatory, while our own message — positioned,
              styled, dark-mode aware and wired to `aria-describedby` — is the one the user
              sees. A native validation bubble is none of those and vanishes on blur.
            */
            <form onSubmit={onSubmit} noValidate>
              <div className="flex flex-col py-2 text-neutral-900 dark:text-neutral-200">
                <div className="relative">
                  <label htmlFor={usernameId} className="mb-2 font-gothic font-medium">
                    {/*
                      "Username", not "Username or Email" as on the login page: `/v1/auth/
                      recovery` takes `RecoveryRequest.username` and nothing else, so
                      offering an address here would promise a lookup the API does not do.
                    */}
                    Username:
                  </label>
                  <span
                    id={usernameErrorId}
                    role="alert"
                    data-testid="recovery-username-error"
                    className="absolute right-1 bottom-0 font-gothic text-sm font-medium text-red-600"
                  >
                    {usernameError}
                  </span>
                </div>
                <input
                  id={usernameId}
                  name="username"
                  type="text"
                  autoComplete="username"
                  // A ceiling on a value that already *exists* — ANV-30's rule allows
                  // `maxLength` here and forbids it on a field being created. 50 is the
                  // `users.username` column, so a longer entry could not be an account.
                  maxLength={USERNAME_MAX_LENGTH}
                  required
                  aria-invalid={usernameError !== ''}
                  aria-describedby={usernameErrorId}
                  className="mt-2 w-full rounded-xl border border-neutral-300 bg-white p-2 dark:border-neutral-700 dark:bg-neutral-900"
                  value={username}
                  onChange={onUsernameChange}
                />
              </div>

              {/*
                The form-level failure — everything that is not the empty-field message
                lands here, because recovery has no 409 and therefore no per-field API
                error to route (contrast `SignUpPage`'s `details.field`).
              */}
              <div className="relative" id={formErrorId} role="alert" data-testid="recovery-error">
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
                {submitting ? 'Submitting…' : error === null ? 'Submit' : 'Try Again'}
              </button>
            </form>
          )}

          {/*
            Present in both states, which is what makes the removed auto-redirect a
            non-loss: the way back exists from the moment the page loads and does not
            expire.
          */}
          <p className="mb-2 text-center font-gothic text-sm font-medium">
            <Link to={LOGIN_ROUTE} className="hover:cursor-pointer hover:underline">
              Back to Log In
            </Link>
          </p>
        </div>
      </div>
    </section>
  )
}

/** The `users.username` column's width (`app/models/user.py`). */
const USERNAME_MAX_LENGTH = 50

/**
 * What a successful request says — a **constant**, never the API's `message` field.
 *
 * Phrased in the conditional, like the backend's own `RECOVERY_MESSAGE`, and for the same
 * reason: it must make no claim about whether an account was found. It also must not
 * promise an email, because nothing is delivered yet — `AuthService.recovery` logs
 * `delivered=False` behind a `TODO(ANV-mail)`. "Arranged", not "sent".
 */
const RECOVERY_CONFIRMATION =
  'If an account matches that username, a password reset will be arranged for it. ' +
  'Check the email address on the account.'

/**
 * What is wrong with the field, as a message string. Pure, so it is testable without a
 * render and without a network.
 *
 * Presence only. The API puts **no floor** on the length and no pattern on the shape
 * (`RecoveryRequest` is `min_length=1`), deliberately: the response is identical either
 * way, so validating the identifier any harder here would only tell an attacker which
 * guesses were worth making — the client-side twin of the rule the endpoint itself obeys.
 * The old message was `"Please Enter a Username"`; this one says what to do without the
 * please.
 *
 * **Not exported**: a `.jsx` module exporting both a component and a plain function loses
 * React Fast Refresh (`react-refresh/only-export-components`). It is exercised through the
 * rendered form, which is the stronger test anyway — what matters is that an empty submit
 * is refused *and never reaches the network*, and only a rendered test asserts the second
 * half.
 */
function validateUsername(username) {
  return username.trim() === '' ? 'Enter your username' : ''
}
