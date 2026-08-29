import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import { apiUrl } from '@lib/env'
import { errorResponse, pageResponse } from '@test/msw/handlers'
import { server } from '@test/msw/server'

import {
  fetchStockSeries,
  fetchWatchlist,
  fetchWatchlists,
  moveWatchlistStock,
  stockDataByTickerPath,
  stockDataPath,
  watchlistPath,
  watchlistStockPath,
} from './api'

/**
 * ANV-34's per-resource API module, at the network boundary (CLAUDE.md §5).
 *
 * **These tests prove real behaviour.** Nothing here stubs `axios`: MSW answers the request
 * the real `authApi` sends, so the paths, the query strings, the method and the body are the
 * ones the backend would receive, and the error mapping is ANV-24's real interceptor.
 *
 * The assertion that matters most is the last one in the first block: **a price never leaves
 * this module as a string.** That is why `fetchStockSeries` exists instead of a
 * `fetchStockData` returning `Page.items` — there is no exported function that can hand a
 * caller `"10.2"`, so the conversion cannot be skipped by a future consumer.
 */

const STOCK_ID = '11111111-1111-4111-8111-111111111111'
const WATCHLIST_ID = '22222222-2222-4222-8222-222222222222'

const candle = (datetime, close) => ({
  stock_id: STOCK_ID,
  datetime,
  open_price: close,
  high_price: close,
  low_price: close,
  close_price: close,
  volume: 7,
})

describe('path builders', () => {
  it('nest a candle series under its security', () => {
    expect(stockDataPath(STOCK_ID)).toBe(`/v1/stocks/${STOCK_ID}/data`)
    expect(stockDataByTickerPath('AAPL')).toBe('/v1/stocks/by-ticker/AAPL/data')
  })

  it('put both halves of a membership row in the path', () => {
    expect(watchlistStockPath(WATCHLIST_ID, STOCK_ID)).toBe(
      `/v1/watchlists/${WATCHLIST_ID}/stocks/${STOCK_ID}`,
    )
    expect(watchlistPath(WATCHLIST_ID)).toBe(`/v1/watchlists/${WATCHLIST_ID}`)
  })

  it('escape an identifier that would otherwise change the route', () => {
    expect(stockDataByTickerPath('a/b')).toBe('/v1/stocks/by-ticker/a%2Fb/data')
    expect(watchlistPath('../users/me')).toBe('/v1/watchlists/..%2Fusers%2Fme')
  })
})

describe('fetchStockSeries', () => {
  it('returns numbers, never the quoted strings the wire carries', async () => {
    server.use(
      http.get(apiUrl(stockDataByTickerPath('AAPL')), () =>
        pageResponse([candle('2026-01-05T09:30:00', '1234.5678')], { total: 1, limit: 200 }),
      ),
    )

    const { series, total, limit, hasMore } = await fetchStockSeries({ ticker: 'AAPL' })

    expect(series).toHaveLength(1)
    expect(series[0].close).toBe(1234.5678)
    expect(typeof series[0].close).toBe('number')
    expect(total).toBe(1)
    expect(limit).toBe(200)
    expect(hasMore).toBe(false)
  })

  it('orders the series numerically, which a raw payload would not be', async () => {
    server.use(
      http.get(apiUrl(stockDataByTickerPath('AAPL')), () =>
        pageResponse([
          candle('2026-01-05T09:30:00', '9.5'),
          candle('2026-01-05T09:35:00', '10.2'),
        ]),
      ),
    )

    const { series } = await fetchStockSeries({ ticker: 'AAPL' })

    expect(Math.min(...series.map((d) => d.close))).toBe(9.5)
  })

  it('sends only the parameters it was given', async () => {
    let url = null
    server.use(
      http.get(apiUrl(stockDataPath(STOCK_ID)), ({ request }) => {
        url = new URL(request.url)
        return pageResponse([])
      }),
    )

    await fetchStockSeries({ stockId: STOCK_ID, limit: 25 })

    expect(url.searchParams.get('limit')).toBe('25')
    expect(url.searchParams.has('start')).toBe(false)
    expect(url.searchParams.has('offset')).toBe(false)
  })

  it('prefers the id route when both identifiers are supplied', async () => {
    let path = null
    server.use(
      http.get(apiUrl(stockDataPath(STOCK_ID)), ({ request }) => {
        path = new URL(request.url).pathname
        return pageResponse([])
      }),
    )

    await fetchStockSeries({ stockId: STOCK_ID, ticker: 'AAPL' })

    expect(path).toBe(`/v1/stocks/${STOCK_ID}/data`)
  })

  it('refuses to build a URL out of nothing', async () => {
    // The old `GET /v1/stock_data?search=` defaulted to every stock's candles interleaved.
    await expect(fetchStockSeries({})).rejects.toBeInstanceOf(TypeError)
  })

  it('rejects with the backend code on a failure', async () => {
    server.use(
      http.get(apiUrl(stockDataByTickerPath('NOPE')), () =>
        errorResponse('not_found', "stock 'NOPE' was not found.", {
          status: 404,
          details: { resource: 'stock', identifier: 'NOPE' },
        }),
      ),
    )

    await expect(fetchStockSeries({ ticker: 'NOPE' })).rejects.toMatchObject({
      code: 'not_found',
      status: 404,
      details: { resource: 'stock', identifier: 'NOPE' },
    })
  })

  it('survives a body that is not the page it expected', async () => {
    server.use(
      http.get(apiUrl(stockDataByTickerPath('AAPL')), () => HttpResponse.json({})),
    )

    await expect(fetchStockSeries({ ticker: 'AAPL' })).resolves.toEqual({
      series: [],
      total: 0,
      limit: 0,
      offset: 0,
      hasMore: false,
    })
  })
})

describe('fetchWatchlists', () => {
  it('unwraps the page envelope', async () => {
    server.use(
      http.get(apiUrl('/v1/watchlists'), () =>
        pageResponse([{ watchlist_id: WATCHLIST_ID, user_id: 'u', title: 'Semis' }], {
          total: 3,
          limit: 1,
        }),
      ),
    )

    const page = await fetchWatchlists({ limit: 1 })

    expect(page.items).toHaveLength(1)
    expect(page.total).toBe(3)
    expect(page.hasMore).toBe(true)
  })

  it('is an empty list rather than undefined when the body is unexpected', async () => {
    server.use(http.get(apiUrl('/v1/watchlists'), () => HttpResponse.json({})))
    await expect(fetchWatchlists()).resolves.toEqual({ items: [], total: 0, hasMore: false })
  })
})

describe('fetchWatchlist', () => {
  it('returns the detail as the API shaped it', async () => {
    const body = { watchlist_id: WATCHLIST_ID, user_id: 'u', title: 'Semis', entries: [] }
    server.use(http.get(apiUrl(watchlistPath(WATCHLIST_ID)), () => HttpResponse.json(body)))

    await expect(fetchWatchlist({ watchlistId: WATCHLIST_ID })).resolves.toEqual(body)
  })
})

describe('moveWatchlistStock — the reorder', () => {
  it('PATCHes the membership row with a destination and nothing else', async () => {
    const seen = { method: null, path: null, body: null }
    const reordered = { watchlist_id: WATCHLIST_ID, title: 'Semis', entries: [] }

    server.use(
      http.patch(apiUrl(watchlistStockPath(WATCHLIST_ID, STOCK_ID)), async ({ request }) => {
        seen.method = request.method
        seen.path = new URL(request.url).pathname
        seen.body = await request.json()
        return HttpResponse.json(reordered)
      }),
    )

    const result = await moveWatchlistStock({
      watchlistId: WATCHLIST_ID,
      stockId: STOCK_ID,
      position: 2,
    })

    expect(seen.method).toBe('PATCH')
    expect(seen.path).toBe(`/v1/watchlists/${WATCHLIST_ID}/stocks/${STOCK_ID}`)
    // No `current_index`: the server knows where the row is, and the client's belief about
    // that was the ANV-15 defect.
    expect(seen.body).toEqual({ position: 2 })
    expect(result).toEqual(reordered)
  })

  it('surfaces an out-of-range destination as a 422 rather than a clamp', async () => {
    server.use(
      http.patch(apiUrl(watchlistStockPath(WATCHLIST_ID, STOCK_ID)), () =>
        errorResponse('validation_error', 'position must be within the watchlist.', {
          status: 422,
        }),
      ),
    )

    await expect(
      moveWatchlistStock({ watchlistId: WATCHLIST_ID, stockId: STOCK_ID, position: 99 }),
    ).rejects.toMatchObject({ code: 'validation_error', status: 422 })
  })
})
