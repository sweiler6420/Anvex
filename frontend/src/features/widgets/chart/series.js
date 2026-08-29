/**
 * The port of `AverageInvestorWeb/src/components/shared/widgets/utils.js` (ANV-34).
 *
 * The old module had one job — turn a row of strings into a row of numbers and a date —
 * and it did it for a TSV of Microsoft prices fetched from `cdn.rawgit.com`, a host that
 * was shut down in 2019. The job survives; the source does not. Anvex has the same rows
 * behind `GET /v1/stocks/{id}/data`, so what is ported is `parseData`'s *coercion*, not
 * `getData`'s fetch, and `d3-dsv` / `d3-time-format` come out with the fetch.
 *
 * ## The two conversions, and why each of them is here rather than in a component
 *
 * Both are the kind of mistake that renders a **plausible chart of the wrong numbers**,
 * which is worse than an exception, so both live in a pure module with exhaustive tests
 * rather than inside a `useMemo` where nothing can reach them (CLAUDE.md §5, ANV-33: *if it
 * would still be true on paper, it does not belong in a component*).
 *
 * ### 1. Prices arrive as quoted JSON strings
 *
 * `app/schemas/stock_data.py` types a price as `Decimal`, and pydantic serialises a
 * `Decimal` as a **string** — `"1234.5678"`, not `1234.5678`. That is deliberate: a JSON
 * number is a float, and a float has already lost the fourth decimal by the time anything
 * reads it. The consequence for a chart is that **every price must go through `Number()`**,
 * and a missed conversion does not throw:
 *
 * ```js
 * "10.2" < "9.5"   // true  — lexicographic
 *  10.2  <  9.5    // false
 * ```
 *
 * A `<`-based min/max — which is what `d3.extent` is — therefore answers confidently and
 * wrongly, the y domain comes out inverted, and the chart still draws. `series.test.js`
 * pins exactly that pair.
 *
 * ### 2. `datetime` is naive on purpose, and stays that way
 *
 * The same schema's `datetime` carries no `Z` and no offset because it is the exchange's
 * local trading clock; 09:30 at the New York open is not 09:30 UTC, and stamping an offset
 * on it would move every candle. So we never append one.
 *
 * What a chart actually needs from that string is two things — a **number to order and
 * space points by**, and a **label** — and neither requires knowing the real instant. So
 * the digits are parsed here and rebuilt with `Date.UTC(...)` into what this module calls a
 * **nominal epoch**: a position on a wall-clock line, not a moment in time. Ticks are then
 * formatted with UTC getters (`scaleUtc`, never `scaleTime`, in `scales.js`), so 09:30 in
 * is "09:30" out on every machine in every timezone.
 *
 * The alternative — `new Date("2026-01-05T09:30:00")`, which every browser parses as *local*
 * time — is not so much wrong as *unstable*. It produces the same labels only because a
 * local parse and a local format cancel out; the **numbers** differ per viewer, the spacing
 * between two candles changes across a DST boundary in the viewer's zone (two candles an
 * hour apart can come out zero or two hours apart), and one `toISOString()` anywhere
 * downstream shifts the whole series by the viewer's offset. A nominal epoch has none of
 * those failure modes and costs a regex.
 *
 * The naming is the safety rail: nothing here is called `date` or `timestamp`, and `t` is
 * documented as an ordinal, so nobody hands it to something that means UTC by it.
 */

import { extent } from 'd3-array'

/**
 * A naive local datetime as `app/schemas/stock_data.py` emits it.
 *
 * Seconds and fractional seconds are optional — a `time` column rendered without `:00`
 * would still be legal ISO 8601, and accepting it costs nothing. A trailing `Z` or
 * `+01:00` deliberately does **not** match: an offset would mean the contract had changed,
 * and reading a zoned instant as a nominal one would silently shift the whole series.
 * A non-match is dropped and shows up as missing data, which is the honest failure.
 */
const NAIVE_DATETIME =
  /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?$/

/**
 * A naive datetime string as a **nominal epoch** in milliseconds, or `null`.
 *
 * The number is `Date.UTC(...)` of the digits as written. It is an ordinal on the exchange's
 * wall clock, chosen because it round-trips exactly through UTC formatting and is identical
 * on every machine — it is **not** a claim that the instant is UTC. See the module docstring.
 *
 * Out-of-range components are rejected rather than rolled over: `Date.UTC(2026, 1, 30)` is
 * happy to hand back the 2nd of March, and a candle silently moved a month is exactly the
 * class of failure this module exists to prevent. The round trip through UTC getters is
 * what catches it.
 *
 * @param {unknown} value
 * @returns {number|null}
 */
export function parseNominalEpoch(value) {
  if (typeof value !== 'string') return null
  const match = NAIVE_DATETIME.exec(value.trim())
  if (!match) return null

  const [, y, mo, d, h, mi, s = '0', frac = '0'] = match
  const year = Number(y)
  const month = Number(mo)
  const day = Number(d)
  const hour = Number(h)
  const minute = Number(mi)
  const second = Number(s)
  // `.5` and `.500` are both half a second, so the fraction is scaled by its own width.
  const ms = Math.round(Number(`0.${frac}`) * 1000)

  if (month < 1 || month > 12 || day < 1 || day > 31) return null
  if (hour > 23 || minute > 59 || second > 59) return null

  const epoch = Date.UTC(year, month - 1, day, hour, minute, second, ms)
  const back = new Date(epoch)
  if (
    back.getUTCFullYear() !== year ||
    back.getUTCMonth() !== month - 1 ||
    back.getUTCDate() !== day
  ) {
    return null
  }
  return epoch
}

/**
 * A quoted-string price (or a plain number) as a finite `Number`, or `null`.
 *
 * `Number('')` and `Number(' ')` are both `0`, and a chart plotting a silent zero for a
 * missing price is the same class of lie as a mis-ordered domain — so blank input is
 * rejected before the coercion rather than after it.
 *
 * @param {unknown} value
 * @returns {number|null}
 */
export function toNumber(value) {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  if (typeof value !== 'string') return null
  const trimmed = value.trim()
  if (trimmed === '') return null
  const parsed = Number(trimmed)
  return Number.isFinite(parsed) ? parsed : null
}

/**
 * `Page[StockDataPoint].items` as a plottable series.
 *
 * Every price goes through {@link toNumber} and every timestamp through
 * {@link parseNominalEpoch}. A point missing either — a malformed datetime, an unparseable
 * close — is **dropped**, not defaulted: a chart is a claim about values, and a fabricated
 * one is indistinguishable from a real one once it is a pixel.
 *
 * The result is sorted ascending by `t`. The API returns candles oldest-first already, so
 * this is belt and braces — but a line chart is precisely where an out-of-order point draws
 * a confident zigzag instead of failing, and the sort costs nothing.
 *
 * Unlike the original `parseData`, which assigned back onto the row it was handed, this
 * builds new objects: the caller's response body is left exactly as the network delivered
 * it, so a second reader (a table, a tooltip, a retry) cannot silently receive a
 * half-coerced copy.
 *
 * @param {Array<object>|unknown} points
 * @returns {Array<{t: number, label: string, open: number|null, high: number|null,
 *   low: number|null, close: number, volume: number|null}>} `t` is a nominal epoch (see the
 *   module docstring); `label` is the exact string the API sent.
 */
export function toSeries(points) {
  if (!Array.isArray(points)) return []

  const series = []
  for (const point of points) {
    if (!point || typeof point !== 'object') continue
    const t = parseNominalEpoch(point.datetime)
    const close = toNumber(point.close_price)
    if (t === null || close === null) continue
    series.push({
      t,
      label: point.datetime,
      open: toNumber(point.open_price),
      high: toNumber(point.high_price),
      low: toNumber(point.low_price),
      close,
      volume: toNumber(point.volume),
    })
  }

  series.sort((a, b) => a.t - b.t)
  return series
}

/**
 * The `[min, max]` of a series' close prices, or `null` for an empty series.
 *
 * `d3-array`'s `extent` compares with `<`, which is why {@link toSeries} must already have
 * converted: on strings `"10.2" < "9.5"` and the extent comes back inverted **without an
 * error**.
 *
 * @param {Array<{close: number}>} series
 * @returns {[number, number]|null}
 */
export function closeExtent(series) {
  if (!Array.isArray(series) || series.length === 0) return null
  const [lo, hi] = extent(series, (d) => d.close)
  return lo === undefined || hi === undefined ? null : [lo, hi]
}

/**
 * The span of nominal epochs a series covers, or `null` for an empty series.
 *
 * Reads the ends rather than scanning, because {@link toSeries} has already sorted.
 *
 * @param {Array<{t: number}>} series
 * @returns {[number, number]|null}
 */
export function timeExtent(series) {
  if (!Array.isArray(series) || series.length === 0) return null
  return [series[0].t, series[series.length - 1].t]
}
