import { Link } from '@tanstack/react-router'

import { RESEARCH_ROUTE } from '@routes/paths'

/**
 * `/portfolio` — a page for a feature the backend does not have (ANV-36).
 *
 * Ported from `AverageInvestorWeb/src/components/authenticated/Portfolio.jsx` (15 lines): a
 * heading, a panel, and the single line **"Portfolio content goes here..."**.
 *
 * ---------------------------------------------------------------------------------------
 * ## Why the one line of copy is the one thing not ported verbatim
 *
 * ANV-32's rule is that copy belongs to the page's owner and a port reports it rather than
 * improving it — and this is not copy. "Portfolio content goes here..." is a note the
 * author wrote **to themselves**, in the position where a sentence addressed to the reader
 * goes. It is the same category as the `<a href="#">` ANV-32 refused to transcribe: a
 * developer's placeholder that behaves, once shipped, as though it were the real thing.
 *
 * So ANV-31's rule applies instead — *a screen for a state the app cannot currently reach
 * says so*, in the words `UnauthorizedPage` uses for a permission system that does not
 * exist. And the state here is genuinely unreachable, verified rather than assumed: the API
 * has **no holdings model at all**. There is no positions table in `app/models/`, no
 * `/v1/positions` or `/v1/portfolios` route, no cost basis, no quantity, no transaction and
 * no quote — `StockOut` carries `{stock_id, ticker_symbol, company, market, isin}` and
 * nothing that could be multiplied by a share count. A page that showed a value, an
 * allocation ring or an empty holdings table with column headers would be describing a
 * product this application does not have, which is the mistake the old `/unauthorized`
 * copy made about roles.
 *
 * **Nothing here is invented and nothing is fetched.** There is no request to make: this is
 * the one page in the authenticated half that reaches no endpoint, and a test asserts that
 * by counting requests rather than by trusting the absence of an import.
 *
 * ## What is kept from the original
 *
 * The structure, exactly: the container, the `<h1>Portfolio</h1>` with its `font-gothic
 * text-4xl font-xl`, and the bordered panel. `data-testid="route-portfolio"` is the id
 * `RoutePlaceholder` generated, so ANV-27's routing tests need no edit.
 *
 * The link out is ANV-31's rule too: a page with no useful action of its own offers a
 * destination that always exists, rather than leaving the reader on a dead end.
 */
export default function PortfolioPage() {
  return (
    <section className="container mx-auto px-4 py-8" data-testid="route-portfolio">
      <h1 className="mb-6 font-gothic text-4xl font-xl">Portfolio</h1>

      <div className="rounded-xl border border-neutral-200 bg-white p-6 text-neutral-900 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-200">
        <p className="mb-4">
          Anvex does not track holdings yet. Nothing in the API records the shares you own,
          what you paid for them or what they are worth, so there is nothing for this page to
          show — it is not empty because your portfolio is empty.
        </p>
        <p>
          What does work today is research:{' '}
          <Link
            to={RESEARCH_ROUTE}
            className="font-medium text-brand-600 underline hover:text-brand-500 dark:text-brand-400"
          >
            price charts and your watchlists
          </Link>
          .
        </p>
      </div>
    </section>
  )
}
