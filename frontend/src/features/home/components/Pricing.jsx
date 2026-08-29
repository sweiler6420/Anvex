import { Link } from '@tanstack/react-router'

import { CheckCircleIcon, XCircleIcon } from '@components/ui/icons'
import { SIGNUP_ROUTE } from '@routes/paths'

/**
 * `#pricing` (ANV-32), ported from
 * `AverageInvestorWeb/src/components/home/Pricing.jsx` (145 lines).
 *
 * ---------------------------------------------------------------------------------------
 * ## The accessibility bug this section actually had
 *
 * Every feature row was a green `CheckCircleIcon` or a red `XCircleIcon` followed by the
 * feature's name — and **both icons were `aria-hidden="true"`**. So the *only* carriers of
 * "is this included" were the glyph's shape and its colour, and one of those was removed
 * from the accessibility tree on purpose. A screen-reader user heard the Basic plan as
 *
 * > Basic Stock Charts. Up to 5 Watchlist Items. Advanced Analytics. Portfolio Tracking.
 *
 * — a free tier that appears to include the two things it exists to withhold. Anyone with a
 * red/green colour deficiency reading the same rows visually had a shape to go on and
 * nothing else.
 *
 * The fix is a `sr-only` "Included: " / "Not included: " in front of each label. It is
 * invisible, occupies no space, and makes the answer survive both `aria-hidden` and
 * colour blindness — WCAG 1.4.1 ("colour is not used as the only visual means of conveying
 * information") and 1.3.1 in one span. The icons stay decorative, which is now true.
 *
 * ## What else changed
 *
 *  - **The three `<a href="/signup">` became `<Link to={SIGNUP_ROUTE}>`.** ANV-28's rule:
 *    a bare anchor is a document navigation and a document navigation discards the
 *    in-memory access token. `HomePage.test.jsx` clicks all three, because an `href`
 *    assertion cannot tell an anchor from a `Link`.
 *  - **The plan name became an `<h3>`.** It was a `<p className='text-4xl'>` — a heading
 *    everywhere except in the accessibility tree, so the three plans were unreachable by
 *    heading navigation and the page's outline went straight from "Pricing" to nothing.
 *  - **Three copies of one card became one array**, which is also what makes the
 *    included/not-included flag a *data* property rather than a choice of icon repeated
 *    twelve times.
 *  - **The `<section>` is labelled by its heading** and the plans are a `<ul>`.
 *
 * **Not changed, deliberately:** only the Pro card carries `h-full`, so at `sm` the middle
 * card stretches and its neighbours do not. Ported as found; see the ANV-32 report.
 */

/** The three plans, in the old file's order. Copy and prices verbatim. */
const PLANS = [
  {
    key: 'basic',
    name: 'Basic',
    qualifier: '(Free Tier)',
    price: '$0',
    fullHeight: false,
    cta: 'Get Started',
    features: [
      { label: 'Basic Stock Charts', included: true },
      { label: 'Up to 5 Watchlist Items', included: true },
      { label: 'Advanced Analytics', included: false },
      { label: 'Portfolio Tracking', included: false },
    ],
  },
  {
    key: 'pro',
    name: 'Pro',
    qualifier: null,
    price: '$9.99',
    fullHeight: true,
    cta: 'Subscribe',
    features: [
      { label: 'Advanced Charts & Indicators', included: true },
      { label: 'Unlimited Watchlists', included: true },
      { label: 'Portfolio Analytics', included: true },
      { label: 'Real-time Alerts', included: true },
    ],
  },
  {
    key: 'premium',
    name: 'Premium',
    qualifier: '(Recommended)',
    price: '$19.99',
    fullHeight: false,
    cta: 'Subscribe',
    features: [
      { label: 'All Pro Features', included: true },
      { label: 'AI-Powered Insights', included: true },
      { label: 'Custom Alerts & Notifications', included: true },
      { label: 'Priority Support', included: true },
    ],
  },
]

export default function Pricing() {
  return (
    <section id="pricing" aria-labelledby="pricing-heading" className="mt-20">
      <h2
        id="pricing-heading"
        className="my-8 text-center font-gothic text-3xl font-medium tracking-wide sm:text-5xl lg:text-6xl"
      >
        Pricing
      </h2>

      <ul className="flex flex-wrap">
        {PLANS.map((plan) => (
          <li key={plan.key} className="w-full p-2 sm:w-1/2 lg:w-1/3">
            <div
              className={`rounded-xl border border-neutral-200 p-10 hover:scale-105 dark:border-neutral-700 ${
                plan.fullHeight ? 'h-full' : ''
              }`}
            >
              <h3 className="mb-8 font-gothic text-4xl font-medium">
                {plan.name}
                {plan.qualifier && (
                  <span className="mb-4 ml-2 bg-gradient-to-r from-brand-500/80 to-brand-700/80 bg-clip-text text-2xl text-transparent dark:from-brand-400/80 dark:to-brand-600/80">
                    {' '}
                    {plan.qualifier}
                  </span>
                )}
              </h3>

              <p className="mb-8">
                <span className="mr-2 mt-6 font-gothic text-5xl font-medium">{plan.price}</span>
                <span className="tracking-tight text-neutral-400">/Month</span>
              </p>

              <ul>
                {plan.features.map((feature) => (
                  <li key={feature.label} className="mt-8 flex items-center">
                    {feature.included ? (
                      <CheckCircleIcon className="block h-6 w-6 text-green-500" />
                    ) : (
                      <XCircleIcon className="block h-6 w-6 text-red-500" />
                    )}
                    <span className="ml-2">
                      {/*
                        The whole point of this section's accessibility fix: without these
                        words, "is it included" lives only in the glyph's colour and shape,
                        and the glyph is aria-hidden.
                      */}
                      <span className="sr-only">
                        {feature.included ? 'Included: ' : 'Not included: '}
                      </span>
                      {feature.label}
                    </span>
                  </li>
                ))}
              </ul>

              <Link
                to={SIGNUP_ROUTE}
                className="mt-20 inline-flex h-12 w-full items-center justify-center rounded-md border border-brand-600 p-5 text-center font-gothic text-xl font-medium tracking-tight hover:bg-brand-600 hover:text-white hover:underline dark:border-brand-400 dark:hover:bg-brand-500"
              >
                {plan.cta}
              </Link>
            </div>
          </li>
        ))}
      </ul>
    </section>
  )
}
