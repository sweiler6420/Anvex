import { useState } from 'react'

/**
 * `#contact` (ANV-32), ported from
 * `AverageInvestorWeb/src/components/home/Contact.jsx` (53 lines).
 *
 * ---------------------------------------------------------------------------------------
 * ## The form does not send anything, and now it says so
 *
 * The old file read, in full:
 *
 * ```js
 * // const isDisabled = !name || !email || !message;
 * const isDisabled = true
 * function handleSubmit(e){ e.preventDefault(); }
 * ```
 *
 * — a permanently disabled button above an empty handler. That is still the honest state of
 * things: the Anvex API has no contact endpoint (§4's routes are auth, users, stocks,
 * stock_data, watchlists, politicians and news), and ANV-31 established that a screen for
 * something the app cannot do says so rather than miming it. So the form is ported intact
 * and **it is the silence that is fixed**, not the behaviour.
 *
 * Two changes, both accessibility:
 *
 *  - **`disabled` became `aria-disabled="true"`.** A `disabled` button is removed from the
 *    tab order *and* from most screen readers' browse output, so a keyboard user tabbed
 *    straight past the only control on the section and was never told why it did nothing.
 *    `aria-disabled` announces "dimmed" while leaving the control reachable — the standard
 *    pattern for a control that is off for a reason worth reading. The `<form>`'s submit
 *    handler still refuses, so nothing can be sent either way; the refusal just moved from
 *    the browser to one line of ours.
 *  - **A description says why**, wired with `aria-describedby`, so the explanation is
 *    attached to the control rather than merely near it.
 *
 * The sentence in that description is **the one piece of copy on this page that is mine**
 * rather than Stephen's — there was no existing wording for "this does nothing", because
 * the old page never admitted it. Flagged in the ANV-32 report.
 *
 * ## What else changed
 *
 *  - The heading carried a trailing `<span>` containing a single space. Removed; it
 *    rendered nothing and pushed a stray text node into the section's accessible name.
 *  - The `<section>` is labelled by its heading, so it is a named region landmark.
 *
 * **Already correct in the old file, unusually:** every control had a real `<label>` with
 * `htmlFor` matching its `id`. ANV-29 found the *auth* forms had none at all; this one did
 * it right.
 */

/** Why the send button is off. One string, referenced by the button's description. */
export const CONTACT_UNAVAILABLE_MESSAGE =
  'Sending is not connected yet, so nothing typed here reaches us.'

export default function Contact() {
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [message, setMessage] = useState('')

  // Not `!name || !email || !message`: there is nowhere for a complete message to go.
  const isDisabled = true

  function handleSubmit(event) {
    event.preventDefault()
  }

  return (
    <section id="contact" aria-labelledby="contact-heading" className="mt-20">
      <h2
        id="contact-heading"
        className="my-8 text-center font-gothic text-3xl font-medium tracking-wide sm:text-5xl lg:text-6xl"
      >
        Contact
        <span className="text-5xl font-xl text-brand-600 dark:text-brand-400"> Anvex</span>
      </h2>

      <div className="flex flex-col items-center">
        <div className="w-full p-2 sm:w-3/4 lg:w-1/2">
          <div className="rounded-xl border border-neutral-200 p-10 dark:border-neutral-700">
            <form onSubmit={handleSubmit} className="flex flex-col sm:flex-row">
              <div className="mr-4 flex w-full flex-col sm:w-1/2">
                <label className="mb-2 font-gothic font-medium" htmlFor="contact-name">
                  Name
                </label>
                <input
                  type="text"
                  id="contact-name"
                  className="mb-4 rounded-xl border border-neutral-300 p-2 dark:border-neutral-700"
                  placeholder="Your Name"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  required
                />

                <label className="mb-2 font-gothic font-medium" htmlFor="contact-email">
                  Email
                </label>
                <input
                  type="email"
                  id="contact-email"
                  className="mb-4 rounded-xl border border-neutral-300 p-2 dark:border-neutral-700"
                  placeholder="Your Email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  required
                />
              </div>

              <div className="flex w-full flex-col sm:w-1/2">
                <label className="mb-2 font-gothic font-medium" htmlFor="contact-message">
                  Message
                </label>
                <textarea
                  id="contact-message"
                  className="mb-4 h-32 rounded-xl border border-neutral-300 p-2 dark:border-neutral-700"
                  placeholder="Your Message"
                  value={message}
                  onChange={(event) => setMessage(event.target.value)}
                  required
                />

                <button
                  type="submit"
                  aria-disabled={isDisabled}
                  aria-describedby="contact-unavailable"
                  className={`rounded px-4 py-2 font-gothic font-medium ${
                    isDisabled
                      ? 'cursor-not-allowed bg-neutral-300 dark:bg-neutral-700'
                      : 'bg-brand-600 text-white hover:underline hover:opacity-90 dark:bg-brand-500'
                  }`}
                >
                  Send
                </button>

                <p
                  id="contact-unavailable"
                  className="mt-2 font-base text-sm text-neutral-600 dark:text-neutral-400"
                >
                  {CONTACT_UNAVAILABLE_MESSAGE}
                </p>
              </div>
            </form>
          </div>
        </div>
      </div>
    </section>
  )
}
