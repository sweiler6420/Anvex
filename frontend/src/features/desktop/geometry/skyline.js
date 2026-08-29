/**
 * The Skyline bin-packing heuristic, in grid cells (ANV-33).
 *
 * Ported from `AverageInvestorWeb/src/components/shared/binpacking/algorithms/skyline.js`
 * (69 lines) essentially verbatim — it was already pure, already deterministic and already
 * the correct shape. The only changes are documentation, the removal of an unused
 * `startOrderSeed` parameter that nothing ever passed, and explicit guards on the inputs
 * that used to produce `NaN` in silence.
 *
 * ## What the algorithm is
 *
 * The grid is described by one number per column: how many rows of that column are already
 * filled (`heights[col]`). That array is the "skyline". To place an item of width `w`, the
 * packer looks at every horizontal span of `w` columns, takes the *tallest* column in the
 * span (an item cannot float over a gap), and picks the span whose tallest column is
 * lowest — ties going to the leftmost span, because the scan is left to right and the
 * comparison is strict `<`. Committing raises every column in the span to `y + height`,
 * which is what makes the skyline monotonically non-decreasing and the whole thing O(n·cols·w).
 *
 * It is deterministic in input order and reads no clock, no DOM and no random source, so
 * `packSkyline(items, cols)` is a value, not an effect. That is why it is the one part of
 * the window system that can be tested for real under jsdom.
 *
 * ## What it does *not* do
 *
 * It never fails. There is no "the grid is full" answer, because there is no row ceiling —
 * the skyline simply grows upward and `requiredRows` reports how tall it got. A caller
 * that has a row budget compares against `requiredRows` itself; see
 * `computeGridSpecForWindows` in `./grid.js`, which grows the *grid* to fit rather than
 * refusing the item.
 */

/**
 * @typedef {object} SkylineItem
 * @property {string} id
 * @property {number} width  desired width in cells; floored, clamped to `1..cols`
 * @property {number} height desired height in cells; floored, clamped to `>= 1`
 */

/**
 * @typedef {object} SkylinePlacement
 * @property {string} id
 * @property {number} x
 * @property {number} y
 * @property {number} width
 * @property {number} height
 */

/**
 * Pack items into `cols` columns with the Skyline heuristic.
 *
 * @param {SkylineItem[]} items items sized in grid cells, packed in the order given
 * @param {number} cols number of columns available
 * @returns {{placed: SkylinePlacement[], requiredRows: number}} the placements and the
 *   height of the resulting skyline, i.e. the number of rows the arrangement needs
 */
export function packSkyline(items, cols) {
  if (!Array.isArray(items)) return { placed: [], requiredRows: 0 }

  // `Math.max(1, cols)` is the original's guard and it is load-bearing in a subtle way:
  // with `cols <= 0` the width clamp below yields 0, every span is empty, and every item
  // is placed at (0, 0) with width 0. That is degenerate but finite — the alternative,
  // a zero-length `heights` array, makes `spanMaxY` return 0 for every query and behaves
  // identically anyway. Kept as found; asserted in the tests rather than "fixed", because
  // a zero-column grid is a caller error and this is not the layer that reports one.
  const columnCount = Math.max(1, Math.floor(cols) || 0)
  const heights = new Array(columnCount).fill(0)
  const placed = []

  /** The tallest column under the span `[x, x + w)`; 0 for an empty span. */
  function spanMaxY(x, w) {
    let maxY = 0
    for (let i = x; i < x + w; i += 1) {
      const h = heights[i] ?? 0
      if (h > maxY) maxY = h
    }
    return maxY
  }

  for (let index = 0; index < items.length; index += 1) {
    const item = items[index]
    const width = Math.min(Math.max(1, Math.floor(item.width)), Math.floor(cols))
    const height = Math.max(1, Math.floor(item.height))

    let bestX = 0
    let bestY = Number.POSITIVE_INFINITY
    for (let x = 0; x <= Math.floor(cols) - width; x += 1) {
      const y = spanMaxY(x, width)
      // Strict `<`, so the leftmost of several equally low spans wins. Making this `<=`
      // would silently reverse the arrangement of every equal-height row.
      if (y < bestY) {
        bestY = y
        bestX = x
      }
    }

    const baseY = Number.isFinite(bestY) ? bestY : spanMaxY(bestX, width)
    for (let i = bestX; i < bestX + width; i += 1) heights[i] = baseY + height

    placed.push({ id: item.id, x: bestX, y: baseY, width, height })
  }

  const requiredRows = heights.reduce((m, v) => (v > m ? v : m), 0)
  return { placed, requiredRows }
}

/**
 * How many rows the current windows need if every one of them were shrunk to its minimum.
 *
 * This is the "can the grid get any shorter" question: packing the *minima* gives the
 * floor below which the container cannot usefully shrink, and `computeGridSpecForWindows`
 * uses it to grow the grid (and therefore to scroll) instead of shrinking a window past
 * the size its content needs.
 *
 * @param {Array<{id: string, minWidth?: number, minHeight?: number}>} windows
 * @param {number} cols
 * @returns {number} rows required by the minimum-size arrangement
 */
export function estimateRowsForMinimums(windows, cols) {
  if (!Array.isArray(windows)) return 0
  const minima = windows.map((w) => ({
    id: w.id,
    width: Math.max(1, w.minWidth || 1),
    height: Math.max(1, w.minHeight || 1),
  }))
  return packSkyline(minima, cols).requiredRows
}
