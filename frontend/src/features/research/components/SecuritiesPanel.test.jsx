import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http } from 'msw'
import { describe, expect, it, vi } from 'vitest'

import { apiUrl } from '@lib/env'
import { errorResponse, pageResponse } from '@test/msw/handlers'
import { server } from '@test/msw/server'

import { STOCKS_PATH } from '../api'
import SecuritiesPanel from './SecuritiesPanel'

/**
 * The securities list (ANV-36).
 *
 * **Every test here proves real behaviour.** A list has no box model: what it requests,
 * what it renders, what it says when the request fails and what it hands its caller are all
 * observable in an unmeasured jsdom exactly as in a browser. Nothing below fabricates a
 * measurement, and nothing stubs `axios` — MSW answers the request the real `authApi` sent.
 */

const stock = (ticker, company) => ({
  stock_id: `id-${ticker}`,
  ticker_symbol: ticker,
  company,
  market: 'NASDAQ',
  isin: null,
})

const listing = (items, options) =>
  server.use(http.get(apiUrl(STOCKS_PATH), () => pageResponse(items, options)))

describe('loading the securities', () => {
  it('says so while it is loading', () => {
    listing([stock('AAPL', 'Apple Inc.')])
    render(<SecuritiesPanel />)

    expect(screen.getByText('Loading securities…')).toBeInTheDocument()
  })

  it('renders each security s ticker, company and market', async () => {
    listing([stock('AAPL', 'Apple Inc.'), stock('NVDA', 'NVIDIA Corporation')])
    render(<SecuritiesPanel />)

    const rows = await screen.findAllByRole('listitem')
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('AAPL')
    expect(rows[0]).toHaveTextContent('Apple Inc.')
    expect(rows[0]).toHaveTextContent('NASDAQ')
  })

  it('renders the securities in the order the server sent them', async () => {
    // The endpoint orders by ticker; a client that sorted would be asserting an order the
    // server did not (ANV-34's rule for the watchlist, applied to a second collection).
    listing([stock('NVDA', 'NVIDIA Corporation'), stock('AAPL', 'Apple Inc.')])
    render(<SecuritiesPanel />)

    const rows = await screen.findAllByRole('listitem')
    expect(rows.map((row) => row.textContent.trim().slice(0, 4))).toEqual(['NVDA', 'AAPL'])
  })

  it('says how many rows exist, not just how many it drew', async () => {
    // `total` counts every matching row regardless of the window (CLAUDE.md §4). Without
    // this line one page of fifty reads as the whole reference table.
    listing([stock('AAPL', 'Apple Inc.')], { total: 412 })
    render(<SecuritiesPanel />)

    expect(await screen.findByTestId('securities-count')).toHaveTextContent('Showing 1 of 412.')
  })

  it('asks for one page rather than the whole table', async () => {
    let seen = null
    server.use(
      http.get(apiUrl(STOCKS_PATH), ({ request }) => {
        seen = new URL(request.url).searchParams.get('limit')
        return pageResponse([])
      }),
    )
    render(<SecuritiesPanel limit={7} />)

    await waitFor(() => expect(seen).toBe('7'))
  })

  it('distinguishes an empty table from a failure', async () => {
    listing([])
    render(<SecuritiesPanel />)

    expect(await screen.findByText('No securities have been loaded yet.')).toBeInTheDocument()
    expect(screen.getByTestId('securities-error')).toHaveTextContent('')
  })
})

describe('when the request fails', () => {
  it('shows the API s message in a live region that was already there', async () => {
    // The slot is rendered empty from the first paint (ANV-29): a live region has to be in
    // the accessibility tree before its text arrives.
    listing([])
    const { unmount } = render(<SecuritiesPanel />)
    expect(screen.getByTestId('securities-error')).toBeInTheDocument()
    unmount()

    server.use(
      http.get(apiUrl(STOCKS_PATH), () =>
        errorResponse('internal_error', 'An unexpected error occurred.', { status: 500 }),
      ),
    )
    render(<SecuritiesPanel />)

    expect(await screen.findByRole('alert')).toHaveTextContent('An unexpected error occurred.')
    expect(screen.queryByRole('list')).not.toBeInTheDocument()
  })

  it('does not report an aborted request as a failure', async () => {
    // A handler that never answers, so the only thing that can resolve is the abort the
    // effect's cleanup performs when `limit` changes. **Re-rendered rather than unmounted,
    // deliberately**: after an unmount there is no DOM left, so `queryByRole('alert')` is
    // null whether or not `request_cancelled` was swallowed, and the test would pass
    // vacuously (ANV-34 hit the same trap in `StockChartWidget.test.jsx`). Still mounted,
    // an unswallowed cancellation paints an error over a panel that is still loading.
    server.use(http.get(apiUrl(STOCKS_PATH), () => new Promise(() => {})))

    const { rerender } = render(<SecuritiesPanel limit={10} />)
    rerender(<SecuritiesPanel limit={20} />)

    await new Promise((resolve) => setTimeout(resolve, 50))

    expect(screen.getByTestId('securities-error')).toHaveTextContent('')
    expect(screen.getByText('Loading securities…')).toBeInTheDocument()
  })
})

describe('opening a security', () => {
  it('hands the whole StockOut row to its caller', async () => {
    // The row, not a ticker: `stockChartWindow` keys the request on `stock_id` and labels
    // the chart with the symbol, and an implementation that passed only the string would
    // force a second lookup the caller has no way to do.
    const onOpen = vi.fn()
    const row = stock('NVDA', 'NVIDIA Corporation')
    listing([row])
    const user = userEvent.setup()
    render(<SecuritiesPanel onOpen={onOpen} />)

    await user.click(await screen.findByRole('button', { name: 'Open NVDA price chart' }))

    expect(onOpen).toHaveBeenCalledTimes(1)
    expect(onOpen).toHaveBeenCalledWith(row)
  })

  it('is reachable from the keyboard', async () => {
    // ANV-29's rule for the password toggle: `user.click` passes on a `<div onClick>` shim
    // and this does not.
    const onOpen = vi.fn()
    listing([stock('AAPL', 'Apple Inc.')])
    const user = userEvent.setup()
    render(<SecuritiesPanel onOpen={onOpen} />)

    const button = await screen.findByRole('button', { name: 'Open AAPL price chart' })
    button.focus()
    await user.keyboard('{Enter}')

    expect(onOpen).toHaveBeenCalledTimes(1)
  })

  it('renders no control at all when there is nowhere to open a chart', async () => {
    // A button that does nothing is indistinguishable from a broken one (ANV-32's
    // `<a href="#">` rule), so with no handler the rows are plain text.
    listing([stock('AAPL', 'Apple Inc.')])
    render(<SecuritiesPanel />)

    const rows = await screen.findAllByRole('listitem')
    expect(within(rows[0]).queryByRole('button')).not.toBeInTheDocument()
    expect(rows[0]).toHaveTextContent('AAPL')
  })
})
