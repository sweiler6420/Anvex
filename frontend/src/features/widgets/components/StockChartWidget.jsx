import { useEffect, useRef, useState } from 'react'

import { useContainerSize as useContainerSizeHook } from '@features/desktop'
import { toApiError } from '@lib/api'

import { fetchStockSeries } from '../api'
import LineChart from './LineChart'
import WidgetFrame from './WidgetFrame'

/**
 * A security's candles, fetched and drawn (ANV-34). The fetching half of
 * `AverageInvestorWeb/src/components/shared/widgets/StockChart.jsx` (76 lines).
 *
 * The original fetched a tab-separated file of Microsoft prices from `cdn.rawgit.com` — a
 * CDN that was **shut down in October 2019** — through a `getData` it imported from the bare
 * specifier `'utils'`, which resolves to a package rather than to the sibling file. So the
 * component as written could not load, and if it had, it would have plotted somebody else's
 * static demo data. What is ported is the *shape*: measure, fetch, draw.
 *
 * ## The two seams, and why they are props
 *
 *  - **`useContainerSize` is a prop defaulting to the real hook**, exactly as
 *    `BinPackingLayout` takes it (ANV-33). jsdom has no layout and the `ResizeObserver` stub
 *    in `src/test/setup.js` is deliberately inert, so a test that needs a chart with a size
 *    has to invent one — and this is what puts the invented number *beside the assertion it
 *    supports* rather than in a global mock two files away.
 *  - **The network is not a seam.** It is MSW at the boundary, per CLAUDE.md §5: stubbing
 *    `fetchStockSeries` would mock away the `authApi` interceptors, the error envelope and
 *    the `Number()` conversion, which is most of what there is to get wrong here.
 *
 * ## Cancellation
 *
 * The effect aborts its request on cleanup, and `request_cancelled` is **swallowed** — it is
 * a component unmounting, not a failure (ANV-24/25). The original used an `isMounted` flag,
 * which stops the `setState` but lets the request run to completion; an `AbortSignal` stops
 * the request, which is what a window being closed mid-load should do.
 */

/** `DEFAULT_PAGE_LIMIT` is 50 and `MAX_PAGE_LIMIT` is 200; a chart wants the ceiling. */
export const DEFAULT_POINT_LIMIT = 200

/**
 * @param {object} props
 * @param {string} [props.ticker] the security, by symbol
 * @param {string} [props.stockId] the security, by id — takes precedence over `ticker`
 * @param {string} [props.start] earliest trading date, inclusive (`YYYY-MM-DD`)
 * @param {string} [props.end] latest trading date, inclusive
 * @param {number} [props.limit]
 * @param {Function} [props.useContainerSize] the measurement seam; see above
 */
export default function StockChartWidget({
  ticker = 'AAPL',
  stockId,
  start,
  end,
  limit = DEFAULT_POINT_LIMIT,
  useContainerSize = useContainerSizeHook,
}) {
  const containerRef = useRef(null)
  const { width, height } = useContainerSize(containerRef)

  const [state, setState] = useState({ status: 'loading', series: [], error: null })

  useEffect(() => {
    const controller = new AbortController()
    setState({ status: 'loading', series: [], error: null })

    fetchStockSeries({ stockId, ticker, start, end, limit, signal: controller.signal })
      .then(({ series }) => setState({ status: 'ready', series, error: null }))
      .catch((err) => {
        const apiError = toApiError(err)
        // An unmount is not a failure, and reporting one would flash an error banner over a
        // window the user has just closed.
        if (apiError.code === 'request_cancelled') return
        setState({ status: 'error', series: [], error: apiError })
      })

    return () => controller.abort()
  }, [stockId, ticker, start, end, limit])

  const name = stockId ? (ticker ?? 'Price') : ticker

  return (
    <WidgetFrame label={`${name} price chart`} testId="stock-chart-widget">
      <div ref={containerRef} className="min-h-0 min-w-0 flex-1" data-testid="stock-chart-box">
        {state.status === 'loading' ? (
          <p className="text-neutral-500 dark:text-neutral-400" data-testid="stock-chart-loading">
            Loading {name}…
          </p>
        ) : null}

        {state.status === 'error' ? (
          <p role="alert" className="text-neutral-900 dark:text-neutral-100">
            {state.error.message}
          </p>
        ) : null}

        {state.status === 'ready' ? (
          <LineChart series={state.series} width={width} height={height} label={name} />
        ) : null}
      </div>
    </WidgetFrame>
  )
}
