import { Link } from '@tanstack/react-router'

import { HOME_ROUTE, SIGNUP_ROUTE } from '@routes/paths'

/**
 * The home page's opening pitch (ANV-32), ported from
 * `AverageInvestorWeb/src/components/home/Hero.jsx` (35 lines).
 *
 * **The copy, the gradients and the type scale are unchanged.** The old Hero was already
 * Anvex-branded ("Research Clearer. Invest Sharper."), so this is a port and not a
 * rewrite; the only edits are the two calls to action.
 *
 * ## The two anchors are the port's one real fix
 *
 * Both were `<a href>`: `/signup` and `#features`. ANV-28's rule — an in-app destination
 * is a `<Link>`, never an `<a href>` — is not a style preference here. A bare anchor to
 * `/signup` is a **full document navigation**: it reloads the bundle, and ANV-26 keeps the
 * access token in memory, so the "Start Researching Today" button signed out anyone who
 * happened to already have a session. The `#features` anchor is the same class of bug one
 * layer down: the fragment would be written straight onto `window.location` without the
 * router seeing it, so `Header`'s `NAV_ACTIVE_OPTIONS` (which reads the *router's*
 * location, hash included) would never notice that "Features" had become the current
 * section. `<Link to={HOME_ROUTE} hash="features">` is the same URL through the router.
 *
 * An `href` assertion cannot tell those two apart — both render `href="/signup"` — which
 * is why `HomePage.test.jsx` clicks each one and asserts the router moved.
 *
 * `id="top"` is carried over. Nothing links to it today; it costs one attribute and it is
 * the anchor a "back to top" control would want.
 */
export default function Hero() {
  return (
    <section id="top" className="mt-6 flex flex-col items-center lg:mt-20">
      <h1 className="mb-5 font-gothic text-4xl font-xl md:mb-20">
        <span>
          <span className="text-5xl text-neutral-900 dark:text-neutral-100">Research </span>
          <span className="text-5xl text-brand-600 dark:text-brand-400">Clearer. </span>
        </span>
        <span>
          {' '}
          <span className="text-5xl text-neutral-900 dark:text-neutral-100">Invest</span>
          <span className="text-5xl text-brand-600 dark:text-brand-400"> Sharper.</span>
        </span>
      </h1>

      <p className="my-10 max-w-4xl text-center font-gothic text-lg font-xl leading-8">
        At Anvex, we specialize in democratizing investment research with our cutting-edge
        stock analysis platform. Designed for individual investors, our platform simplifies
        the process of stock research and analysis, ensuring you have all the information
        you need to make informed investment decisions. This eliminates the complexity of
        traditional financial analysis tools. Subscribers can instantly access detailed
        stock data, charts, and insights simply by searching for any stock symbol. Plus,
        track your trades with our AI-powered journaling system for in-depth performance
        reviews and analysis.
      </p>

      <div className="my-20 flex justify-center">
        <Link
          to={SIGNUP_ROUTE}
          className="mx-3 rounded-md border bg-gradient-to-r from-brand-500 to-brand-700 px-4 py-3 font-gothic font-demi text-white hover:underline hover:opacity-90 dark:from-brand-400 dark:to-brand-600"
        >
          Start Researching Today
        </Link>
        <Link
          to={HOME_ROUTE}
          hash="features"
          className="mx-3 rounded-md border border-neutral-300 px-4 py-3 font-gothic font-demi hover:scale-105 hover:underline dark:border-neutral-700"
        >
          Learn More
        </Link>
      </div>
    </section>
  )
}
