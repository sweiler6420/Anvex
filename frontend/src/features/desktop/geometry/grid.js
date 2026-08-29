/**
 * The grid: how many cells fit in a measured container, where those cells sit inside it,
 * and the two conversions between pixels and cells (ANV-33).
 *
 * Ported from `AverageInvestorWeb/src/components/shared/binpacking/GridManager.js` plus the
 * ~20 lines that were written **inline inside a `useEffect`** in `BinPackingLayout.jsx`.
 * Pulling that body out into {@link computeGridSpecForWindows} is the single most valuable
 * extraction in this ticket: it is the rule that decides whether the desktop scrolls or
 * centres, it has four interacting inputs, and as an effect body it could not be called at
 * all without a browser that lays out.
 *
 * Everything here is pure. `pixelToCell` takes the container's measurements as arguments
 * rather than reading them, so the one call to `getBoundingClientRect()` in the feature
 * lives in the component and this file stays testable with no DOM.
 *
 * **Not ported:** `computeGridSpecWithMinimumFootprint`. It was imported by
 * `BinPackingLayout` and never called, and its body could not have worked if it had been —
 * `const cols = fitsW ? requiredCols : requiredCols` is the same value in both branches, as
 * is the `rows` line below it, so its entire "does the footprint fit" computation fed two
 * ternaries that discard it. See the ANV-33 report.
 */

import { estimateRowsForMinimums } from './skyline'
import { computeArrangementMinimums } from './reflow'

/**
 * @typedef {object} GridSpec
 * @property {number} cols
 * @property {number} rows
 * @property {number} innerW  the grid's width in pixels (`cols * cellSize`)
 * @property {number} innerH  the grid's height in pixels
 * @property {number} offsetLeft  where the grid starts inside the container, in pixels
 * @property {number} offsetTop
 * @property {boolean} overflowX  the grid is wider than the container
 * @property {boolean} overflowY  the grid is taller than the container
 */

/** A grid spec that means "nothing has been measured yet". */
export const EMPTY_GRID_SPEC = Object.freeze({
  cols: 0,
  rows: 0,
  innerW: 0,
  innerH: 0,
  offsetLeft: 0,
  offsetTop: 0,
  overflowX: false,
  overflowY: false,
})

/**
 * The largest whole number of cells that fits, centred in the leftover space.
 *
 * `Math.floor` rather than `Math.round`, so the grid never claims a partial cell it cannot
 * draw; the remainder becomes the centring offset. `minCols`/`minRows` are floors, not
 * caps — a container too small for them produces a grid larger than itself, which is what
 * `overflowX`/`overflowY` in {@link computeGridSpecForWindows} then turns into scrollbars.
 *
 * @param {number} containerW
 * @param {number} containerH
 * @param {number} cellSize pixels per cell
 * @param {number} minCols
 * @param {number} minRows
 * @returns {{cols: number, rows: number, innerW: number, innerH: number, offsetLeft: number, offsetTop: number}}
 */
export function computeGridSpec(containerW, containerH, cellSize, minCols, minRows) {
  const cols = Math.max(minCols, Math.floor(containerW / cellSize))
  const rows = Math.max(minRows, Math.floor(containerH / cellSize))
  const innerW = cols * cellSize
  const innerH = rows * cellSize
  const offsetLeft = Math.max(0, Math.floor((containerW - innerW) / 2))
  const offsetTop = Math.max(0, Math.floor((containerH - innerH) / 2))
  return { cols, rows, innerW, innerH, offsetLeft, offsetTop }
}

/**
 * The grid the current windows actually need, given the container we were handed.
 *
 * Four rules, in this order, and each of them is why the previous one is not enough:
 *
 * 1. **What fits.** {@link computeGridSpec} on the measured container. On its own this
 *    shrinks the grid under the windows as soon as the container narrows.
 * 2. **What the arrangement needs.** `computeArrangementMinimums` reads the windows' *own
 *    layout* and asks, for every horizontal band, how wide the windows sitting side by side
 *    in it must be at their minimum sizes. That is a floor on `cols` no container can talk
 *    the grid out of, and it is what stops a narrowing container from squeezing a row of
 *    windows below the size their content needs.
 * 3. **What the minima need vertically.** The Skyline packer run over the minimum sizes
 *    (`estimateRowsForMinimums`) gives the shortest arrangement that could exist at this
 *    column count; `rows` is at least that.
 * 4. **Centre, or scroll.** A grid that fits is centred in the leftover pixels. A grid that
 *    does not fit is pinned to the top-left and the axis is flagged, because centring
 *    something larger than its container puts the beginning of it off-screen where nothing
 *    can scroll back to it.
 *
 * The consequence, stated rather than discovered: **the grid grows rather than the windows
 * shrinking.** Below a certain container size the desktop scrolls. That is the original's
 * behaviour and the reason the component offers `allowOverflowScroll`.
 *
 * @param {object} input
 * @param {number} input.containerWidth  measured, in pixels
 * @param {number} input.containerHeight
 * @param {number} input.cellSize
 * @param {number} input.minCols
 * @param {number} input.minRows
 * @param {Array<object>} input.windows
 * @returns {GridSpec}
 */
export function computeGridSpecForWindows({
  containerWidth,
  containerHeight,
  cellSize,
  minCols,
  minRows,
  windows = [],
}) {
  if (!(containerWidth > 0) || !(containerHeight > 0) || !(cellSize > 0)) {
    return EMPTY_GRID_SPEC
  }

  const base = computeGridSpec(containerWidth, containerHeight, cellSize, minCols, minRows)
  const arrangement = computeArrangementMinimums(windows)

  const cols = Math.max(base.cols, arrangement.minCols)
  const rows = Math.max(
    base.rows,
    Math.max(estimateRowsForMinimums(windows, cols), arrangement.minRows),
  )

  const innerW = cols * cellSize
  const innerH = rows * cellSize
  const overflowX = innerW > containerWidth
  const overflowY = innerH > containerHeight

  return {
    cols,
    rows,
    innerW,
    innerH,
    offsetLeft: overflowX ? 0 : Math.max(0, Math.floor((containerWidth - innerW) / 2)),
    offsetTop: overflowY ? 0 : Math.max(0, Math.floor((containerHeight - innerH) / 2)),
    overflowX,
    overflowY,
  }
}

/** Is this spec measured enough to render and pack against? */
export function isGridReady(gridSpec, cellSize) {
  return gridSpec.cols > 0 && gridSpec.rows > 0 && cellSize > 0
}

/**
 * Cells → pixels, relative to the container's padding box.
 *
 * @param {{x: number, y: number, width: number, height: number}} rect in cells
 * @param {GridSpec} gridSpec
 * @param {number} cellSize
 * @returns {{left: number, top: number, width: number, height: number}}
 */
export function cellsToPixels({ x, y, width, height }, gridSpec, cellSize) {
  return {
    left: gridSpec.offsetLeft + x * cellSize,
    top: gridSpec.offsetTop + y * cellSize,
    width: width * cellSize,
    height: height * cellSize,
  }
}

/**
 * Pixels → cells: where in the grid is the pointer?
 *
 * Takes the container's rect and scroll offsets as **arguments**, so the one
 * `getBoundingClientRect()` call in the feature stays in the component and this stays a
 * function of numbers. The result is deliberately **fractional** — the caller centres a
 * rectangle on it and rounds once, at the end; rounding here would quantise the cursor to a
 * cell first and make a window jump a whole cell as the pointer crosses a boundary.
 *
 * @param {object} input
 * @param {number} input.clientX viewport coordinates, as an event reports them
 * @param {number} input.clientY
 * @param {{left: number, top: number}} input.containerRect the container's bounding rect
 * @param {number} [input.scrollLeft]
 * @param {number} [input.scrollTop]
 * @param {GridSpec} input.gridSpec
 * @param {number} input.cellSize
 * @returns {{cellX: number, cellY: number}} fractional cell coordinates
 */
export function pixelToCell({
  clientX,
  clientY,
  containerRect,
  scrollLeft = 0,
  scrollTop = 0,
  gridSpec,
  cellSize,
}) {
  const withinContainerX = clientX - containerRect.left + scrollLeft
  const withinContainerY = clientY - containerRect.top + scrollTop
  return {
    cellX: (withinContainerX - gridSpec.offsetLeft) / cellSize,
    cellY: (withinContainerY - gridSpec.offsetTop) / cellSize,
  }
}
