import { useId, useMemo } from 'react'

import { chartLayout } from '../chart/layout'
import { buildScales, describeSeries, formatPrice, linePath } from '../chart/scales'

/**
 * A close-price line chart (ANV-34). The port of
 * `AverageInvestorWeb/src/components/shared/widgets/LineChart.jsx` (74 lines) and the
 * drawing half of `StockChart.jsx` (76 lines), which were two attempts at the same thing.
 *
 * **This component measures nothing and fetches nothing.** `width` and `height` arrive as
 * pixel numbers and `series` arrives already converted — the same discipline `DesktopWindow`
 * follows for the same reason (ANV-33): there is then no layout for jsdom to be missing, so
 * everything below is testable, and the two things that are genuinely environmental
 * (measurement, network) live in `StockChartWidget` where they can be swapped at a named
 * seam.
 *
 * ## React owns the SVG; d3 owns only the arithmetic
 *
 * Both originals did `d3.select(svgRef.current).append(…)` inside a `useEffect`. That gives
 * two libraries write access to one subtree, and it showed: the old `LineChart` appended a
 * fresh pair of axis `<g>`s on every `data` change and removed nothing, so the axes
 * accumulated. Here the scales are computed and the elements are JSX, so React reconciles
 * them like anything else and there is no imperative DOM code to leak.
 *
 * ## Four sizes, not one
 *
 * ANV-33 requires a widget to survive a 2×2 window — 40×40 px. Both originals hardcoded
 * their canvas (`400×100`, `640×320`) and let the window's `overflow: auto` scroll it, which
 * at 40×40 shows the top-left corner of an axis. `chartLayout` picks a mode instead: axes,
 * bare sparkline, a single number, or nothing at all when nothing has been measured yet.
 * Under jsdom that last mode is the only one a real measurement ever produces.
 *
 * ## Bugs in the original that are not reproduced
 *
 *  - `LineChart`'s y scale was `domain([0, h]).range([h, 0])` — a domain of **pixel heights**
 *    rather than prices — and its line generator was `.y(yScale)`, which handed the whole row
 *    object to the scale. Every y coordinate was `NaN`, so the path never drew.
 *  - `LineChart` imported `React` as a *named* export of `react`, which is `undefined`.
 *  - `StockChart` imported `getData` from the bare specifier `'utils'`, which resolves to a
 *    package, not to the sibling file; the module could not have loaded.
 */

/** cyan-500 — legible on both the light and the dark panel, so it is not theme-dependent. */
const LINE_COLOR = '#06b6d4'

const TICK_FONT_SIZE = 10

/**
 * @param {object} props
 * @param {Array<object>} props.series output of `toSeries` — numbers, not quoted strings
 * @param {number} props.width content-box width in pixels
 * @param {number} props.height content-box height in pixels
 * @param {string} [props.label] names the security, for the text alternative
 * @param {string} [props.emptyMessage] shown when the box is measured but the series is empty
 */
export default function LineChart({
  series = [],
  width = 0,
  height = 0,
  label = 'Price',
  emptyMessage = 'No price data for this range.',
}) {
  const titleId = useId()

  const layout = useMemo(() => chartLayout({ width, height }), [width, height])
  const scales = useMemo(() => buildScales(series, layout), [series, layout])
  const description = useMemo(() => describeSeries(series, { label }), [series, label])

  if (layout.mode === 'unmeasured') {
    // Not an error state: a `ResizeObserver` reports 0×0 for a hidden element, and under
    // jsdom it reports 0×0 for everything. Rendering nothing is the truth (ANV-33).
    return <div data-testid="line-chart" data-mode="unmeasured" className="h-full w-full" />
  }

  if (series.length === 0) {
    return (
      <div
        data-testid="line-chart"
        data-mode="empty"
        className="flex h-full w-full min-w-0 items-center justify-center text-center text-neutral-500 dark:text-neutral-400"
      >
        {emptyMessage}
      </div>
    )
  }

  if (layout.mode === 'value' || !scales) {
    // Too small for a line to mean anything. The latest close is the one number worth the
    // pixels, and the full description is still there for a screen reader.
    return (
      <div
        data-testid="line-chart"
        data-mode="value"
        className="flex h-full w-full min-w-0 items-center justify-center"
      >
        <span className="sr-only">{description}</span>
        <span aria-hidden="true" className="truncate font-demi tabular-nums">
          {formatPrice(series[series.length - 1].close)}
        </span>
      </div>
    )
  }

  const { margin, innerWidth, innerHeight } = layout
  const { x, y, xTicks, yTicks, formatX, formatY } = scales
  const path = linePath(series, x, y)

  return (
    <svg
      data-testid="line-chart"
      data-mode={layout.mode}
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      // Deliberately redundant with the `<title>` below, and **an equivalent mutant**:
      // deleting this attribute leaves the suite green, because the accessible-name
      // algorithm falls back to an SVG's `<title>` child and that is what the tests
      // actually pin. It stays because the fallback is the weaker of the two in practice —
      // some assistive tech treats an inline SVG's `<title>` as a tooltip — and because a
      // future `<title>` that stops being the first child would silently lose the name
      // without it. Recorded here rather than deleted (ANV-33's rule for a survivor).
      aria-labelledby={titleId}
      className="text-neutral-500 dark:text-neutral-400"
    >
      {/* The accessible name. An SVG of paths is otherwise an empty element to a reader. */}
      <title id={titleId}>{description}</title>

      <g transform={`translate(${margin.left},${margin.top})`}>
        {yTicks.map((tick) => (
          <g key={tick} transform={`translate(0,${round(y(tick))})`}>
            <line
              x1={0}
              x2={innerWidth}
              stroke="currentColor"
              strokeOpacity={0.25}
              shapeRendering="crispEdges"
            />
            <text
              x={-6}
              dy="0.32em"
              textAnchor="end"
              fill="currentColor"
              fontSize={TICK_FONT_SIZE}
              data-testid="y-tick"
            >
              {formatY(tick)}
            </text>
          </g>
        ))}

        {xTicks.map((tick, index) => (
          <text
            key={tick.valueOf()}
            x={round(x(tick))}
            y={innerHeight + TICK_FONT_SIZE + 4}
            // The end labels are anchored inward so a chart at its minimum width does not
            // paint half a timestamp outside its own viewBox.
            textAnchor={anchorFor(index, xTicks.length)}
            fill="currentColor"
            fontSize={TICK_FONT_SIZE}
            data-testid="x-tick"
          >
            {formatX(tick)}
          </text>
        ))}

        <path
          d={path}
          fill="none"
          stroke={LINE_COLOR}
          strokeWidth={1.5}
          strokeLinejoin="round"
          strokeLinecap="round"
          data-testid="price-line"
        />
      </g>
    </svg>
  )
}

const round = (n) => Math.round(n * 100) / 100

const anchorFor = (index, total) => {
  if (index === 0) return 'start'
  if (index === total - 1) return 'end'
  return 'middle'
}
