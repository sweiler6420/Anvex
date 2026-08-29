import { Link, useNavigate } from '@tanstack/react-router'
import { useId, useState } from 'react'

import { EyeIcon, EyeSlashIcon } from '@components/ui/icons'
import { CLIENT_ERROR_CODES, toApiError } from '@lib/api'
import { LOGIN_ROUTE } from '@routes/paths'

import { register } from '../api'
import { signUpHandoffState } from '../handoff'

/**
 * The sign-up page (ANV-30), ported from
 * `AverageInvestorWeb/src/components/authenticate/SignUp.jsx` (188 lines).
 *
 * It is **ANV-29's five-part form pattern**, copied rather than re-derived: local
 * `useState` per field; a pure `validate()` called first with an early return, so an
 * invalid submit never reaches the network; one `await` on the feature's operation;
 * `catch` branching on `err.code`; message slots rendered unconditionally and left empty.
 * `LoginPage.jsx` is where that reasoning is written out. What follows is only what is
 * *different* here, and each difference is a decision.
 *
 * ---------------------------------------------------------------------------------------
 * ## This page navigates, and it is the one page that may
 *
 * ANV-27's rule — "a page contains no navigation code" — is about **where a signed-in user
 * goes**: that decision belongs to `/login`'s `beforeLoad`, because a guard is what runs on
 * every arrival, including the ones no form was involved in. Registration produces **no
 * session**: `POST /v1/users` returns a `UserOut`, not a token pair, so `isAuthenticated`
 * never flips, no guard re-runs, and nothing else in the application is in a position to
 * move the user. The hand-off to `/login` is therefore this component's own transition and
 * has to be written here.
 *
 * `replace: true`, so Back from the login page returns to wherever they came from rather
 * than to a filled-in sign-up form for an account that now exists.
 *
 * ## What travels to `/login`, and what deliberately does not
 *
 * `signUpHandoffState({ username })` — **the username only, never the password.** A browser
 * persists session-history state to disk so a crashed tab can be restored, so a password
 * handed over this way can outlive the tab that chose it. `handoff.js` left the `password`
 * half optional for exactly this decision; dropping it costs one field the user must retype
 * on a page where the password manager they just saved to will offer it anyway.
 *
 * The username handed over is **the one this form validated**, not the one the API echoed
 * back in its `UserOut`. They are the same value, and preferring the local one means the
 * hand-off has no dependency on the shape of a response body.
 *
 * ## The password rules are on the page, permanently, and that replaced a tooltip
 *
 * The old page listed the four rules in a `react-power-tooltip` shown from
 * `onMouseOver`/`onMouseLeave` on a `<label>`, rendered twice — once per theme — inside a
 * `{passwordError !== "" ? … : null}`. Three separate problems, and the third is the worst:
 *
 *  1. **Hover is not an interaction every user has.** A keyboard user cannot produce
 *     `mouseover`, and a touch user has no hover state at all, so the rules a form insists
 *     on were unreadable to both. Requirements that only some people can read are not
 *     requirements, they are a guessing game.
 *  2. **Two copies of one list.** The light and dark tooltips differed only in colours and
 *     a `fontWeight`, so the four rules were written out twice and could drift.
 *  3. **They appeared only *after* a rejection.** The tooltip lived inside the error
 *     branch, so the rules were invisible for as long as the user was actually choosing a
 *     password and became available only once they had already got it wrong — the moment
 *     they were least useful.
 *
 * So the rules are **always on screen**, below the field, and joined to the input with
 * `aria-describedby`, which is what makes a screen reader read them out *when the field is
 * focused* rather than leaving them to be discovered. A disclosure was considered and
 * rejected: it is the right pattern for content long enough to be in the way, and four
 * short lines are not — it would trade a hover nobody can perform for a click everybody
 * has to.
 *
 * They are deliberately **static**, not a live tick-list that updates per keystroke. The
 * element is a *description* of the field; a description that rewrites itself while the
 * user types is re-announced at unpredictable moments, which is noisier than the thing it
 * was trying to help with. What *is* live is the error message, which names the rules the
 * password has not met yet — announced once, on submit, from a `role="alert"`.
 *
 * `react-power-tooltip` is not a dependency of Anvex and is not being added.
 *
 * ## `validator` is not a dependency either, and the real bug is not the one it looks like
 *
 * The old page called
 *
 *     validator.isStrongPassword(password, {minLength: 7, minLowerCase: 1,
 *                                           minUppercase: 1, minNumbers: 1, minSybols: 1})
 *
 * Two of those keys are misspelled — the options are `minSymbols` and `minLowercase` — and
 * `isStrongPassword` merges the caller's object over its defaults without validating a
 * single key, so both were silently dropped. **The obvious conclusion, that the symbol rule
 * therefore never applied, is wrong**, and it is worth being precise about why: read
 * `validator/lib/isStrongPassword.js`, and `minSymbols` and `minLowercase` are *both* `1`
 * by default. `merge` fills in every key the caller did not supply, so the typos changed
 * nothing about the symbol rule at all. A symbol-free password was already rejected.
 *
 * What the typos *did* leave behind is the mismatch, running the other way: `minLowercase`
 * defaulted to `1`, so the form enforced a **lowercase requirement the tooltip never
 * mentioned**. `PASSWORD1!` satisfies every rule the tooltip listed — 7+ characters, an
 * uppercase letter, a number, a symbol — and was refused with "Password Must Obey Rules"
 * beside a tooltip that gave no hint why. That is the defect being fixed: the four rules
 * below are the four rules on screen, and there is no fifth.
 *
 * Two narrower promises were broken by the library's own definitions rather than by the
 * typos, and they go the same way. `upperCaseRegex` is `/^[A-Z]$/`, so `ÄNDERUNG1!` had no
 * uppercase letter; `symbolRegex` is a fixed ASCII punctuation set, so a password whose only
 * symbol was `€` or `§` had no symbol. Both were refused by a tooltip promising something
 * the user had plainly done.
 *
 * So the reason for not adding the dependency is not the ~140 kB, it is that **an API which
 * accepts an unknown option silently is the wrong tool for expressing a policy**. Writing
 * the rules out buys three things a boolean cannot: the message can name **which** rules are
 * unmet, where `isStrongPassword` could only ever produce "Password Must Obey Rules"; the
 * rendered list and the check are generated from **one array**, so a rule promised on screen
 * and not enforced (or enforced and not promised) is now impossible rather than merely
 * fixed; and a misspelling is a `ReferenceError` at import rather than a policy that quietly
 * differs from its own documentation.
 *
 * `validator.isEmail` goes the same way, for a different reason: an email address is only
 * really validated by the server that accepts it (`app/schemas/user.py`'s `EmailStr`) and
 * by delivery. A client-side check exists to catch a typo before a round trip, so it should
 * be **conservative** — a stricter local regex than the server's rule rejects addresses the
 * account could have had, which is the one failure mode with no recovery from the user's
 * side.
 *
 * ## Other defects of the old page, fixed here
 *
 *  - **The password was visible by default** (`useState(true)`), so a sign-up form
 *    displayed the credential being chosen to anyone in the room. Hidden by default, as
 *    `LoginPage` has it.
 *  - **`useEffect` chains for navigation.** Success was an effect watching `[response,
 *    error]` and a *second* effect wiped all three fields whenever the global error changed
 *    — so a failed sign-up deleted everything the user had typed, including the two fields
 *    that were fine. Here the navigation is on the success path of the submit handler and
 *    nothing clears the form.
 *  - **No accessible names.** No `htmlFor`, no `id`, per-field messages in a second
 *    `<label>` associated with nothing, an `onClick` on an `aria-hidden` `<svg>` for the
 *    visibility toggle. ANV-29 fixed all four on the login page; the same fixes are here.
 *  - **No length ceilings.** `username`, `email` and `password` are `VARCHAR(50)`,
 *    `VARCHAR(320)` and 72 bytes of bcrypt respectively, and the old form let a user submit
 *    past all three to be told "Try Again" by a 422 that named nothing.
 *
 * ## Why there is no `maxLength` attribute on these inputs
 *
 * `LoginPage` caps its identifier with `maxLength={50}` and that is right for a field whose
 * value **already exists**: a longer one cannot be a username anybody has. On a form that
 * *creates* the value, `maxLength` truncates a paste silently — a password manager filling
 * an 80-character password would leave 72 characters in the box, the account would be
 * created with a password the user does not have, and nothing on screen would have said so.
 * The ceilings are validation rules instead, so passing one is a message rather than a
 * quiet edit.
 */
export default function SignUpPage() {
  const navigate = useNavigate()

  const [form, setForm] = useState({ email: '', username: '', password: '' })

  /** `{email, username, password}` of messages; `''` means "no problem with this field". */
  const [fieldErrors, setFieldErrors] = useState(EMPTY_FIELD_ERRORS)

  /** The failed registration, as an `ApiError` — or `null`. Drives the banner and button. */
  const [error, setError] = useState(null)

  const [submitting, setSubmitting] = useState(false)
  const [passwordVisible, setPasswordVisible] = useState(false)

  const emailId = useId()
  const usernameId = useId()
  const passwordId = useId()
  const emailErrorId = useId()
  const usernameErrorId = useId()
  const passwordErrorId = useId()
  const passwordRulesId = useId()
  const formErrorId = useId()

  /** Update one field and clear its message — the user is already fixing it. */
  const setField = (field) => (event) => {
    const { value } = event.target
    setForm((previous) => ({ ...previous, [field]: value }))
    setFieldErrors((previous) => (previous[field] === '' ? previous : { ...previous, [field]: '' }))
  }

  async function onSubmit(event) {
    event.preventDefault()
    if (submitting) return

    // Trimmed for the check *and* for the request. Surrounding whitespace in an email
    // address or a username is never meant, and the lowercasing is what makes
    // `Ada@Example.com` and `ada@example.com` the same account rather than two — the old
    // page lowercased the address it *sent* but compared the raw one against the username,
    // so the "username cannot be your email" rule missed on any difference in case.
    const email = form.email.trim().toLowerCase()
    const username = form.username.trim()

    const problems = validateRegistration({ email, username, password: form.password })
    setFieldErrors(problems)
    if (problems.email !== '' || problems.username !== '' || problems.password !== '') return

    setSubmitting(true)
    setError(null)

    try {
      await register({ username, email, password: form.password })
    } catch (caught) {
      const apiError = toApiError(caught)
      setSubmitting(false)
      // ANV-24/25's rule: a cancelled request is this component unmounting, not a failure,
      // and swallowing it means changing nothing rather than clearing what is on screen.
      if (apiError.code === CLIENT_ERROR_CODES.CANCELLED) return

      // A duplicate belongs beside the field that has to change, not in a banner at the
      // bottom of the form. CLAUDE.md §4 makes `details.field` the machine-readable half of
      // the 409 for exactly this; the message is displayed, never matched on.
      const field = conflictedField(apiError)
      if (field === null) setError(apiError)
      else setFieldErrors((previous) => ({ ...previous, [field]: apiError.message }))
      return
    }

    // The account exists. Hand the username to `/login` and let them sign in with it —
    // `submitting` is deliberately not cleared, because the button must not become
    // pressable again during the navigation.
    navigate({ to: LOGIN_ROUTE, replace: true, state: signUpHandoffState({ username }) })
  }

  return (
    // `data-testid` matches the `RoutePlaceholder` this replaces, so every ANV-27/28
    // routing test that asserts "/signup resolved" keeps asserting it against the real page.
    <section data-testid="route-sign-up" className="mt-6 flex flex-col items-center lg:mt-20">
      <h1 className="mb-5 font-gothic text-4xl font-xl">Sign Up</h1>

      <div className="rounded-xl border border-neutral-200 p-4 sm:w-1/2 md:w-1/2 lg:w-1/3 xl:w-1/4 dark:border-neutral-700">
        {/*
          `noValidate` with `required` still on the inputs: the attribute is what tells
          assistive tech the field is mandatory, while our own messages — positioned, styled,
          dark-mode aware and wired to `aria-describedby` — are the ones the user sees.
        */}
        <form className="mx-5" onSubmit={onSubmit} noValidate>
          <div className="flex flex-col py-2 text-neutral-900 dark:text-neutral-200">
            <div className="relative">
              <label htmlFor={emailId} className="mb-2 font-gothic font-medium">
                Email:
              </label>
              <span
                id={emailErrorId}
                role="alert"
                data-testid="signup-email-error"
                className="absolute right-1 bottom-0 font-gothic text-sm font-medium text-red-600"
              >
                {fieldErrors.email}
              </span>
            </div>
            <input
              id={emailId}
              name="email"
              // `type="email"` rather than the old `type="text"`: it is what asks a phone
              // for the keyboard with an `@` on it. The format check is still ours — a
              // browser's own is a bubble that vanishes on blur and is suppressed by
              // `noValidate` anyway.
              type="email"
              autoComplete="email"
              required
              aria-invalid={fieldErrors.email !== ''}
              aria-describedby={emailErrorId}
              className="mt-2 w-full rounded-xl border border-neutral-300 bg-white p-2 dark:border-neutral-700 dark:bg-neutral-900"
              value={form.email}
              onChange={setField('email')}
            />
          </div>

          <div className="flex flex-col py-2 text-neutral-900 dark:text-neutral-200">
            <div className="relative">
              <label htmlFor={usernameId} className="mb-2 font-gothic font-medium">
                Username:
              </label>
              <span
                id={usernameErrorId}
                role="alert"
                data-testid="signup-username-error"
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
                data-testid="signup-password-error"
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
                // `new-password`, not `current-password`: it is what makes a password
                // manager offer to *generate* one here instead of filling in an old one.
                autoComplete="new-password"
                required
                aria-invalid={fieldErrors.password !== ''}
                // Two descriptions, in the order they are useful: the standing rules, then
                // whatever went wrong this time. Both are read out with the field.
                aria-describedby={`${passwordRulesId} ${passwordErrorId}`}
                className="mt-2 w-full rounded-xl border border-neutral-300 bg-white p-2 pr-10 dark:border-neutral-700 dark:bg-neutral-900"
                value={form.password}
                onChange={setField('password')}
              />
              <button
                type="button"
                onClick={() => setPasswordVisible((visible) => !visible)}
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

            {/*
              The rules the tooltip used to hide. Always rendered, never behind a hover, and
              generated from the same array the validator checks, so the list and the policy
              cannot disagree.
            */}
            <div
              id={passwordRulesId}
              data-testid="signup-password-rules"
              className="mt-2 font-gothic text-sm font-medium text-neutral-600 dark:text-neutral-400"
            >
              <p>Password must contain:</p>
              <ul className="list-disc pl-5">
                {PASSWORD_RULES.map((rule) => (
                  <li key={rule.id}>{rule.label}</li>
                ))}
              </ul>
            </div>
          </div>

          {/*
            The form-level failure. In the DOM at all times and empty when there is nothing
            to say — a `role="alert"` region inserted together with its text is the case
            screen readers handle least consistently, and the old page rendered the whole
            `<p>` conditionally with no role at all.
          */}
          <div className="relative" id={formErrorId} role="alert" data-testid="signup-error">
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
            {submitting ? 'Signing Up…' : error === null ? 'Sign Up' : 'Try Again'}
          </button>

          <p className="mb-2 text-center font-gothic text-sm font-medium">
            {/*
              A `<Link>`, not the old `<p onClick={() => navigate("/login")}>`: a paragraph
              is not focusable, not operable from a keyboard, and is announced as text.
            */}
            <Link to={LOGIN_ROUTE} className="hover:cursor-pointer hover:underline">
              Already Have an Account? Log In Now!
            </Link>
          </p>
        </form>
      </div>
    </section>
  )
}

/** The "nothing is wrong" map, frozen so a `setFieldErrors(EMPTY_FIELD_ERRORS)` cannot alias. */
const EMPTY_FIELD_ERRORS = Object.freeze({ email: '', username: '', password: '' })

/**
 * The backend's 409 code (`app/domain/errors.py`'s `ConflictError`).
 *
 * A literal here rather than in `lib/api`, which enumerates only the codes the *transport*
 * itself invents and the four token codes its interceptor has to act on. §4's contract is
 * that a consumer branches on `code`; when a second feature needs this one, it moves down
 * beside `AUTH_ERROR_CODES` rather than sideways into a shared page module.
 */
const CONFLICT_CODE = 'conflict'

/** Length ceilings, from `backend/app/models/user.py` and `app/schemas/user.py`. */
const USERNAME_MIN_LENGTH = 7
const USERNAME_MAX_LENGTH = 50
const EMAIL_MAX_LENGTH = 320
const PASSWORD_MIN_LENGTH = 7

/**
 * bcrypt hashes at most 72 **bytes** and ignores the rest, which would make two different
 * long passwords interchangeable — so `app/utils/security.py` refuses beyond it and the
 * schema caps at 72 *characters*. This is the character cap; the byte one is the server's,
 * because only the server knows the encoding, and a multibyte password can pass here and
 * still be refused there (as a 422 naming the password field, which lands in the banner).
 */
const PASSWORD_MAX_LENGTH = 72

/**
 * A deliberately loose address check: one `@`, something either side of it, and a dotted
 * domain, with no whitespace anywhere.
 *
 * It exists to catch `ada@example` and `ada.example.com` before a round trip, and nothing
 * more. The authority on what is a valid address is `EmailStr` on the server; a regex here
 * that tried to be RFC 5322 would eventually refuse somebody's real address, and there is
 * no way for them to argue with it.
 */
const EMAIL_PATTERN = /^[^\s@]+@[^\s@.]+(\.[^\s@.]+)+$/

/**
 * The password policy: one array, read by the rendered list **and** by the validator.
 *
 * That is the fix for the old page's real defect — a tooltip promising four rules beside a
 * check that enforced whichever of them `validator` happened to default to. A rule cannot
 * be displayed without being enforced, or enforced without being displayed, because there
 * is one of it.
 *
 * `label` is what the user reads; `missing` is how the rule is named inside a sentence
 * when it fails. The predicates are Unicode-aware (`\p{Lu}`, `\p{Nd}`) rather than
 * `[A-Z]`/`[0-9]`, so a password in a non-Latin script is judged by the same rules instead
 * of being told it has no uppercase letter — and "symbol" is *anything that is neither a
 * letter nor a number*, which is broader than `validator`'s fixed punctuation set. A form
 * that rejects a password because the symbol in it is not on our list is hostile.
 */
const PASSWORD_RULES = Object.freeze([
  {
    id: 'length',
    label: `At least ${PASSWORD_MIN_LENGTH} characters`,
    missing: `${PASSWORD_MIN_LENGTH} characters`,
    met: (password) => password.length >= PASSWORD_MIN_LENGTH,
  },
  {
    id: 'uppercase',
    label: 'At least 1 uppercase letter',
    missing: 'an uppercase letter',
    met: (password) => /\p{Lu}/u.test(password),
  },
  {
    id: 'number',
    label: 'At least 1 number',
    missing: 'a number',
    met: (password) => /\p{Nd}/u.test(password),
  },
  {
    id: 'symbol',
    label: 'At least 1 symbol',
    missing: 'a symbol',
    met: (password) => /[^\p{L}\p{N}]/u.test(password),
  },
])

/**
 * The field a 409 says to fix, or `null` if this failure is not one of those.
 *
 * `null` for a conflict on a field this form does not have, too: routing a message to a
 * field that is not on screen would hide it completely, so anything unrecognised falls
 * through to the banner, where at least it is read out.
 */
function conflictedField(apiError) {
  if (apiError.code !== CONFLICT_CODE) return null
  const field = apiError.details?.field
  return field === 'email' || field === 'username' ? field : null
}

/** `["a", "b", "c"]` → `"a, b and c"`. */
function joinPhrases(phrases) {
  if (phrases.length < 2) return phrases.join('')
  return `${phrases.slice(0, -1).join(', ')} and ${phrases[phrases.length - 1]}`
}

/**
 * What is wrong with this form, as a `{field: message}` map. Pure, so an invalid submit is
 * decided without a render and without a network.
 *
 * **Not exported**, on purpose: a `.jsx` module exporting both a component and a plain
 * function loses React Fast Refresh (`react-refresh/only-export-components`). It is
 * exercised through the rendered form, which is the stronger test anyway — what matters is
 * that an invalid submit is refused **and never reaches the network**, and only a rendered
 * test can assert the second half.
 *
 * `email` and `username` arrive already trimmed (and the email lowercased) from the caller,
 * so the equality rule compares the values that would actually be sent.
 */
function validateRegistration({ email, username, password }) {
  return {
    email: emailProblem(email),
    username: usernameProblem(username, email),
    password: passwordProblem(password),
  }
}

function emailProblem(email) {
  if (email === '') return 'Enter your email address'
  if (email.length > EMAIL_MAX_LENGTH) return `Email must be ${EMAIL_MAX_LENGTH} characters or fewer`
  if (!EMAIL_PATTERN.test(email)) return 'Enter a valid email address'
  return ''
}

function usernameProblem(username, email) {
  if (username === '') return 'Enter a username'
  if (username.length < USERNAME_MIN_LENGTH)
    return `Username must be ${USERNAME_MIN_LENGTH} or more characters`
  if (username.length > USERNAME_MAX_LENGTH)
    return `Username must be ${USERNAME_MAX_LENGTH} characters or fewer`
  // Compared case-insensitively against the *normalised* address, because that is the one
  // being registered. The old page compared against the raw input while sending the
  // lowercased one, so `Ada@Example.com` as a username passed a rule it plainly broke.
  if (email !== '' && username.toLowerCase() === email) return 'Username cannot be your email address'
  return ''
}

function passwordProblem(password) {
  if (password === '') return 'Enter a password'
  if (password.length > PASSWORD_MAX_LENGTH)
    return `Password must be ${PASSWORD_MAX_LENGTH} characters or fewer`
  const unmet = PASSWORD_RULES.filter((rule) => !rule.met(password))
  if (unmet.length === 0) return ''
  // Names what is missing, where the old page could only say "Password Must Obey Rules" —
  // a boolean has nothing else to say.
  return `Password needs ${joinPhrases(unmet.map((rule) => rule.missing))}`
}
