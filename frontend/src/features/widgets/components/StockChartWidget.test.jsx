import { render, screen } from '@testing-library/react'
import { http } from 'msw'
import { describe, expect, it } from 'vitest'

import { apiUrl } from '@lib/env'
import { errorResponse, pageResponse } from '@test/msw/handlers'
import { server } from '@test/msw/server'

import StockChartWidget from './StockChartWidget'

/**
 * ANV-34 — the chart widget, at the network boundary.
 *
 * ## Which of these prove behaviour and which prove wiring
 *
 * The **network** is real: MSW answers the HTTP the real `authApi` sends, so the URL, the
 * query string, the `Page` envelope and the quoted-string prices all travel the path they
 * travel in production (CLAUDE.md §5 — never stub `fetch`/`axios`). Those assertions are
 * **REAL**, and the price ones would fail if `api.js` handed back the raw items.
 *
 * The **size** is fabricated. jsdom has no layout and the `ResizeObserver` stub is inert, so
 * a chart with axes only exists here because a test typed `640×320` into the
 * `useContainerSize` prop. Those assertions prove the widget *passes what it measured to the
 * chart*, not that a real panel is that size.
 *
 * The unmeasured test needs no fabrication at all and is the one that shows what the widget
 * does with a genuine jsdom measurement.
 */

const size = (width, height) => () => ({ width, height })

/** Three candles, priced so their string ordering is the reverse of their numeric one. */
const CANDLES = [
  candle('2026-01-05T09:30:00', '9.5'),
  candle('2026-01-05T09:45:00', '10.2'),
  candle('2026-01-05T10:00:00', '9.75'),
]

function candle(datetime, close) {
  return {
    stock_id: '11111111-1111-4111-8111-111111111111',
    datetime,
    open_price: close,
    high_price: close,
    low_price: close,
    close_price: close,
    volume: 1000,
  }
}

/** Mocks the by-ticker route and records what it was asked for. */
function mockTicker(ticker, items = CANDLES) {
  const seen = { url: null, count: 0 }
  server.use(
    http.get(apiUrl(`/v1/stocks/by-ticker/${ticker}/data`), ({ request }) => {
      seen.count += 1
      seen.url = new URL(request.url)
      return pageResponse(items, { total: items.length, limit: 200 })
    }),
  )
  return seen
}

describe('StockChartWidget — unmeasured (REAL: jsdom reports 0×0)', () => {
  it('renders an unmeasured chart rather than a fabricated one', async () => {
    mockTicker('AAPL')
    render(<StockChartWidget ticker="AAPL" />)

    const chart = await screen.findByTestId('line-chart')
    expect(chart).toHaveAttribute('data-mode', 'unmeasured')
  })
})

describe('StockChartWidget — the request', () => {
  it('asks the by-ticker route for the page ceiling', async () => {
    const seen = mockTicker('AAPL')
    render(<StockChartWidget ticker="AAPL" useContainerSize={size(640, 320)} />)

    await screen.findByTestId('price-line')

    expect(seen.count).toBe(1)
    expect(seen.url.pathname).toBe('/v1/stocks/by-ticker/AAPL/data')
    expect(seen.url.searchParams.get('limit')).toBe('200')
  })

  it('uses the id route when given a stockId', async () => {
    const stockId = '11111111-1111-4111-8111-111111111111'
    const seen = { url: null }
    server.use(
      http.get(apiUrl(`/v1/stocks/${stockId}/data`), ({ request }) => {
        seen.url = new URL(request.url)
        return pageResponse(CANDLES, { limit: 200 })
      }),
    )

    render(<StockChartWidget stockId={stockId} useContainerSize={size(640, 320)} />)
    await screen.findByTestId('price-line')

    expect(seen.url.pathname).toBe(`/v1/stocks/${stockId}/data`)
  })

  it('passes an explicit date range through', async () => {
    const seen = mockTicker('NVDA')
    render(
      <StockChartWidget
        ticker="NVDA"
        start="2026-01-05"
        end="2026-01-09"
        useContainerSize={size(640, 320)}
      />,
    )
    await screen.findByTestId('price-line')

    expect(seen.url.searchParams.get('start')).toBe('2026-01-05')
    expect(seen.url.searchParams.get('end')).toBe('2026-01-09')
  })
})

describe('StockChartWidget — the prices survive the wire as numbers (REAL)', () => {
  it('describes the range numerically, not lexicographically', async () => {
    mockTicker('AAPL')
    render(<StockChartWidget ticker="AAPL" useContainerSize={size(640, 320)} />)

    // "9.5", "10.2", "9.75" as strings sort to min "10.2"; as numbers the range is 9.5–10.2.
    // A `fetchStockSeries` that handed back the raw `Page.items` would describe `—` to `—`.
    const chart = await screen.findByRole('img', {
      name: /AAPL: 3 points from 2026-01-05T09:30:00 to 2026-01-05T10:00:00\. Close ranged 9\.50 to 10\.20, ending at 9\.75\./,
    })
    expect(chart).toBeInTheDocument()
  })

  it('plots the candles in wall-clock order', async () => {
    // Deliberately delivered newest-first: the series is sorted by nominal epoch, so the
    // path must still start at 09:30 and the description must still name it first.
    mockTicker('AAPL', [...CANDLES].reverse())
    render(<StockChartWidget ticker="AAPL" useContainerSize={size(640, 320)} />)

    await screen.findByRole('img', { name: /from 2026-01-05T09:30:00 to 2026-01-05T10:00:00/ })
  })
})

describe('StockChartWidget — the states around the chart', () => {
  it('says it is loading before the response lands', () => {
    mockTicker('AAPL')
    render(<StockChartWidget ticker="AAPL" useContainerSize={size(640, 320)} />)

    expect(screen.getByTestId('stock-chart-loading')).toHaveTextContent('Loading AAPL')
  })

  it('shows the API message on a failure, in an alert', async () => {
    server.use(
      http.get(apiUrl('/v1/stocks/by-ticker/AAPL/data'), () =>
        errorResponse('external_service_error', 'The price vendor is unavailable.', {
          status: 502,
        }),
      ),
    )

    render(<StockChartWidget ticker="AAPL" useContainerSize={size(640, 320)} />)

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The price vendor is unavailable.',
    )
  })

  it('says the range is empty rather than drawing nothing', async () => {
    mockTicker('AAPL', [])
    render(<StockChartWidget ticker="AAPL" useContainerSize={size(640, 320)} />)

    const chart = await screen.findByTestId('line-chart')
    expect(chart).toHaveAttribute('data-mode', 'empty')
  })

  it('does not report an aborted request as a failure', async () => {
    // Two handlers that never answer, so the only thing that can resolve is the abort the
    // effect's cleanup performs when `ticker` changes. `request_cancelled` is a component
    // moving on, not a failure (ANV-24/25) — without the swallow the cleanup's rejection
    // paints an error banner over a chart that is still loading.
    const hang = () => new Promise(() => {})
    server.use(
      http.get(apiUrl('/v1/stocks/by-ticker/AAPL/data'), hang),
      http.get(apiUrl('/v1/stocks/by-ticker/NVDA/data'), hang),
    )

    const { rerender } = render(
      <StockChartWidget ticker="AAPL" useContainerSize={size(640, 320)} />,
    )
    rerender(<StockChartWidget ticker="NVDA" useContainerSize={size(640, 320)} />)

    await new Promise((resolve) => setTimeout(resolve, 50))

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByTestId('stock-chart-loading')).toHaveTextContent('Loading NVDA')
  })

  it('is named as a region so a desktop full of widgets is navigable', async () => {
    mockTicker('AAPL')
    render(<StockChartWidget ticker="AAPL" useContainerSize={size(640, 320)} />)

    expect(screen.getByRole('region', { name: 'AAPL price chart' })).toBeInTheDocument()
    await screen.findByTestId('price-line')
  })
})
