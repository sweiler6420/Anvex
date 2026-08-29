/**
 * The dashboard widgets (ANV-34) — the public surface of `features/widgets/`.
 *
 * ANV-35 composes {@link WIDGET_PALETTE} with `features/desktop/`'s `WindowMenu` and
 * `BinPackingLayout` to build `InteractiveDesktop`; it should import from here rather than
 * from the individual modules, so the internal layout can change without a sweep through
 * its files — the same rule `features/desktop/index.js` follows.
 *
 * The feature has ANV-33's two halves. `chart/` is the pure one: functions of strings and
 * numbers, with no React, no DOM, no clock and no measurement, and it is where the two
 * conversions the API contract demands live —
 *
 *   - **prices are quoted JSON strings** and must go through `Number()` (`toSeries`), and
 *   - **`datetime` is naive on purpose** and is read as a nominal wall-clock epoch, never
 *     stamped with a zone (`parseNominalEpoch`).
 *
 * `components/` is the stateful one, and `api.js` is the feature's per-resource transport
 * module (CLAUDE.md §5). Note that `api.js` exports no function returning a raw price: the
 * conversion happens inside `fetchStockSeries`, so a future caller cannot skip it.
 */

export { default as CounterWidget } from './components/CounterWidget'
export { default as LineChart } from './components/LineChart'
export { default as StaticInfoWidget } from './components/StaticInfoWidget'
export { default as StockChartWidget } from './components/StockChartWidget'
export { default as TextInputWidget } from './components/TextInputWidget'
export { default as WatchlistWidget } from './components/WatchlistWidget'
export { default as WidgetFrame } from './components/WidgetFrame'

export { WIDGET_PALETTE } from './palette'
export { PUBLIC_WIDGET_PALETTE } from './publicPalette'

export {
  fetchStockSeries,
  fetchWatchlist,
  fetchWatchlists,
  moveWatchlistStock,
  stockDataByTickerPath,
  stockDataPath,
  watchlistPath,
  watchlistStockPath,
  WATCHLISTS_PATH,
} from './api'

export {
  closeExtent,
  parseNominalEpoch,
  timeExtent,
  toNumber,
  toSeries,
} from './chart/series'

export {
  chartLayout,
  FULL_MARGIN,
  MIN_FULL_HEIGHT,
  MIN_FULL_WIDTH,
  MIN_SPARKLINE_HEIGHT,
  MIN_SPARKLINE_WIDTH,
  SPARKLINE_MARGIN,
  tickCount,
} from './chart/layout'

export { buildScales, describeSeries, formatPrice, linePath } from './chart/scales'
