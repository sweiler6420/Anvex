import { CheckCircleIcon } from '@components/ui/icons'

/**
 * `#workflow` (ANV-32), ported from
 * `AverageInvestorWeb/src/components/home/Workflow.jsx` (86 lines).
 *
 * ---------------------------------------------------------------------------------------
 * ## The seam where `InteractiveDesktop` goes
 *
 * The old section put `<InteractiveDesktop />` in the gradient panel on the left. That
 * component is **deliberately not ported here**: it is a thin wrapper around
 * `BinPackingLayout` + `WindowMenu` (the ~1200-line bin-packing window system, **ANV-33**)
 * and three widgets (**ANV-34**), and it lands in **ANV-35**.
 *
 * The seam is the `demo` prop, not a `TODO` comment and not an import that does not exist
 * yet. ANV-35's whole change to this file is one line in `HomePage.jsx`:
 *
 * ```jsx
 * <Workflow demo={<InteractiveDesktop />} />
 * ```
 *
 * A prop rather than a direct import for two reasons. It keeps `features/home/` from
 * depending on the window system, so this section renders (and tests) in a suite that has
 * never loaded a `ResizeObserver`; and it makes the seam *assertable* — `HomePage.test.jsx`
 * passes a stub and checks it lands inside the panel, so ANV-35 finds out immediately if
 * the panel is ever restyled out from under it.
 *
 * Until then the panel holds `WorkflowDemoPlaceholder`: three muted rectangles suggesting
 * tiled windows, `aria-hidden` and entirely without text. An empty panel would read as a
 * broken image, and inventing a caption ("interactive demo coming soon") would put words on
 * a marketing page that are nobody's — see the ANV-32 report.
 *
 * ---------------------------------------------------------------------------------------
 * ## What else changed
 *
 *  - **`<h5>` became `<h3>`** under the section's `<h2>`, for the reason `Features.jsx`
 *    gives: a two-level skip announces sections that do not exist.
 *  - **The four benefits became a `<ul>`/`<li>`** with the classes untouched.
 *  - **The `<section>` is labelled by its heading**, so it is a named region landmark.
 *
 * ## Copy that is wrong and was *not* changed
 *
 * Two of the four benefit paragraphs say **"AverageInvestor"** — the old product name — on
 * a page whose `Hero` says "Anvex". Ported verbatim, because the marketing copy is
 * Stephen's; flagged in the ANV-32 report as the one thing on this page that should
 * probably change before anybody reads it.
 */

/** The four benefits, in the old file's order. Copy verbatim, `AverageInvestor` included. */
const BENEFITS = [
  {
    key: 'research',
    title: 'Streamlined Research',
    body: 'AverageInvestor eliminates the complexity of traditional financial analysis tools. By using our intuitive platform, investors can quickly access comprehensive stock data and insights, saving valuable time and reducing confusion.',
  },
  {
    key: 'access',
    title: 'Instant Market Access',
    body: 'With AverageInvestor, investors have immediate access to real-time market data at their fingertips. This on-demand availability allows traders to respond to market movements faster, making them more effective in their investment decisions.',
  },
  {
    key: 'windows',
    title: 'Multi-Window Research Environment',
    body: 'Experience desktop-like functionality with multiple research windows. Compare stocks side-by-side, keep charts open while analyzing fundamentals, and organize your workspace like a professional trading platform - all in your browser.',
  },
  {
    key: 'journal',
    title: 'AI-Powered Trade Journaling',
    body: 'Track your trades with intelligent analysis and performance reviews. Our AI system helps you learn from your investment patterns, identify strengths and weaknesses, and continuously improve your research and decision-making process.',
  },
]

/**
 * What sits in the gradient panel until ANV-35 fills it.
 *
 * Decorative and textless on purpose: `aria-hidden` keeps it out of the accessibility tree
 * entirely, so it adds no words to the page and a screen-reader user is not told about a
 * demo they cannot operate.
 */
function WorkflowDemoPlaceholder() {
  const tile =
    'rounded border border-neutral-300/60 bg-neutral-100/40 dark:border-neutral-700/60 dark:bg-neutral-800/40'

  return (
    <div
      data-testid="workflow-demo-placeholder"
      aria-hidden="true"
      className="flex h-full w-full flex-col gap-2"
    >
      <div className={`h-6 ${tile}`} />
      <div className="flex flex-1 gap-2">
        <div className={`flex-1 ${tile}`} />
        <div className={`w-1/3 ${tile}`} />
      </div>
      <div className={`h-1/4 ${tile}`} />
    </div>
  )
}

/**
 * @param {{demo?: React.ReactNode}} props `demo` is ANV-35's `<InteractiveDesktop />`.
 */
export default function Workflow({ demo = null }) {
  return (
    <section id="workflow" aria-labelledby="workflow-heading" className="mt-20">
      <h2
        id="workflow-heading"
        className="mt-6 text-center font-gothic text-3xl font-medium tracking-wide sm:text-5xl lg:text-6xl"
      >
        Smart investing through
        <span className="text-5xl font-xl text-brand-600 dark:text-brand-400">
          {' '}
          Technology
        </span>
      </h2>

      <div className="flex flex-wrap justify-center lg:items-stretch">
        <div className="w-full p-2 lg:w-1/2">
          <div
            data-testid="workflow-demo-panel"
            className="relative h-96 overflow-hidden rounded-lg bg-gradient-to-r from-brand-500/10 to-brand-700/10 p-4 lg:h-full dark:from-brand-400/10 dark:to-brand-600/10"
          >
            {demo ?? <WorkflowDemoPlaceholder />}
          </div>
        </div>

        <ul className="w-full pt-12 lg:w-1/2">
          {BENEFITS.map(({ key, title, body }) => (
            <li key={key} className="mb-12 flex">
              <div className="mx-6 h-10 w-10 items-center justify-center rounded-full bg-neutral-100 p-2 text-green-500 dark:bg-neutral-900">
                <CheckCircleIcon className="block h-6 w-6" />
              </div>
              <div>
                <h3 className="mb-2 mt-1 font-gothic text-xl font-medium">{title}</h3>
                <p className="mb-10 p-2 font-gothic text-md font-medium text-neutral-600 dark:text-neutral-400">
                  {body}
                </p>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </section>
  )
}
