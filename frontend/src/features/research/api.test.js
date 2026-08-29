import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import { apiUrl } from '@lib/env'
import { errorResponse, pageResponse } from '@test/msw/handlers'
import { server } from '@test/msw/server'

import { fetchStocks, STOCKS_PATH } from './api'

/**
 * The research feature's per-resource API module, at the network boundary (CLAUDE.md §5).
 *
 * **Real behaviour.** Nothing here stubs `axios`: MSW answers the request the real
 * `authApi` sends, so the path, the query string and the method are the ones the backend
 * would receive, and the failure below is ANV-24's interceptor mapping a real response.
 */

const stock = (ticker, company) => ({
  stock_id: `id-${ticker}`,
  ticker_symbol: ticker,
  company,
  market: 'NASDAQ',
  isin: null,
})

describe('fetchStocks', () => {
  it('asks the securities collection and projects the Page envelope', async () => {
    server.use(
      http.get(apiUrl(STOCKS_PATH), () =>
        pageResponse([stock('AAPL', 'Apple Inc.'), stock('NVDA', 'NVIDIA Corporation')], {
          total: 9,
          limit: 2,
        }),
      ),
    )

    const page = await fetchStocks({ limit: 2 })

    expect(page.items.map((item) => item.ticker_symbol)).toEqual(['AAPL', 'NVDA'])
    expect(page.total).toBe(9)
    expect(page.limit).toBe(2)
    expect(page.offset).toBe(0)
    // Derived by the server from the window, and the reason a caller can say "showing 2 of
    // 9" without doing arithmetic it would get wrong at the last page.
    expect(page.hasMore).toBe(true)
  })

  it('sends the window it was asked for and nothing it was not', async () => {
    let seen = null
    server.use(
      http.get(apiUrl(STOCKS_PATH), ({ request }) => {
        seen = new URL(request.url).search
        return pageResponse([])
      }),
    )

    await fetchStocks({ limit: 5, offset: 10 })

    expect(seen).toBe('?limit=5&offset=10')
  })

  it('serialises no query string at all when nothing was asked for', async () => {
    // `?limit=` for an argument nobody passed is a 422 on this endpoint (`Query(ge=1)`),
    // so dropping `undefined` keys is correctness, not tidiness.
    let seen = null
    server.use(
      http.get(apiUrl(STOCKS_PATH), ({ request }) => {
        seen = new URL(request.url).search
        return pageResponse([])
      }),
    )

    await fetchStocks()

    expect(seen).toBe('')
  })

  it('rejects with an ApiError carrying the backend s code', async () => {
    server.use(
      http.get(apiUrl(STOCKS_PATH), () =>
        errorResponse('unauthorized', 'Not authenticated.', { status: 401 }),
      ),
      // The interceptor refreshes on a 401 and replays once; with no stored refresh token
      // `performRefresh` ends the session without a round trip, and *that* is the rejection
      // the caller sees. Asserting the code rather than the message is CLAUDE.md §4.
    )

    await expect(fetchStocks()).rejects.toMatchObject({ code: 'unauthorized' })
  })

  it('survives a body that is not a page, rather than crashing the caller', async () => {
    // A proxy or a misconfigured route can answer 200 with something else. `items.map` in
    // a component must not be the thing that discovers it.
    server.use(http.get(apiUrl(STOCKS_PATH), () => HttpResponse.json({})))

    await expect(fetchStocks()).resolves.toEqual({
      items: [],
      total: 0,
      limit: 0,
      offset: 0,
      hasMore: false,
    })
  })
})
