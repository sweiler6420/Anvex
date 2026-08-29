/**
 * The widgets feature's per-resource API module (ANV-34).
 *
 * CLAUDE.md §5: `lib/api` is the transport, not an endpoint catalogue — a per-resource
 * module lives in the feature that uses it and owns "the URL, the params, and the
 * projection of the response". Every call here goes out on **`authApi`**: both resources
 * are behind `CurrentUser` on the backend, and `authApi` is what attaches the bearer token
 * and performs the single-flight refresh on a 401 (ANV-24).
 *
 * There is no `try`/`catch` and no `err.response` below. A failure leaves here as an
 * `ApiError` with a `code`, because the response interceptor already normalised it, so a
 * caller writes `catch (err)` and branches on `err.code`.
 *
 * ## The one rule this module adds: **a price never leaves here as a string**
 *
 * `GET /v1/stocks/{id}/data` returns prices as quoted JSON strings (`"1234.5678"`) so the
 * fourth decimal survives the wire. That is correct on the wire and a trap in a chart,
 * because `"10.2" < "9.5"` is `true` and nothing throws. So the *projection* half of this
 * module's job is where `toSeries` runs: {@link fetchStockSeries} hands back numbers, and
 * there is deliberately **no exported function that returns the raw items**. A future
 * caller cannot forget the conversion because it is not reachable from here.
 *
 * The watchlist half is projected differently — its shapes are handed on as the API sent
 * them. Nothing on a watchlist is a `Decimal`, so there is nothing to lose, and inventing a
 * camel-cased mirror of `WatchlistDetailOut` would be a second spelling of a contract that
 * already has one.
 */

import { authApi } from '@lib/api'

import { toSeries } from './chart/series'

/** CLAUDE.md §4 — a candle series is a sub-collection of its security, not a top-level one. */
export const stockDataPath = (stockId) => `/v1/stocks/${encodeURIComponent(stockId)}/data`

/** The same series, reached by the other identifier ANV-13 established. */
export const stockDataByTickerPath = (ticker) =>
  `/v1/stocks/by-ticker/${encodeURIComponent(ticker)}/data`

export const WATCHLISTS_PATH = '/v1/watchlists'

export const watchlistPath = (watchlistId) =>
  `${WATCHLISTS_PATH}/${encodeURIComponent(watchlistId)}`

/**
 * The membership row. **Both identifiers are in the path**, because together they *are* the
 * row's identity — `WatchlistData` has no surrogate key.
 */
export const watchlistStockPath = (watchlistId, stockId) =>
  `${watchlistPath(watchlistId)}/stocks/${encodeURIComponent(stockId)}`

/**
 * One window of a security's candles, already converted to numbers.
 *
 * Either identifier will do: pass `stockId` for the UUID route or `ticker` for the
 * by-ticker route. Passing neither is a `TypeError` at the call site rather than a request
 * to `/v1/stocks/undefined/data` — the old `GET /v1/stock_data?search=` defaulted to *every*
 * stock's candles interleaved, which is not a series anybody can plot, and a missing
 * identifier should not be able to mean anything at all.
 *
 * @param {{stockId?: string, ticker?: string, start?: string, end?: string,
 *   limit?: number, offset?: number, signal?: AbortSignal}} request
 * @returns {Promise<{series: Array<object>, total: number, limit: number, offset: number,
 *   hasMore: boolean}>} `series` is `toSeries`'s output — every price a `number`, every
 *   timestamp a nominal epoch.
 */
export async function fetchStockSeries({
  stockId,
  ticker,
  start,
  end,
  limit,
  offset,
  signal,
} = {}) {
  if (!stockId && !ticker) {
    throw new TypeError('fetchStockSeries needs either a stockId or a ticker.')
  }

  const path = stockId ? stockDataPath(stockId) : stockDataByTickerPath(ticker)
  const response = await authApi.get(path, {
    params: definedOnly({ start, end, limit, offset }),
    signal,
  })
  const page = response?.data ?? {}

  return {
    series: toSeries(page.items),
    total: typeof page.total === 'number' ? page.total : 0,
    limit: typeof page.limit === 'number' ? page.limit : 0,
    offset: typeof page.offset === 'number' ? page.offset : 0,
    hasMore: page.has_more === true,
  }
}

/**
 * The caller's own watchlists, without their contents.
 *
 * `Page[WatchlistOut]`, handed on as `{items, total, ...}`. There is no id to substitute
 * and no way to ask for anybody else's: the collection *is* "mine".
 *
 * @param {{limit?: number, offset?: number, signal?: AbortSignal}} [request]
 * @returns {Promise<{items: Array<object>, total: number, hasMore: boolean}>}
 */
export async function fetchWatchlists({ limit, offset, signal } = {}) {
  const response = await authApi.get(WATCHLISTS_PATH, {
    params: definedOnly({ limit, offset }),
    signal,
  })
  const page = response?.data ?? {}
  return {
    items: Array.isArray(page.items) ? page.items : [],
    total: typeof page.total === 'number' ? page.total : 0,
    hasMore: page.has_more === true,
  }
}

/**
 * One watchlist with its entries, **already in `position` order**.
 *
 * The ordering is the relationship's, so neither this function nor its caller sorts
 * anything — and a client that re-sorted would be asserting an order the server did not.
 *
 * @param {{watchlistId: string, signal?: AbortSignal}} request
 * @returns {Promise<object>} `WatchlistDetailOut`
 */
export async function fetchWatchlist({ watchlistId, signal } = {}) {
  const response = await authApi.get(watchlistPath(watchlistId), { signal })
  return response?.data
}

/**
 * Move a stock to a position within a watchlist. **This is the frontend half of ANV-15.**
 *
 * The call names the stock and the destination, and nothing else. The old client sent
 * `(stock_id, destination_index, current_index)` in the *query string* of a `PUT` that
 * answered `201 Created` for a move that created nothing — and `current_index` is the bug:
 * it is the client's belief about where the row already is, which is stale by construction
 * and which the server cannot verify. When it was wrong the server moved a different stock
 * than the user had dropped, plausibly and silently. The new endpoint has no field for it,
 * so the mistake is unrepresentable rather than merely discouraged.
 *
 * The response is the **whole watchlist in its new order**, which is why nothing here or
 * above splices a local array: the caller renders the server's answer instead of its own
 * guess, and the two can therefore never disagree.
 *
 * `position` is validated server-side. Out of range is a 422 (`validation_error`), never a
 * silent clamp — clamping is what let the old API turn a nonsense index into a
 * plausible-looking success.
 *
 * @param {{watchlistId: string, stockId: string, position: number, signal?: AbortSignal}}
 *   request
 * @returns {Promise<object>} the reordered `WatchlistDetailOut`
 */
export async function moveWatchlistStock({ watchlistId, stockId, position, signal } = {}) {
  const response = await authApi.patch(
    watchlistStockPath(watchlistId, stockId),
    { position },
    { signal },
  )
  return response?.data
}

/**
 * Drop `undefined` keys so axios does not serialise `?limit=` for an argument nobody passed.
 *
 * `null` is kept **by this filter** — but it does not survive anyway, and the first version of
 * this comment was wrong to imply otherwise. ANV-36 checked rather than assuming:
 * `axios.getUri({url, params: {limit: undefined, offset: null, search: ''}})` is
 * `/v1/stocks?search=`, so axios drops `null` exactly as it drops `undefined`, and only the
 * empty string survives. An explicit `null` from a caller therefore cannot mean anything
 * different from omitting the key, whatever this function does with it.
 *
 * It is kept regardless, because the distinction costs nothing here and no route on this
 * client takes a nullable query parameter. **If one ever does, the serialiser is the thing to
 * fix, not this filter.**
 */
function definedOnly(params) {
  return Object.fromEntries(Object.entries(params).filter(([, v]) => v !== undefined))
}
