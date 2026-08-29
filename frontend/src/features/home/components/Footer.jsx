import { Link } from '@tanstack/react-router'

import { HOME_ROUTE } from '@routes/paths'

/**
 * The home page's footer (ANV-32), ported from
 * `AverageInvestorWeb/src/components/home/Footer.jsx` (66 lines).
 *
 * ---------------------------------------------------------------------------------------
 * ## Five of its eight links were broken, in two different ways
 *
 *  - **Five were `<a href="#features">`-style fragment anchors.** Outside the router, so
 *    the hash would be written onto `window.location` without TanStack seeing it and
 *    `Header`'s active-item rule — which reads the *router's* hash — would never update.
 *    Now `<Link to={HOME_ROUTE} hash="…">`, the same URL through the router.
 *  - **Three were `<a href="#">`: links to nowhere.** "Investment Guides", "Investment
 *    Forums" and "Educational Resources" name pages that do not exist in Anvex. `href="#"`
 *    is not a placeholder, it is a control that takes focus, announces itself as a link,
 *    and scrolls the reader to the top of the page when they press it. They are rendered
 *    as plain text here: the words stay, the promise does not. **When those pages exist,
 *    give the entry a `hash` or a `to` and it becomes a link again** — that is the only
 *    edit needed.
 *
 * ## Contrast
 *
 * `hover:text-black` had no dark counterpart, so hovering a footer link in dark mode turned
 * it black on a near-black background — an item that disappears at the moment you point at
 * it. `dark:hover:text-white` added; the light theme is untouched.
 *
 * ## Where this lives, and why it is not in `Layout`
 *
 * It renders inside `HomePage`, i.e. inside `<main>` — which means it is **not** a
 * `contentinfo` landmark (HTML-AAM only maps `<footer>` to `contentinfo` when it is not
 * inside `main`/`article`/`aside`/`nav`/`section`). That is correct rather than an
 * oversight: every link it has is a fragment of the *home* route, so it is this page's
 * footer, not the site's. `Layout.jsx`'s comment anticipating "the footer ANV-32 adds"
 * refers to the flex column having room for one. A genuine site-wide `contentinfo` — legal,
 * status, the things every route owes a visitor — is a different component with different
 * content, and it is not this one.
 *
 * **Not changed, deliberately:** `border-neutral-700` has no light-mode variant, so the top
 * border is near-black in the light theme. That is a visual decision, not an accessibility
 * one; see the ANV-32 report.
 */

/**
 * The three columns. An entry with a `hash` is a `<Link>` into the home page; an entry
 * without one is a destination Anvex does not have yet, and renders as text.
 */
const COLUMNS = [
  {
    key: 'resources',
    heading: 'Resources',
    items: [
      { key: 'contact', label: 'Contact Us', hash: 'contact' },
      { key: 'guides', label: 'Investment Guides' },
    ],
  },
  {
    key: 'platform',
    heading: 'Platform',
    items: [
      { key: 'features', label: 'Features', hash: 'features' },
      { key: 'workflow', label: 'How It Works', hash: 'workflow' },
      { key: 'pricing', label: 'Pricing', hash: 'pricing' },
    ],
  },
  {
    key: 'community',
    heading: 'Community',
    items: [
      { key: 'forums', label: 'Investment Forums' },
      { key: 'education', label: 'Educational Resources' },
    ],
  },
]

const ITEM_CLASS = 'font-gothic font-medium text-neutral-500'

export default function Footer() {
  return (
    <footer className="mt-20 border-t border-neutral-700 py-10">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-3">
        {COLUMNS.map((column) => (
          <div key={column.key}>
            <h3 className="mb-4 font-gothic text-md font-semibold font-demi">
              {column.heading}
            </h3>
            <ul className="space-y-2">
              {column.items.map((item) => (
                <li key={item.key}>
                  {item.hash === undefined ? (
                    <span className={ITEM_CLASS}>{item.label}</span>
                  ) : (
                    <Link
                      to={HOME_ROUTE}
                      hash={item.hash}
                      className={`${ITEM_CLASS} hover:text-black dark:hover:text-white`}
                    >
                      {item.label}
                    </Link>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </footer>
  )
}
