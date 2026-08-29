import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import { apiUrl } from '@lib/env'
import { errorResponse, pageResponse } from '@test/msw/handlers'
import { server } from '@test/msw/server'

import WatchlistWidget from './WatchlistWidget'

/**
 * ANV-34 — the watchlist widget, and the reorder that is the frontend half of ANV-15.
 *
 * **Everything in this file proves real behaviour.** There is no measurement anywhere in the
 * widget — it is a list of buttons — so nothing is fabricated and jsdom's missing layout
 * costs nothing. MSW answers the HTTP the real `authApi` sends, so the URL, the method and
 * the body asserted below are the ones the backend would receive.
 *
 * The two assertions that carry the ticket:
 *
 *  - **`sends the stock id and the destination, and nothing else`** — the old client sent
 *    `current_index` too, a stale client-side belief the server could not verify, and when
 *    it was wrong a different stock moved.
 *  - **`renders the server's order, not a local splice`** — the endpoint returns the whole
 *    reordered watchlist precisely so a client need not guess. The mock deliberately returns
 *    an order no local splice would produce, so an implementation that optimistically
 *    reordered its own array fails here and passes everywhere else.
 */

const WATCHLIST_ID = '22222222-2222-4222-8222-222222222222'
const USER_ID = '33333333-3333-4333-8333-333333333333'

const STOCKS = {
  AAPL: {
    stock_id: 'aaaaaaa1-0000-4000-8000-000000000001',
    ticker_symbol: 'AAPL',
    company: 'Apple Inc.',
    market: 'NASDAQ',
    isin: 'US0378331005',
  },
  NVDA: {
    stock_id: 'aaaaaaa1-0000-4000-8000-000000000002',
    ticker_symbol: 'NVDA',
    company: 'NVIDIA Corporation',
    market: 'NASDAQ',
    isin: null,
  },
  MSFT: {
    stock_id: 'aaaaaaa1-0000-4000-8000-000000000003',
    ticker_symbol: 'MSFT',
    company: 'Microsoft Corporation',
    market: 'NASDAQ',
    isin: null,
  },
}

const detail = (tickers) => ({
  watchlist_id: WATCHLIST_ID,
  user_id: USER_ID,
  title: 'Semiconductors',
  entries: tickers.map((ticker, position) => ({
    watchlist_id: WATCHLIST_ID,
    stock_id: STOCKS[ticker].stock_id,
    position,
    stock: STOCKS[ticker],
  })),
})

/**
 * Mock the two reads and the reorder.
 *
 * `afterMove` is what the `PATCH` answers with, so a test can make the server's order differ
 * from anything the client could have guessed.
 */
function mockWatchlist({ order = ['AAPL', 'NVDA', 'MSFT'], afterMove, patch } = {}) {
  const seen = { count: 0, url: null, method: null, body: null }

  server.use(
    http.get(apiUrl('/v1/watchlists'), () =>
      pageResponse([{ watchlist_id: WATCHLIST_ID, user_id: USER_ID, title: 'Semiconductors' }]),
    ),
    http.get(apiUrl(`/v1/watchlists/${WATCHLIST_ID}`), () => HttpResponse.json(detail(order))),
    http.patch(apiUrl(`/v1/watchlists/${WATCHLIST_ID}/stocks/:stockId`), async ({ request }) => {
      seen.count += 1
      seen.url = new URL(request.url)
      seen.method = request.method
      seen.body = await request.json()
      if (patch) return patch()
      return HttpResponse.json(detail(afterMove ?? order))
    }),
  )

  return seen
}

/** The rendered order, read off each row's own test id. */
const rowTickers = () =>
  screen
    .getAllByRole('listitem')
    .map((li) => li.getAttribute('data-testid').replace('watchlist-row-', ''))

describe('WatchlistWidget — reading', () => {
  it('renders the entries in the order the server sent them', async () => {
    mockWatchlist()
    render(<WatchlistWidget />)

    await screen.findByTestId('watchlist-row-AAPL')
    expect(rowTickers()).toEqual(['AAPL', 'NVDA', 'MSFT'])
  })

  it('takes its accessible name from the watchlist title', async () => {
    mockWatchlist()
    render(<WatchlistWidget />)

    expect(await screen.findByRole('region', { name: 'Semiconductors' })).toBeInTheDocument()
  })

  it('resolves the first watchlist when none is named', async () => {
    mockWatchlist()
    render(<WatchlistWidget />)

    await screen.findByTestId('watchlist-row-AAPL')
  })

  it('skips the lookup when given a watchlist id', async () => {
    let listed = 0
    server.use(
      http.get(apiUrl('/v1/watchlists'), () => {
        listed += 1
        return pageResponse([])
      }),
      http.get(apiUrl(`/v1/watchlists/${WATCHLIST_ID}`), () =>
        HttpResponse.json(detail(['AAPL'])),
      ),
    )

    render(<WatchlistWidget watchlistId={WATCHLIST_ID} />)
    await screen.findByTestId('watchlist-row-AAPL')

    expect(listed).toBe(0)
  })

  it('shows no price, because the API carries none', async () => {
    // The original rendered the literal string `$320` on every row.
    mockWatchlist()
    render(<WatchlistWidget />)

    await screen.findByTestId('watchlist-row-AAPL')
    expect(screen.queryByText(/\$\d/)).not.toBeInTheDocument()
  })

  it('is an ordered list, so position is conveyed without being drawn', async () => {
    mockWatchlist()
    render(<WatchlistWidget />)

    await screen.findByTestId('watchlist-row-AAPL')
    expect(screen.getByRole('list').tagName.toLowerCase()).toBe('ol')
  })
})

describe('WatchlistWidget — the reorder', () => {
  it('sends the stock id and the destination, and nothing else', async () => {
    const user = userEvent.setup()
    const seen = mockWatchlist()
    render(<WatchlistWidget />)
    await screen.findByTestId('watchlist-row-AAPL')

    await user.click(screen.getByRole('button', { name: 'Move AAPL down' }))

    await waitFor(() => expect(seen.count).toBe(1))
    expect(seen.method).toBe('PATCH')
    expect(seen.url.pathname).toBe(
      `/v1/watchlists/${WATCHLIST_ID}/stocks/${STOCKS.AAPL.stock_id}`,
    )
    // Exactly one key. `current_index` — the field ANV-15 deleted — has nowhere to live.
    expect(seen.body).toEqual({ position: 1 })
  })

  it('keys the move on the row that was pressed, not on a remembered index', async () => {
    const user = userEvent.setup()
    const seen = mockWatchlist()
    render(<WatchlistWidget />)
    await screen.findByTestId('watchlist-row-NVDA')

    await user.click(screen.getByRole('button', { name: 'Move NVDA up' }))

    await waitFor(() => expect(seen.count).toBe(1))
    expect(seen.url.pathname).toContain(STOCKS.NVDA.stock_id)
    expect(seen.body).toEqual({ position: 0 })
  })

  it('moves the last row up with the destination one above it', async () => {
    const user = userEvent.setup()
    const seen = mockWatchlist()
    render(<WatchlistWidget />)
    await screen.findByTestId('watchlist-row-MSFT')

    await user.click(screen.getByRole('button', { name: 'Move MSFT up' }))

    await waitFor(() => expect(seen.count).toBe(1))
    expect(seen.body).toEqual({ position: 1 })
  })

  it("renders the server's order, not a local splice", async () => {
    const user = userEvent.setup()
    // A local splice of "AAPL down" gives NVDA, AAPL, MSFT. The server says otherwise, and
    // the server is the authority — the endpoint returns the whole list for this reason.
    mockWatchlist({ afterMove: ['MSFT', 'NVDA', 'AAPL'] })
    render(<WatchlistWidget />)
    await screen.findByTestId('watchlist-row-AAPL')

    await user.click(screen.getByRole('button', { name: 'Move AAPL down' }))

    await waitFor(() => expect(rowTickers()).toEqual(['MSFT', 'NVDA', 'AAPL']))
  })

  it('works from the keyboard', async () => {
    // The reason there is no drag-and-drop: an HTML5 drag has no keyboard equivalent, and
    // `user.click` alone would pass on a `<div onClick>` shim.
    const user = userEvent.setup()
    const seen = mockWatchlist()
    render(<WatchlistWidget />)
    await screen.findByTestId('watchlist-row-AAPL')

    screen.getByRole('button', { name: 'Move AAPL down' }).focus()
    await user.keyboard('{Enter}')

    await waitFor(() => expect(seen.count).toBe(1))
    expect(seen.body).toEqual({ position: 1 })
  })

  it('announces the result, since nothing visibly moved under the pointer', async () => {
    const user = userEvent.setup()
    mockWatchlist({ afterMove: ['NVDA', 'AAPL', 'MSFT'] })
    render(<WatchlistWidget />)
    await screen.findByTestId('watchlist-row-AAPL')

    await user.click(screen.getByRole('button', { name: 'Move AAPL down' }))

    await waitFor(() =>
      expect(screen.getByTestId('watchlist-announcement')).toHaveTextContent(
        'AAPL moved to position 2 of 3.',
      ),
    )
  })

  it('offers no move off either end', async () => {
    mockWatchlist()
    render(<WatchlistWidget />)
    await screen.findByTestId('watchlist-row-AAPL')

    expect(screen.getByRole('button', { name: 'Move AAPL up' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Move MSFT down' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Move AAPL down' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Move NVDA up' })).toBeEnabled()
  })

  it('is idempotent while a move is in flight', async () => {
    const user = userEvent.setup()
    let release
    const held = new Promise((resolve) => {
      release = resolve
    })
    const seen = mockWatchlist({
      patch: async () => {
        await held
        return HttpResponse.json(detail(['NVDA', 'AAPL', 'MSFT']))
      },
    })
    render(<WatchlistWidget />)
    await screen.findByTestId('watchlist-row-AAPL')

    const down = screen.getByRole('button', { name: 'Move AAPL down' })
    await user.click(down)

    // *Every* control is disabled while the server decides, so a second press cannot queue
    // a move against a list that is about to change underneath it. Both directions are
    // asserted: the first mutation run found that a down-only assertion left the up
    // buttons' in-flight guard entirely uncovered.
    await waitFor(() => expect(down).toBeDisabled())
    expect(screen.getByRole('button', { name: 'Move NVDA down' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Move NVDA up' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Move MSFT up' })).toBeDisabled()

    release()
    await waitFor(() => expect(rowTickers()).toEqual(['NVDA', 'AAPL', 'MSFT']))
    expect(seen.count).toBe(1)
  })

  it('reports a refused move and leaves the order alone', async () => {
    const user = userEvent.setup()
    mockWatchlist({
      patch: () =>
        errorResponse('validation_error', 'position must be within the watchlist.', {
          status: 422,
        }),
    })
    render(<WatchlistWidget />)
    await screen.findByTestId('watchlist-row-AAPL')

    await user.click(screen.getByRole('button', { name: 'Move AAPL down' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'position must be within the watchlist.',
    )
    expect(rowTickers()).toEqual(['AAPL', 'NVDA', 'MSFT'])
  })
})

describe('WatchlistWidget — the empty and failing states', () => {
  it('renders both live regions before there is anything to say', () => {
    mockWatchlist()
    render(<WatchlistWidget />)

    // ANV-29: a live region has to exist before its text arrives.
    expect(screen.getByTestId('watchlist-announcement')).toBeEmptyDOMElement()
    expect(screen.getByTestId('watchlist-error')).toBeEmptyDOMElement()
  })

  it('says when the account has no watchlists at all', async () => {
    server.use(http.get(apiUrl('/v1/watchlists'), () => pageResponse([])))
    render(<WatchlistWidget />)

    expect(await screen.findByText('You have no watchlists yet.')).toBeInTheDocument()
  })

  it('says when the watchlist is empty rather than rendering an empty list', async () => {
    server.use(
      http.get(apiUrl('/v1/watchlists'), () =>
        pageResponse([{ watchlist_id: WATCHLIST_ID, user_id: USER_ID, title: 'Empty' }]),
      ),
      http.get(apiUrl(`/v1/watchlists/${WATCHLIST_ID}`), () => HttpResponse.json(detail([]))),
    )
    render(<WatchlistWidget />)

    expect(await screen.findByText('This watchlist is empty.')).toBeInTheDocument()
    expect(screen.queryByRole('list')).not.toBeInTheDocument()
  })

  it('does not report an aborted read as a failure', async () => {
    // Neither watchlist ever answers, so the only settled promise is the abort the effect's
    // cleanup performs when `watchlistId` changes. Without the `request_cancelled` swallow
    // that rejection fills the alert slot for a widget that is still loading.
    const other = '44444444-4444-4444-8444-444444444444'
    const hang = () => new Promise(() => {})
    server.use(
      http.get(apiUrl(`/v1/watchlists/${WATCHLIST_ID}`), hang),
      http.get(apiUrl(`/v1/watchlists/${other}`), hang),
    )

    const { rerender } = render(<WatchlistWidget watchlistId={WATCHLIST_ID} />)
    rerender(<WatchlistWidget watchlistId={other} />)

    await new Promise((resolve) => setTimeout(resolve, 50))

    expect(screen.getByTestId('watchlist-error')).toBeEmptyDOMElement()
    expect(screen.getByText('Loading watchlist…')).toBeInTheDocument()
  })

  it('shows the API message when the read fails', async () => {
    server.use(
      http.get(apiUrl('/v1/watchlists'), () =>
        errorResponse('unauthorized', 'Not authenticated.', { status: 401 }),
      ),
      http.post(apiUrl('/v1/auth/refresh'), () =>
        errorResponse('invalid_token', 'Refresh token is invalid.', { status: 401 }),
      ),
    )
    render(<WatchlistWidget />)

    expect(await screen.findByRole('alert')).not.toBeEmptyDOMElement()
  })
})
