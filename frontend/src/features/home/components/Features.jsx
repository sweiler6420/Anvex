import {
  ArrowTrendingUpIcon,
  ChartBarIcon,
  CurrencyDollarIcon,
  EyeIcon,
  Squares2X2Icon,
  UserGroupIcon,
} from '@components/ui/icons'

/**
 * `#features` (ANV-32), ported from
 * `AverageInvestorWeb/src/components/home/Features.jsx` (118 lines).
 *
 * **The id is load-bearing.** `components/layout/navItems.js` links the header's "Features"
 * item at `HOME_ROUTE` + `hash: 'features'`, and `Footer` links the same fragment. If this
 * `id` is renamed those links silently scroll nowhere, which is why `HomePage.test.jsx`
 * derives its assertion from `ANONYMOUS_NAV_ITEMS` rather than restating the four strings.
 *
 * ## What changed from the old file
 *
 *  - **Six copies of one card became one array and one `.map()`.** The old file repeated
 *    the same twelve-line block six times, which is how the icon colour ended up
 *    alternating `brand-600`/`brand-700` — plausibly deliberate, certainly unmaintainable.
 *    The alternation is preserved by index so the render is byte-identical.
 *  - **`<h5>` became `<h3>`.** The section's own heading is an `<h2>`, so `<h5>` skipped
 *    two levels: a screen-reader user navigating by heading hears a jump that implies two
 *    missing sections. Nothing about the *size* changes — the size was always `text-xl`
 *    from the class, never from the tag.
 *  - **The six cards became a `<ul>`/`<li>`.** They are a list, and Tailwind's preflight
 *    already zeroes a list's margin, padding and marker, so the rendered layout is
 *    identical while assistive tech gains "list, 6 items".
 *  - **The `<section>` is labelled by its heading**, which promotes it from a generic
 *    element to a named `region` landmark — the thing that makes a long marketing page
 *    navigable without scrolling through it.
 *
 * **Not changed, deliberately:** the column class is `sm:1/2`, which is a typo for
 * `sm:w-1/2` and therefore not a Tailwind class at all — the cards stay one-per-row until
 * `lg`. It is Stephen's layout to fix; see the ANV-32 report.
 */

/** The six cards, in the order the old file wrote them. Copy verbatim. */
const FEATURES = [
  {
    key: 'charting',
    Icon: ChartBarIcon,
    title: 'Advanced Charting',
    body: 'Access professional-grade candlestick charts and technical indicators to analyze stock price movements and identify research opportunities with precision.',
  },
  {
    key: 'real-time',
    Icon: ArrowTrendingUpIcon,
    title: 'Real-time Data',
    body: 'Get up-to-the-minute stock prices, market data, and financial metrics to make informed investment decisions based on the latest market information.',
  },
  {
    key: 'watchlists',
    Icon: EyeIcon,
    title: 'Watchlist Management',
    body: 'Create and manage personalized watchlists to track your favorite stocks and monitor their performance without cluttering your investment strategy.',
  },
  {
    key: 'desktop',
    Icon: Squares2X2Icon,
    title: 'Multi-Window Desktop',
    body: 'Organize multiple research windows like a desktop environment. Compare stocks side-by-side, keep charts open while analyzing fundamentals - just like professional trading platforms.',
  },
  {
    key: 'insights',
    Icon: UserGroupIcon,
    title: 'Market Insights',
    body: 'Access anonymous market analytics and research trends. See what stocks are being researched most, popular analysis patterns, and market sentiment indicators.',
  },
  {
    key: 'journaling',
    Icon: CurrencyDollarIcon,
    title: 'Trade Journaling',
    body: 'Track your trades with AI-powered analysis and in-depth performance reviews. Journal your investment decisions and learn from your trading patterns.',
  },
]

export default function Features() {
  return (
    <section
      id="features"
      aria-labelledby="features-heading"
      className="relative mt-20 border-b border-neutral-200 dark:border-neutral-800"
    >
      <div className="text-center">
        <span className="h-6 rounded-full bg-neutral-100 px-2 py-1 font-gothic text-sm font-medium uppercase text-brand-700 dark:bg-neutral-900 dark:text-brand-400">
          Features
        </span>
        <h2
          id="features-heading"
          className="mt-10 font-gothic text-3xl font-medium tracking-wide sm:text-5xl lg:mt-20 lg:text-6xl"
        >
          <span className="text-5xl font-xl text-brand-600 dark:text-brand-400">Research </span>&
          Analysis Tools
        </h2>
      </div>

      <ul className="mt-10 flex flex-wrap lg:mt-20">
        {FEATURES.map(({ key, Icon, title, body }, index) => (
          <li key={key} className="w-full sm:1/2 lg:w-1/3">
            <div className="flex">
              <div
                className={`mx-6 flex h-10 w-10 items-center justify-center rounded-full bg-neutral-100 p-2 dark:bg-neutral-900 dark:text-brand-400 ${
                  index % 2 === 0 ? 'text-brand-600' : 'text-brand-700'
                }`}
              >
                <Icon className="block h-6 w-6" />
              </div>
              <div>
                <h3 className="mb-6 mt-1 font-gothic text-xl font-medium">{title}</h3>
                <p className="mb-20 p-2 font-gothic text-md font-medium text-neutral-600 dark:text-neutral-400">
                  {body}
                </p>
              </div>
            </div>
          </li>
        ))}
      </ul>
    </section>
  )
}
