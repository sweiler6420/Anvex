import { useEffect, useId, useState } from 'react'

import { toApiError } from '@lib/api'

import { fetchStocks } from '../api'

/**
 * The securities reference table, as a list you can open a chart from (ANV-36).
 *
 * This is the third of the three endpoints `/research` is built on — `GET /v1/stocks` —
 * and it is the one the widgets do not reach. It exists because a research desktop whose
 * chart is permanently `AAPL` is a demo with a session attached: the palette's chart chip
 * cannot ask *which* security, because a chip is a fixed template. A list of the securities
 * the API actually has data for can, and the answer goes straight into
 * `stockChartWindow({stockId, ticker})`.
 *
 * ## What it is not
 *
 * There is **no search box and no paging**, and both absences are deliberate rather than
 * unfinished. The endpoint supports `search`, `limit` and `offset`; the panel asks for one
 * default page ordered by ticker and *says how many rows exist* beside it, so a table
 * larger than the page is visible as a fact rather than implied to be the whole thing.
 * Adding either is additive and neither is invented product — `total` is what tells the
 * next person it is worth doing.
 *
 * There is also no price, no change and no sparkline on a row. `StockOut` carries
 * `{stock_id, ticker_symbol, company, market, isin}` and no quote, and ANV-34 already
 * recorded what a hardcoded `$320` did to the old watchlist: a number in the position where
 * a price goes is worse than no number.
 *
 * ## A row is a button, not a drag and not a link
 *
 * ANV-34's rule: where the API is keyed on an entity and a destination, the affordance is a
 * button. Opening a chart *is* "this security, onto the desktop", it is keyboard- and
 * touch-operable by construction, and the accessible name says what pressing it does
 * (`Open NVDA price chart`) rather than naming the thing it is next to. It is not a `<Link>`
 * because nothing about it changes the location — the desktop is the destination.
 *
 * The announcement is deliberately **not** made here: `InteractiveDesktop.openWindow` owns
 * it, so a window added from a chip and a window added from this list say the same thing in
 * the same live region, and the "no room" refusal cannot be reported by one and swallowed by
 * the other.
 */

/** `DEFAULT_PAGE_LIMIT` on the API is 50; asking explicitly documents what a page is here. */
export const DEFAULT_SECURITY_LIMIT = 50

/**
 * @param {object} props
 * @param {(security: object) => void} [props.onOpen] called with the `StockOut` row whose
 *   button was pressed. With no handler the rows render as plain text, so the panel is
 *   still readable on a page that has nowhere to put a chart.
 * @param {number} [props.limit]
 */
export default function SecuritiesPanel({ onOpen, limit = DEFAULT_SECURITY_LIMIT }) {
  const [state, setState] = useState({ status: 'loading', items: [], total: 0, error: null })
  const headingId = useId()

  useEffect(() => {
    const controller = new AbortController()
    setState({ status: 'loading', items: [], total: 0, error: null })

    fetchStocks({ limit, signal: controller.signal })
      .then(({ items, total }) => setState({ status: 'ready', items, total, error: null }))
      .catch((err) => {
        const apiError = toApiError(err)
        // An unmount is not a failure (ANV-24/25) — swallowing means changing nothing, not
        // clearing what is on screen.
        if (apiError.code === 'request_cancelled') return
        setState({ status: 'error', items: [], total: 0, error: apiError })
      })

    return () => controller.abort()
  }, [limit])

  const { status, items, total, error } = state

  return (
    <section
      aria-labelledby={headingId}
      data-testid="securities-panel"
      className="flex min-h-0 flex-col rounded-xl border border-neutral-200 bg-white p-4 dark:border-neutral-700 dark:bg-neutral-900"
    >
      <h3
        id={headingId}
        className="mb-2 font-gothic font-medium text-neutral-900 dark:text-neutral-200"
      >
        Securities
      </h3>

      {/* Rendered unconditionally and left empty (ANV-29): a live region has to be in the
          accessibility tree before its text arrives. */}
      <p
        role="alert"
        data-testid="securities-error"
        className="text-sm text-neutral-900 dark:text-neutral-100"
      >
        {status === 'error' ? error.message : ''}
      </p>

      {status === 'loading' ? (
        <p className="text-sm text-neutral-500 dark:text-neutral-400">Loading securities…</p>
      ) : null}

      {status === 'ready' && items.length === 0 ? (
        <p className="text-sm text-neutral-500 dark:text-neutral-400">
          No securities have been loaded yet.
        </p>
      ) : null}

      {status === 'ready' && items.length > 0 ? (
        <>
          <ul className="min-h-0 divide-y divide-neutral-200 overflow-y-auto dark:divide-neutral-700">
            {items.map((security) => (
              <li key={security.stock_id}>
                <SecurityRow security={security} onOpen={onOpen} />
              </li>
            ))}
          </ul>

          {/* The truthful denominator. `total` counts every matching row regardless of the
              window (CLAUDE.md §4), so this is what stops one page reading as the table. */}
          <p
            data-testid="securities-count"
            className="mt-2 text-sm text-neutral-500 dark:text-neutral-400"
          >
            Showing {items.length} of {total}.
          </p>
        </>
      ) : null}
    </section>
  )
}

/**
 * One security. A `<button>` when there is somewhere to open it, plain text otherwise —
 * a control that does nothing is worse than no control (ANV-32's `<a href="#">` rule).
 */
function SecurityRow({ security, onOpen }) {
  const label = (
    <>
      <span className="font-medium text-neutral-900 dark:text-neutral-200">
        {security.ticker_symbol}
      </span>{' '}
      <span className="text-neutral-600 dark:text-neutral-400">{security.company}</span>{' '}
      <span className="text-sm text-neutral-500 dark:text-neutral-400">{security.market}</span>
    </>
  )

  if (!onOpen) return <p className="py-2">{label}</p>

  return (
    <button
      type="button"
      // WCAG 2.5.3: the visible label (`NVDA NVIDIA Corporation NASDAQ`) is a substring of
      // nothing here, so the accessible name is written to *contain* the visible ticker and
      // to say what pressing does. `aria-label` replaces the content for a screen reader,
      // which is why the ticker is repeated inside it rather than left to the markup.
      aria-label={`Open ${security.ticker_symbol} price chart`}
      onClick={() => onOpen(security)}
      className="w-full rounded px-1 py-2 text-left hover:bg-neutral-100 focus:outline-none focus:ring-2 focus:ring-brand-500 dark:hover:bg-neutral-800"
    >
      {label}
    </button>
  )
}
