/**
 * The window operations, as pure functions over an arrangement (ANV-33).
 *
 * Collapse, grow, move, resize and close were all written **inline inside `BinPackingLayout`'s
 * JSX** in the old repo — six anonymous callbacks on the `<Window>` element, each of them
 * closing over `packed`, calling `setPacked` and calling `onWindowsChange` with a
 * differently-shaped updater. The maximise callback alone was 35 lines of collision-checked
 * geometry in the middle of a render.
 *
 * Here each of them is `(windows, id, …) -> windows`. That is what lets the interesting half
 * be tested with no DOM at all: "clicking maximise grows the window up and left before it
 * grows down and right" is a claim about {@link growToFill}, and the component test is left
 * with the much smaller claim that the button is wired to it.
 *
 * Every function returns a **new** array of new objects and mutates nothing.
 */

import { canPlace } from './rects'

/** A window's minimum width, defaulting to 1 cell. */
const minWidthOf = (w) => Math.max(1, w.minWidth || 1)
/** A window's minimum height, defaulting to 1 cell. */
const minHeightOf = (w) => Math.max(1, w.minHeight || 1)

/**
 * Replace one window in the arrangement, leaving every other object identical.
 *
 * @param {Array<object>} windows
 * @param {string} id
 * @param {(w: object) => object} update
 * @returns {Array<object>}
 */
function replace(windows, id, update) {
  return windows.map((w) => (w.id === id ? update(w) : w))
}

/**
 * Collapse a window to its minimum size, in place.
 *
 * This is what the yellow dot does. It is deliberately **not** a minimise in the desktop
 * sense: nothing is hidden, nothing is remembered, and there is no restore — the window
 * simply becomes as small as its content allows and the space it gives up stays empty until
 * something else claims it. Shrinking in place can never collide, so there is no bounds
 * check here; the top-left corner does not move.
 *
 * @param {Array<object>} windows
 * @param {string} id
 * @returns {Array<object>}
 */
export function collapseToMinimum(windows, id) {
  return replace(windows, id, (w) => ({
    ...w,
    width: minWidthOf(w),
    height: minHeightOf(w),
  }))
}

/**
 * Grow a window into all the free space around it, in a fixed order.
 *
 * What the green dot does, and the order of the four passes is the behaviour, not an
 * implementation detail:
 *
 * 1. **Up, with the bottom edge pinned.** Each row gained above is added to the height, so
 *    the window's bottom does not move as its top rises.
 * 2. **Left, with the right edge pinned.** The same trade one axis over.
 * 3. **Right**, then 4. **Down** — these two simply extend, because there is nothing left
 *    above or to the left to anchor against.
 *
 * The effect is that a window climbs toward the top-left of whatever free region contains
 * it and then fills the rest of it, which is why maximising two windows in turn tiles them
 * rather than stacking them. Every step is collision-checked against the *other* windows,
 * so growth stops at a neighbour's edge and the result never overlaps.
 *
 * ## A defect kept as found
 *
 * The `Math.max(min…)` clamp between passes 2 and 3 can *enlarge* the window past a size
 * the collision checks approved, if it arrives narrower than its own minimum. That is
 * unreachable from a normal arrangement — but `reflowScaleByOverlap` can produce such a
 * window (see its `splitCost` note), so the two defects compose. Preserved rather than
 * fixed, because fixing it changes where windows land; reported instead.
 *
 * @param {Array<object>} windows
 * @param {string} id
 * @param {{cols: number, rows: number}} grid
 * @returns {Array<object>} unchanged if `id` names no window
 */
export function growToFill(windows, id, { cols, rows }) {
  const target = windows.find((w) => w.id === id)
  if (!target) return windows

  const free = (x, y, width, height) =>
    canPlace({ x, y, width, height }, { cols, rows, placed: windows, excludeId: id })

  let { x, y, width, height } = target

  // 1. Up, keeping the bottom edge where it is.
  while (y > 0 && free(x, y - 1, width, height + 1)) {
    y -= 1
    height += 1
  }
  // 2. Left, keeping the right edge where it is.
  while (x > 0 && free(x - 1, y, width + 1, height)) {
    x -= 1
    width += 1
  }

  width = Math.max(minWidthOf(target), width)
  height = Math.max(minHeightOf(target), height)

  // 3. Right, then 4. down.
  while (x + width < cols && free(x, y, width + 1, height)) width += 1
  while (y + height < rows && free(x, y, width, height + 1)) height += 1

  return replace(windows, id, (w) => ({ ...w, x, y, width, height }))
}

/**
 * Move a window to a position that has already been decided.
 *
 * No validation: the caller is a completed drag, and the position it commits came from
 * `fitCenteredRect` or `findNearestFreePosition`, both of which have already tested it.
 *
 * @param {Array<object>} windows
 * @param {string} id
 * @param {{x: number, y: number}} position
 * @returns {Array<object>}
 */
export function moveWindow(windows, id, { x, y }) {
  return replace(windows, id, (w) => ({ ...w, x, y }))
}

/**
 * Apply a completed resize: a new size, and the position it had to move to in order to
 * take it.
 *
 * @param {Array<object>} windows
 * @param {string} id
 * @param {{x: number, y: number, width: number, height: number}} rect
 * @returns {Array<object>}
 */
export function resizeWindow(windows, id, { x, y, width, height }) {
  return replace(windows, id, (w) => ({ ...w, x, y, width, height }))
}

/**
 * Remove a window. What the red dot does.
 *
 * @param {Array<object>} windows
 * @param {string} id
 * @returns {Array<object>}
 */
export function closeWindow(windows, id) {
  return windows.filter((w) => w.id !== id)
}

/**
 * Restore a window's geometry — the second half of leaving fullscreen.
 *
 * @param {Array<object>} windows
 * @param {string} id
 * @param {{x: number, y: number, width: number, height: number}} rect
 * @returns {Array<object>}
 */
export function restoreWindow(windows, id, rect) {
  return replace(windows, id, (w) => ({ ...w, ...rect }))
}
