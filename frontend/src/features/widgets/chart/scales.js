/**
 * Value-to-pixel mapping, tick selection and the path string (ANV-34). Pure; no DOM.
 *
 * ## What d3 is used for here, and what it is not
 *
 * The old `LineChart.jsx` and `StockChart.jsx` both imported all of `d3` and drove the DOM
 * with it — `d3.select(ref.current).append('g').call(d3.axisLeft(y))` inside a `useEffect`.
 * That is two problems in one line. It puts d3 and React in charge of the same subtree, so
 * React has no idea the axes exist (the old `LineChart` appended a fresh pair on every data
 * change and never removed the old ones, so they stacked up); and `d3-selection` /
 * `d3-axis` do all their work through measurement and layout, which **jsdom does not have**
 * — every such test proves the DOM node exists and nothing about where it is.
 *
 * So this module takes d3 for the arithmetic only, and the SVG is ordinary JSX. What that
 * bought, package by package:
 *
 *  - **`d3-scale` — taken.** `scaleLinear` and `scaleUtc` are the value→pixel map, and
 *    `.ticks()` / `.nice()` are the one genuinely hard piece of chart maths: choosing round
 *    tick values (d3's `tickIncrement` picks from 1/2/5·10ⁿ) and, on a time axis, choosing
 *    between second, minute, hour, day and month intervals. Hand-rolling that is where a
 *    home-made chart goes wrong, and it is not 40 lines.
 *  - **`d3-array` — taken**, for `extent` in `series.js`. It arrives transitively with
 *    `d3-scale` anyway, but a module that imports it declares it: an undeclared transitive
 *    dependency breaks on somebody else's minor bump.
 *  - **`d3` (the bundle) — declined.** It is thirty packages to reach two.
 *  - **`d3-selection` / `d3-axis` — declined.** React renders the SVG; see above.
 *  - **`d3-shape` — declined.** The only generator we would use is `d3.line()`, and the
 *    line we want is `curveLinear`, which is one `Array.join`. The curves are worse than
 *    useless here: `curveMonotoneX` (what the old `StockChart` used) invents a smooth path
 *    *between* candles, i.e. it draws prices that were never printed.
 *  - **`d3-dsv` — declined.** It existed to parse a TSV off a CDN that shut down in 2019.
 *    Anvex has an API. (The old `utils.js` imported `csvParse` and never called it.)
 *  - **`d3-time-format` / `d3-format` — declined as direct dependencies.** They arrive under
 *    `d3-scale` and are reached only through `scale.tickFormat()`, which is the right entry
 *    point: it picks the granularity from the domain rather than from a hardcoded
 *    `"%Y-%m-%d"` the way the old `utils.js` did.
 *
 * ## `scaleUtc`, never `scaleTime`
 *
 * `series.js` turns the API's naive datetime into a *nominal* epoch — the digits as
 * written, rebuilt through `Date.UTC`. The axis must therefore read them back with UTC
 * getters or the labels shift by the viewer's offset. `scaleUtc` is `scaleTime` with UTC
 * intervals and a UTC tick format; the two are otherwise identical, and picking the wrong
 * one produces a chart that is correct in London and eight hours out in California.
 */

import { scaleLinear, scaleUtc } from 'd3-scale'

import { closeExtent, timeExtent } from './series'
import { tickCount } from './layout'

/** Roughly the width of a time label, used to pick a tick count. */
export const PX_PER_X_TICK = 70

/** Roughly the height a price label needs to stay legible. */
export const PX_PER_Y_TICK = 34

/**
 * Build the two scales for a series inside a laid-out box.
 *
 * Returns `null` when there is nothing to scale — no points, or a layout with no plot area
 * (`unmeasured` / `value`). A caller therefore branches once, on `null`, rather than
 * guessing whether a scale with an empty domain is safe to call.
 *
 * A degenerate domain is deliberately **not** padded. d3 maps a zero-width domain to the
 * middle of its range, so a single candle, or a series that never moved, is drawn as a flat
 * line across the centre — which is what the data says. Widening the domain to make it look
 * interesting would be inventing a range the prices never had.
 *
 * `.nice()` is applied to the price scale in `full` mode only. It rounds the domain outward
 * to the tick values, which is what makes the axis labels land on round numbers; in
 * `sparkline` mode there are no labels, so rounding outward would only flatten the line.
 *
 * @param {Array<{t: number, close: number}>} series from `toSeries`
 * @param {{mode: string, innerWidth: number, innerHeight: number}} layout from `chartLayout`
 * @returns {{x: Function, y: Function, xTicks: Date[], yTicks: number[],
 *   formatX: Function, formatY: Function}|null}
 */
export function buildScales(series, layout) {
  if (!layout || (layout.mode !== 'full' && layout.mode !== 'sparkline')) return null
  const times = timeExtent(series)
  const closes = closeExtent(series)
  if (!times || !closes) return null

  const x = scaleUtc().domain(times).range([0, layout.innerWidth])
  const y = scaleLinear().domain(closes).range([layout.innerHeight, 0])
  if (layout.mode === 'full') y.nice()

  const xCount = tickCount(layout.innerWidth, PX_PER_X_TICK)
  const yCount = tickCount(layout.innerHeight, PX_PER_Y_TICK)

  return {
    x,
    y,
    xTicks: layout.mode === 'full' ? x.ticks(xCount) : [],
    yTicks: layout.mode === 'full' ? y.ticks(yCount) : [],
    formatX: x.tickFormat(xCount),
    formatY: y.tickFormat(yCount),
  }
}

/**
 * The `d` attribute for the close-price line.
 *
 * Straight segments between candles, on purpose — see the note on `d3-shape` above. Values
 * are rounded to two decimal places: a sub-pixel coordinate is not a distinction any screen
 * can draw, and the rounding takes the path string (which ends up in the DOM, and in a test
 * assertion) from unreadable to readable.
 *
 * An empty series is an empty string rather than `'M'`, because an SVG `path` with a
 * malformed `d` is a console error in a browser and silence in jsdom.
 *
 * @param {Array<{t: number, close: number}>} series
 * @param {Function} x
 * @param {Function} y
 * @returns {string}
 */
export function linePath(series, x, y) {
  if (!Array.isArray(series) || series.length === 0) return ''
  return series
    .map((d, i) => `${i === 0 ? 'M' : 'L'}${round(x(d.t))},${round(y(d.close))}`)
    .join('')
}

const round = (n) => Math.round(n * 100) / 100

/**
 * A price as a human reads one.
 *
 * Two decimals normally, up to four when the value has them — because four is exactly what
 * the quoted-string transport exists to preserve (`app/models/stock.py`'s `NUMERIC(12, 4)`),
 * and truncating it in the one place a user can actually see the number would undo that.
 * Trailing zeros above the minimum are dropped, so `10.2` reads `10.20` and `1234.5678`
 * reads `1234.5678`.
 *
 * No `Intl.NumberFormat`: it is locale-dependent, so the same value renders differently for
 * two users and differently again under a test runner's ICU build, and its thousands
 * separators are wasted width in a 40 px box.
 *
 * @param {unknown} value
 * @param {{minDecimals?: number, maxDecimals?: number}} [options]
 * @returns {string} `'—'` for anything that is not a finite number
 */
export function formatPrice(value, { minDecimals = 2, maxDecimals = 4 } = {}) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  const fixed = value.toFixed(maxDecimals)
  if (minDecimals >= maxDecimals) return fixed
  const trimmed = fixed.replace(/0+$/, '')
  const [whole, fraction = ''] = trimmed.split('.')
  return `${whole}.${fraction.padEnd(minDecimals, '0')}`
}

/**
 * What the chart *says*, for the reader who cannot see it.
 *
 * An `<svg>` full of `<path>` elements is a picture with no text in it, so a chart needs a
 * sentence or it is simply absent from a screen reader — and the sentence has to carry the
 * numbers, not "a line chart". Neither of the original widgets had one.
 *
 * The timestamps quoted are the **raw strings the API sent**, not a re-formatted `Date`:
 * they are the exchange's wall clock and this is the one place a user reads them back, so
 * re-deriving them would be one more opportunity to attach a zone that was never there.
 *
 * Pure and exported so it can be asserted without rendering anything.
 *
 * @param {Array<{label: string, close: number}>} series
 * @param {{label?: string}} [options] `label` names the security
 * @returns {string}
 */
export function describeSeries(series, { label = 'Price' } = {}) {
  if (!Array.isArray(series) || series.length === 0) return `${label}: no price data.`

  const first = series[0]
  const last = series[series.length - 1]
  const [lo, hi] = closeExtent(series)
  const points = series.length === 1 ? '1 point' : `${series.length} points`

  return (
    `${label}: ${points} from ${first.label} to ${last.label}. ` +
    `Close ranged ${formatPrice(lo)} to ${formatPrice(hi)}, ending at ${formatPrice(last.close)}.`
  )
}
