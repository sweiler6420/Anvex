/**
 * Reflow: what happens to an arrangement of windows when the grid changes size (ANV-33).
 *
 * Ported from `AverageInvestorWeb/src/components/shared/binpacking/WindowManager.js`.
 *
 * ## What was and was not ported, and why
 *
 * That file exported **four** reflow strategies. Exactly one of them was reachable:
 * `BinPackingLayout` called `reflowScaleByOverlap` and nothing else. `reflowPreserve` was
 * imported and never invoked; `reflowWindows` (with its `conservativeReflow`,
 * `scaleToFitIfNeeded` and `clampWindowToBounds` helpers), `reflowScaleFirst` and
 * `computeMinimumFootprint` had no reference anywhere in the repository. Together that is
 * roughly 200 lines of untested geometry with no behaviour to preserve, and one of them —
 * `reflowWindows` — reads backwards from its own name: passing `strategy: 'aggressive'`
 * *skips* the scaling pass that `'conservative'` performs.
 *
 * They are deliberately not carried over. The ticket's mandate is that drag, resize, snap,
 * collapse, fullscreen and packing behave as they do today, and code nothing calls has no
 * behaviour. Reintroducing a strategy means writing it against a case that wants it.
 *
 * ## The strategy that *is* live
 *
 * {@link reflowScaleByOverlap} shrinks before it moves. When the grid gets smaller, a
 * window that now hangs over the edge loses exactly the cells it hangs over; two windows
 * that now overlap split the overlap between them, each giving up half and neither going
 * below its own minimum. Only when nothing can shrink any further does a window move, and
 * then to the nearest free cell rather than being repacked from scratch. The point is that
 * a user's arrangement survives a resize looking like the arrangement they made.
 *
 * Pure: no React, no DOM, no clock. Input windows are never mutated — every window in the
 * result is a fresh object.
 */

import { canPlace, findNearestFreePosition, rectsOverlap } from './rects'

/** Guard on the collision-resolution loop; the original's number. */
const COLLISION_GUARD = 50

/**
 * Every window with its size fields coerced into the invariants the rest of the module
 * assumes: integers ≥ 1, a width no wider than the grid, and a position inside it.
 *
 * @param {Array<object>} windows
 * @param {number} cols
 * @param {number} rows
 * @returns {Array<object>}
 */
function normalise(windows, cols, rows) {
  return windows.map((w) => {
    const width = Math.max(1, Math.min(w.width, cols))
    const height = Math.max(1, w.height)
    return {
      ...w,
      width,
      height,
      minWidth: Math.max(1, w.minWidth || 1),
      minHeight: Math.max(1, w.minHeight || 1),
      x: Math.max(0, Math.min(w.x || 0, Math.max(0, cols - width))),
      y: Math.max(0, Math.min(w.y || 0, Math.max(0, (rows || 1) - height))),
    }
  })
}

/**
 * The smallest grid this *arrangement* can be squeezed into without moving anything.
 *
 * The insight, which is not obvious and is the reason the function exists: because windows
 * never overlap, any set of windows that share a horizontal band must be side by side, so
 * the columns that band needs is the **sum** of their minimum widths. Sweep the distinct
 * top and bottom edges as band boundaries, take the worst band, and that is `minCols`.
 * Transpose the whole argument for `minRows`.
 *
 * `computeGridSpecForWindows` uses this as a floor the container cannot argue with: it is
 * what stops a narrowing panel from crushing a row of windows below their minimum sizes,
 * choosing a scrollbar instead.
 *
 * @param {Array<object>} windows
 * @returns {{minCols: number, minRows: number}} `{0, 0}` for no windows
 */
export function computeArrangementMinimums(windows) {
  if (!Array.isArray(windows) || windows.length === 0) return { minCols: 0, minRows: 0 }

  const rects = windows.map((w) => ({
    x: Math.max(0, w.x || 0),
    y: Math.max(0, w.y || 0),
    width: Math.max(1, w.width || 1),
    height: Math.max(1, w.height || 1),
    minWidth: Math.max(1, w.minWidth || 1),
    minHeight: Math.max(1, w.minHeight || 1),
  }))

  const sweep = (points, isActive, minOf) => {
    const sorted = Array.from(points).sort((a, b) => a - b)
    let worst = 0
    for (let i = 0; i < sorted.length - 1; i += 1) {
      const lo = sorted[i]
      const hi = sorted[i + 1]
      if (hi <= lo) continue
      const total = rects
        .filter((r) => isActive(r, lo, hi))
        .reduce((sum, r) => sum + minOf(r), 0)
      if (total > worst) worst = total
    }
    return worst
  }

  const yEdges = new Set()
  const xEdges = new Set()
  for (const r of rects) {
    yEdges.add(r.y)
    yEdges.add(r.y + r.height)
    xEdges.add(r.x)
    xEdges.add(r.x + r.width)
  }

  return {
    minCols: sweep(
      yEdges,
      (r, lo, hi) => r.y < hi && r.y + r.height > lo,
      (r) => r.minWidth,
    ),
    minRows: sweep(
      xEdges,
      (r, lo, hi) => r.x < hi && r.x + r.width > lo,
      (r) => r.minHeight,
    ),
  }
}

/**
 * Fit an arrangement into a `cols` × `rows` grid, preferring to shrink over to move.
 *
 * Windows are processed top-to-bottom then left-to-right, so a window higher on the screen
 * keeps its place and the ones below it give way — which is the order a reader expects.
 * For each window in turn:
 *
 * 1. **Boundary overflow** costs exactly the cells that hang outside the grid, down to the
 *    window's minimum; if the minimum still does not fit, the window slides inward instead.
 * 2. **Collisions** with already-placed windows are resolved on the axis with the *smaller*
 *    overlap (the cheaper concession), splitting the cost between the two windows — each
 *    gives up half, and whichever of them has less slack gives up less. Note that this
 *    shrinks a window that has **already been placed**: a reflow can make a window the user
 *    never touched smaller. That is the original behaviour and it is the price of not
 *    repacking.
 * 3. **Only if neither can shrink** does the window move, to the nearest free cell.
 *
 * @param {object} input
 * @param {Array<object>} input.windows
 * @param {number} input.cols
 * @param {number} input.rows
 * @returns {{next: Array<object>, requiredRows: number}}
 */
export function reflowScaleByOverlap({ windows, cols, rows }) {
  if (!Array.isArray(windows)) return { next: [], requiredRows: rows || 0 }

  const sorted = normalise(windows, cols, rows).sort((a, b) => a.y - b.y || a.x - b.x)
  const placed = []

  for (let i = 0; i < sorted.length; i += 1) {
    const curr = { ...sorted[i] }

    // 1. Give back exactly the cells that hang over the edge, then slide inward if the
    //    minimum size still does not fit.
    const overRight = Math.max(0, curr.x + curr.width - cols)
    if (overRight > 0) curr.width = Math.max(curr.minWidth, curr.width - overRight)
    const overBottom = Math.max(0, curr.y + curr.height - rows)
    if (overBottom > 0) curr.height = Math.max(curr.minHeight, curr.height - overBottom)
    if (curr.x + curr.width > cols) curr.x = Math.max(0, cols - curr.width)
    if (curr.y + curr.height > rows) curr.y = Math.max(0, rows - curr.height)

    // 2. Resolve collisions, one at a time, re-scanning from the start after each change.
    let collided = true
    let guard = 0
    while (collided && guard < COLLISION_GUARD) {
      guard += 1
      collided = false
      for (let j = 0; j < placed.length; j += 1) {
        const other = placed[j]
        if (!rectsOverlap(curr, other)) continue
        collided = true

        const overlapX =
          Math.min(curr.x + curr.width, other.x + other.width) - Math.max(curr.x, other.x)
        const overlapY =
          Math.min(curr.y + curr.height, other.y + other.height) - Math.max(curr.y, other.y)

        if (overlapX <= overlapY) {
          const [takeCurr, takeOther] = splitCost(
            overlapX,
            curr.width - curr.minWidth,
            other.width - other.minWidth,
          )
          if (takeCurr > 0) curr.width -= takeCurr
          if (takeOther > 0) placed[j] = { ...other, width: other.width - takeOther }
        } else {
          const [takeCurr, takeOther] = splitCost(
            overlapY,
            curr.height - curr.minHeight,
            other.height - other.minHeight,
          )
          if (takeCurr > 0) curr.height -= takeCurr
          if (takeOther > 0) placed[j] = { ...other, height: other.height - takeOther }
        }

        // 3. Neither could give up enough: move rather than overlap.
        if (rectsOverlap(curr, placed[j])) {
          const spot = findNearestFreePosition(curr, { cols, rows, placed })
          curr.x = spot.x
          curr.y = spot.y
        }
        if (curr.x + curr.width > cols) curr.x = Math.max(0, cols - curr.width)
        if (curr.y + curr.height > rows) curr.y = Math.max(0, rows - curr.height)
        break
      }
    }

    // A final safety net for geometry the loop above could not settle.
    if (!canPlace(curr, { cols, rows, placed })) {
      const spot = findNearestFreePosition(curr, { cols, rows, placed })
      curr.x = spot.x
      curr.y = spot.y
      curr.width = Math.max(curr.minWidth, Math.min(curr.width, cols - curr.x))
      curr.height = Math.max(curr.minHeight, Math.min(curr.height, rows - curr.y))
    }

    placed.push(curr)
  }

  return {
    next: placed,
    requiredRows: rows || placed.reduce((m, w) => Math.max(m, w.y + w.height), 0),
  }
}

/**
 * Split `need` cells of overlap between two windows, half each, shifting the remainder onto
 * whichever of them still has slack.
 *
 * @param {number} need cells of overlap to remove
 * @param {number} currCap how much the current window can still give up
 * @param {number} otherCap how much the already-placed window can still give up
 * @returns {[number, number]} `[takeFromCurrent, takeFromOther]`
 */
function splitCost(need, currCap, otherCap) {
  const wanted = Math.ceil(need)
  let takeCurr = Math.min(Math.ceil(wanted / 2), Math.max(0, currCap))
  let takeOther = wanted - takeCurr
  if (takeOther > otherCap) {
    takeCurr = Math.min(wanted - otherCap, currCap)
    takeOther = wanted - takeCurr
  }
  return [takeCurr, takeOther]
}
