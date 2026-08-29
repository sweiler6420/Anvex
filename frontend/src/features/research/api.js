/**
 * The research feature's per-resource API module (ANV-36).
 *
 * CLAUDE.md §5: `lib/api` is the transport, not an endpoint catalogue. `features/widgets/
 * api.js` already owns a security's *candles* (`/v1/stocks/{id}/data`) and the watchlists;
 * this owns the securities **reference list** (`GET /v1/stocks`), which has exactly one
 * consumer — the research page's picker. It goes out on `authApi`: every route under
 * `/v1/stocks` takes `CurrentUser`, reference data or not.
 *
 * ## There is no conversion to own here, and that is worth stating
 *
 * `fetchStockSeries` exists in the shape it does because a price is a quoted JSON string
 * and `"10.2" < "9.5"`. `StockOut` is `{stock_id, ticker_symbol, company, market, isin}` —
 * five strings and a nullable one, with no number and no timestamp in it — so there is
 * nothing whose wire type differs from its usable type, and inventing a camel-cased mirror
 * would be a second spelling of a contract that already has one (ANV-34's rule for the
 * watchlist half). The rows are handed on exactly as the API sent them.
 *
 * What *is* projected is the envelope: `Page[T]`'s `has_more` becomes `hasMore`, and each
 * field is defaulted rather than trusted, so a caller can render `items.map` without
 * checking whether the body arrived at all.
 */

import { authApi } from '@lib/api'

/** CLAUDE.md §4 — `/v1/<plural-resource>`. */
export const STOCKS_PATH = '/v1/stocks'

/**
 * One window of the securities reference table, ordered by ticker.
 *
 * **The endpoint's `search` parameter is deliberately not wrapped.** `GET /v1/stocks`
 * accepts a case-insensitive substring match against the ticker or the company name, and
 * nothing in this ticket has a control that would produce one — so wrapping it would be a
 * parameter no caller passes, which ANV-33's port rule calls code with no behaviour. It is
 * one argument away the day a search box lands; `total` is surfaced meanwhile so the page
 * can say how much of the table it is showing rather than implying it is all of it.
 *
 * @param {{limit?: number, offset?: number, signal?: AbortSignal}} [request]
 * @returns {Promise<{items: Array<object>, total: number, limit: number, offset: number,
 *   hasMore: boolean}>} `items` are `StockOut` rows, untouched.
 */
export async function fetchStocks({ limit, offset, signal } = {}) {
  const response = await authApi.get(STOCKS_PATH, {
    params: definedOnly({ limit, offset }),
    signal,
  })
  const page = response?.data ?? {}

  return {
    items: Array.isArray(page.items) ? page.items : [],
    total: typeof page.total === 'number' ? page.total : 0,
    limit: typeof page.limit === 'number' ? page.limit : 0,
    offset: typeof page.offset === 'number' ? page.offset : 0,
    hasMore: page.has_more === true,
  }
}

/**
 * Drop `undefined` keys, so axios does not serialise `?limit=` for an argument nobody
 * passed — which on this endpoint is a 422, since `limit` carries `Query(ge=1)`.
 *
 * **This is an EQUIVALENT MUTANT and it is recorded rather than deleted (ANV-33's rule).**
 * Removing it leaves the whole suite green, because axios's own parameter serialiser
 * already skips a value that is `undefined` — verified, not assumed:
 * `axios.getUri({url, params: {limit: undefined, offset: null, search: ''}})` is
 * `/v1/stocks?search=`. What it buys is that the request this module builds does not depend
 * on a default in a library we do not own, and it is one line.
 *
 * **It also corrects a claim `features/widgets/api.js` makes about the same helper.** That
 * module's copy says `null` is kept deliberately, "because an explicit `null` from a caller
 * is a statement and an absent key is not". axios drops `null` as well (same call above), so
 * the distinction does not survive the transport and no caller can rely on it. Nothing
 * depends on it today — no route here takes a nullable query parameter — but the comment
 * there is wrong and should not be copied a third time.
 */
function definedOnly(params) {
  return Object.fromEntries(Object.entries(params).filter(([, v]) => v !== undefined))
}
