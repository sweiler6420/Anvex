/**
 * How much chart fits in a box (ANV-34). Pure integer arithmetic; no React, no DOM.
 *
 * ANV-33 fixed the constraint this module exists for: **a widget must survive being 2×2
 * cells**, which at the default `cellSize` is a 40×40 px content box. A chart drawn at
 * 40×40 with a 48 px left margin has a *negative* plot area, and the ported `StockChart`
 * would have produced exactly that — it hardcoded `width = 640`, `height = 320` and a
 * four-sided margin, so at any size the user actually chose it drew a 640×320 SVG and let
 * the window's `overflow: auto` scroll it.
 *
 * The answer is not a minimum size (a window manager whose windows have a minimum the user
 * cannot reach is a broken window manager) but a **mode**: the same data, drawn with
 * whatever the box can carry.
 *
 *  - `unmeasured` — nothing has reported a size yet. **Under jsdom this is every chart**,
 *    because there is no layout and `useContainerSize` reports 0×0. Rendering nothing is
 *    the truthful answer, not a broken one (ANV-33).
 *  - `value` — too small for a line to say anything. Show the latest price as text.
 *  - `sparkline` — room for the line but not for axes. Drop the axes, keep the shape.
 *  - `full` — axes, ticks and labels.
 *
 * The thresholds are here, as named constants, rather than as `width > 220` inside a
 * component, because they are the whole of the decision and a test that has to render a
 * component to reach them is a test of React.
 */

/**
 * Margins for `full` mode. `left` carries a price label ("1234.56") and `bottom` carries a
 * time label, so neither is symmetric with its opposite.
 */
export const FULL_MARGIN = Object.freeze({ top: 8, right: 10, bottom: 18, left: 42 })

/** `sparkline` mode keeps a hair of padding so the stroke is not clipped by the viewBox. */
export const SPARKLINE_MARGIN = Object.freeze({ top: 2, right: 2, bottom: 2, left: 2 })

/** Zero margins, for the modes that plot nothing. */
const NO_MARGIN = Object.freeze({ top: 0, right: 0, bottom: 0, left: 0 })

/**
 * Below this, `full` mode's plot area is narrower than the axis labels beside it.
 * `FULL_MARGIN.left + FULL_MARGIN.right` is 52, so 220 leaves 168 px of line.
 */
export const MIN_FULL_WIDTH = 220

/** Below this, `full` mode's tick labels collide with each other vertically. */
export const MIN_FULL_HEIGHT = 120

/** Below this a line is fewer pixels than a series has points, and says nothing. */
export const MIN_SPARKLINE_WIDTH = 64

/** Below this a line has no vertical room to differ from a straight one. */
export const MIN_SPARKLINE_HEIGHT = 24

/** @typedef {'unmeasured'|'value'|'sparkline'|'full'} ChartMode */

const unmeasured = () => ({
  mode: 'unmeasured',
  width: 0,
  height: 0,
  margin: NO_MARGIN,
  innerWidth: 0,
  innerHeight: 0,
})

/**
 * Decide what to draw in a box of this size.
 *
 * A non-finite or non-positive dimension is `unmeasured` rather than an error: 0×0 is what
 * a `ResizeObserver` reports for a hidden element and what jsdom reports for everything, so
 * it is an ordinary state and not a defect.
 *
 * `innerWidth` / `innerHeight` are floored at 0. They cannot go negative for any size that
 * reaches `full` or `sparkline` — the thresholds are larger than the margins they pair
 * with — but flooring them means a future threshold edit cannot produce a negative
 * `width` attribute on an SVG, which browsers treat as an error and jsdom does not.
 *
 * @param {{width: number, height: number}} size the content box, in pixels
 * @returns {{mode: ChartMode, width: number, height: number,
 *   margin: {top: number, right: number, bottom: number, left: number},
 *   innerWidth: number, innerHeight: number}}
 */
export function chartLayout({ width, height } = {}) {
  if (!Number.isFinite(width) || !Number.isFinite(height)) return unmeasured()
  if (width <= 0 || height <= 0) return unmeasured()

  if (width >= MIN_FULL_WIDTH && height >= MIN_FULL_HEIGHT) {
    return withMargin('full', width, height, FULL_MARGIN)
  }
  if (width >= MIN_SPARKLINE_WIDTH && height >= MIN_SPARKLINE_HEIGHT) {
    return withMargin('sparkline', width, height, SPARKLINE_MARGIN)
  }
  return { ...unmeasured(), mode: 'value', width, height }
}

function withMargin(mode, width, height, margin) {
  return {
    mode,
    width,
    height,
    margin,
    innerWidth: Math.max(0, width - margin.left - margin.right),
    innerHeight: Math.max(0, height - margin.top - margin.bottom),
  }
}

/**
 * How many ticks an axis of this length should ask for.
 *
 * d3 treats a tick count as a *suggestion* and returns a round number of round values, so
 * this only has to be in the right neighbourhood. The floor of 2 is what stops a narrow
 * `full` chart asking for one tick and getting an axis with a single unlabelled end.
 *
 * @param {number} lengthPx the axis's length in pixels
 * @param {number} pxPerTick roughly how much room one label needs
 * @returns {number} at least 2
 */
export function tickCount(lengthPx, pxPerTick) {
  if (!Number.isFinite(lengthPx) || !Number.isFinite(pxPerTick) || pxPerTick <= 0) return 2
  return Math.max(2, Math.floor(lengthPx / pxPerTick))
}
