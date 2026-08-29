/**
 * Rectangle geometry in grid cells (ANV-33) — collision, free-space search, and the two
 * "shrink until it fits" searches the drag, resize and drop gestures are built from.
 *
 * Ported from the geometry that the old repo had written **twice**: once in
 * `binpacking/WindowManager.js` (`overlaps`, `canPlace`, `spiralSearch`) and once again
 * inside `BinPackingLayout.jsx` as `rectsOverlap`, `isCollision` and
 * `findNearestValidPosition` — the second copy differing only in that it read `cols`/`rows`
 * from component state and took an `excludeId`. Two implementations of one predicate is
 * how "the reflow says it fits and the drag says it does not" becomes possible, so they
 * are one function here, with `excludeId` optional.
 *
 * Everything in this module is a **pure function of its arguments**: no React, no DOM, no
 * clock. All coordinates are integer grid cells with the origin at the grid's top-left;
 * pixels do not appear in this file. That is what makes it testable for real under jsdom,
 * where `getBoundingClientRect()` is 0×0 and nothing can be measured.
 */

/**
 * @typedef {object} Rect
 * @property {number} x
 * @property {number} y
 * @property {number} width
 * @property {number} height
 */

/**
 * Do two rectangles share any area?
 *
 * Half-open intervals: rectangles that merely touch along an edge do **not** overlap, which
 * is what lets two windows sit flush against each other. Written as the negation of the
 * four separating-axis cases, exactly as the original was.
 *
 * @param {Rect} a
 * @param {Rect} b
 * @returns {boolean}
 */
export function rectsOverlap(a, b) {
  return !(
    a.x >= b.x + b.width ||
    a.x + a.width <= b.x ||
    a.y >= b.y + b.height ||
    a.y + a.height <= b.y
  )
}

/**
 * Is `rect` fully inside a `cols` × `rows` grid and clear of everything in `placed`?
 *
 * The single collision predicate for the whole feature. A window listed in `placed` whose
 * `id` equals `excludeId` is ignored, which is how a window being dragged or resized does
 * not collide with the position it is currently occupying.
 *
 * @param {Rect} rect
 * @param {object} bounds
 * @param {number} bounds.cols
 * @param {number} bounds.rows
 * @param {Array<Rect & {id?: string}>} [bounds.placed]
 * @param {string} [bounds.excludeId]
 * @returns {boolean}
 */
export function canPlace(rect, { cols, rows, placed = [], excludeId } = {}) {
  if (rect.x < 0 || rect.y < 0) return false
  if (rect.x + rect.width > cols) return false
  if (rect.y + rect.height > rows) return false
  for (let i = 0; i < placed.length; i += 1) {
    const other = placed[i]
    if (excludeId !== undefined && other.id === excludeId) continue
    if (rectsOverlap(rect, other)) return false
  }
  return true
}

/**
 * The nearest free position to `(x, y)` for a `width` × `height` rectangle.
 *
 * Tries the requested position first, then walks outward one square ring at a time
 * (Chebyshev radius 1, 2, 3 …) up to `max(cols, rows)`. Within a ring the scan is
 * `dx` ascending then `dy` ascending, so a tie resolves **up and to the left** — arbitrary,
 * but fixed, which is what makes a drop land in the same cell every time.
 *
 * ## The fallback, and why it is reported rather than hidden
 *
 * When no ring contains a free position the original returned the requested position
 * *clamped into the grid* — **without checking it**, so a caller that trusted the result
 * placed an overlapping window. That is a real defect and it is reachable: a grid with no
 * room left produces it every time. Behaviour is preserved here (the same coordinates come
 * back, so nothing on screen moves) but the result carries `found: false`, so a caller can
 * tell "here is a free cell" from "here is my best guess" and a test can assert the
 * no-overlap invariant against the cases where the search actually succeeded.
 *
 * @param {object} request
 * @param {number} request.x
 * @param {number} request.y
 * @param {number} request.width
 * @param {number} request.height
 * @param {object} bounds see {@link canPlace}
 * @returns {{x: number, y: number, found: boolean}}
 */
export function findNearestFreePosition({ x, y, width, height }, bounds) {
  const { cols, rows } = bounds
  if (canPlace({ x, y, width, height }, bounds)) return { x, y, found: true }

  const maxRadius = Math.max(cols, rows)
  for (let r = 1; r <= maxRadius; r += 1) {
    for (let dx = -r; dx <= r; dx += 1) {
      for (let dy = -r; dy <= r; dy += 1) {
        // Only the ring itself: an interior cell was covered by a smaller radius. This is a
        // *performance* filter, not a correctness one — deleting it changes no result, only
        // how many candidates are re-tested (confirmed by mutation). Keep it: the search is
        // O(r²) per ring already, and re-scanning the interior makes it O(r⁴) overall on a
        // grid where the fallback case walks every radius.
        if (Math.abs(dx) !== r && Math.abs(dy) !== r) continue
        const cx = x + dx
        const cy = y + dy
        if (canPlace({ x: cx, y: cy, width, height }, bounds)) {
          return { x: cx, y: cy, found: true }
        }
      }
    }
  }

  return {
    x: Math.max(0, Math.min(x, cols - width)),
    y: Math.max(0, Math.min(y, rows - height)),
    found: false,
  }
}

/**
 * The largest size at or above the minimum that fits at a **fixed** top-left corner.
 *
 * This is what a resize gesture is allowed to grow to: the corner does not move, so the
 * only freedom is how far the bottom-right can go before it hits the grid edge or another
 * window. Shrinking alternates width, height, width, height rather than exhausting one
 * axis first — dragging the handle diagonally into a neighbour should give up a little of
 * each rather than collapsing to a sliver in one dimension.
 *
 * The result can still collide, when even the minimum size does not fit; the caller
 * (`BinPackingLayout`'s resize commit) then moves the window with
 * {@link findNearestFreePosition}. `guard` bounds the loop at 2000 iterations, which is the
 * original's number and is far beyond any real grid.
 *
 * @param {object} request
 * @param {number} request.x
 * @param {number} request.y
 * @param {number} request.width  requested width in cells
 * @param {number} request.height requested height in cells
 * @param {number} request.minWidth
 * @param {number} request.minHeight
 * @param {object} bounds see {@link canPlace}
 * @returns {{width: number, height: number}}
 */
export function computeAllowedSize(
  { x, y, width, height, minWidth, minHeight },
  bounds,
) {
  const { cols, rows } = bounds
  let w = Math.max(minWidth, Math.min(width, cols - x))
  let h = Math.max(minHeight, Math.min(height, rows - y))

  let guard = 0
  let shrinkWidthNext = true
  while (!canPlace({ x, y, width: w, height: h }, bounds) && guard < 2000) {
    guard += 1
    if (shrinkWidthNext && w > minWidth) w -= 1
    else if (!shrinkWidthNext && h > minHeight) h -= 1
    else if (w > minWidth) w -= 1
    else if (h > minHeight) h -= 1
    else break
    shrinkWidthNext = !shrinkWidthNext
  }

  return { width: w, height: h }
}

/**
 * The largest rectangle **centred on** a point that fits, or `null` if none does.
 *
 * The ghost that follows the cursor during a window drag and during a drag from the menu.
 * Centring is the whole point — the window under the cursor should stay under the cursor —
 * so this cannot reuse {@link computeAllowedSize}, whose corner is pinned: every time the
 * size changes here the top-left moves by half of it, and the fit has to be re-tested.
 *
 * The order of concessions is the original's and it is deliberate: shrink on the axis that
 * is out of bounds first (the cursor is near an edge), then alternate axes for a collision
 * (the cursor is next to another window), and only when nothing can shrink further, give up
 * on centring and look for the nearest free position instead. `null` means "there is
 * nowhere for this to go", and the callers render no ghost and refuse the drop rather than
 * dropping the window somewhere the user did not point at.
 *
 * @param {object} request
 * @param {number} request.centerX fractional cell coordinate of the cursor
 * @param {number} request.centerY fractional cell coordinate of the cursor
 * @param {number} request.width
 * @param {number} request.height
 * @param {number} request.minWidth
 * @param {number} request.minHeight
 * @param {object} bounds see {@link canPlace}
 * @returns {(Rect | null)}
 */
export function fitCenteredRect(
  { centerX, centerY, width, height, minWidth, minHeight },
  bounds,
) {
  const { cols, rows } = bounds

  // A non-finite centre means "we do not know where the pointer is", and the honest answer
  // to that is the same as "there is nowhere for this to go". Without the guard the
  // arithmetic below propagates `NaN` silently: every bounds comparison against `NaN` is
  // false, so an out-of-grid rectangle reads as in-bounds and a `NaN` position is committed
  // to a window that then cannot be found again.
  if (!Number.isFinite(centerX) || !Number.isFinite(centerY)) return null

  let w = Math.max(minWidth, Math.min(width, cols))
  let h = Math.max(minHeight, Math.min(height, rows))

  let guard = 0
  let preferWidthNext = true
  while (guard < 2000) {
    guard += 1
    const x = Math.round(centerX - w / 2)
    const y = Math.round(centerY - h / 2)
    const outLeft = x < 0
    const outRight = x + w > cols
    const outTop = y < 0
    const outBottom = y + h > rows
    const inBounds = !outLeft && !outRight && !outTop && !outBottom
    const collided = inBounds && !canPlace({ x, y, width: w, height: h }, bounds)

    if (inBounds && !collided) return { x, y, width: w, height: h }

    if ((outLeft || outRight) && w > minWidth) {
      w -= 1
      continue
    }
    if ((outTop || outBottom) && h > minHeight) {
      h -= 1
      continue
    }
    if (collided) {
      if (preferWidthNext && w > minWidth) {
        w -= 1
        preferWidthNext = false
        continue
      }
      if (!preferWidthNext && h > minHeight) {
        h -= 1
        preferWidthNext = true
        continue
      }
      if (w > minWidth) {
        w -= 1
        continue
      }
      if (h > minHeight) {
        h -= 1
        continue
      }
    }

    // Nothing left to shrink. Give up on keeping the rectangle centred and take the
    // nearest free cell instead — but only if the search actually found one.
    const spot = findNearestFreePosition(
      { x: Math.round(centerX - w / 2), y: Math.round(centerY - h / 2), width: w, height: h },
      bounds,
    )
    if (spot.found) {
      return {
        x: Math.max(0, Math.min(spot.x, cols - w)),
        y: Math.max(0, Math.min(spot.y, rows - h)),
        width: w,
        height: h,
      }
    }
    return null
  }

  /* c8 ignore next -- the loop above always returns; `guard` is a belt-and-braces bound. */
  return null
}
