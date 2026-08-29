import { describe, expect, it } from 'vitest'

import { seededRandom } from '@test/seededRandom'

import { canPlace, rectsOverlap } from './rects'
import { computeArrangementMinimums, reflowScaleByOverlap } from './reflow'

/**
 * ANV-33 — reflow.
 *
 * **These tests prove real behaviour.** Reflow is a function from an arrangement and a grid
 * size to an arrangement; there is nothing to measure and nothing to render.
 *
 * Two of them pin *defects* rather than intentions. They are marked, and they are here so
 * that a future fix shows up as a failing assertion with an explanation attached rather than
 * as a silent change in where windows land.
 */

const win = (id, x, y, width, height, minWidth = 1, minHeight = 1) => ({
  id,
  x,
  y,
  width,
  height,
  minWidth,
  minHeight,
})

const pairs = (list) => list.flatMap((a, i) => list.slice(i + 1).map((b) => [a, b]))
const byId = (list) => Object.fromEntries(list.map((w) => [w.id, w]))

describe('computeArrangementMinimums', () => {
  it('is zero for an empty arrangement, and for a non-array', () => {
    expect(computeArrangementMinimums([])).toEqual({ minCols: 0, minRows: 0 })
    expect(computeArrangementMinimums(null)).toEqual({ minCols: 0, minRows: 0 })
  })

  it('is a single window s own minimum', () => {
    expect(computeArrangementMinimums([win('a', 0, 0, 8, 6, 3, 2)])).toEqual({
      minCols: 3,
      minRows: 2,
    })
  })

  it('sums the minimum widths of windows that share a horizontal band', () => {
    // Side by side: they must fit next to each other, so the grid needs 3 + 4 columns.
    const windows = [win('a', 0, 0, 5, 4, 3, 2), win('b', 5, 0, 5, 4, 4, 2)]

    expect(computeArrangementMinimums(windows)).toMatchObject({ minCols: 7 })
  })

  it('takes the maximum, not the sum, for windows that are stacked', () => {
    // One above the other: no band holds both, so the widest minimum decides.
    const windows = [win('a', 0, 0, 5, 4, 3, 2), win('b', 0, 4, 5, 4, 4, 2)]

    expect(computeArrangementMinimums(windows)).toMatchObject({ minCols: 4, minRows: 4 })
  })

  it('finds the worst band, not the first', () => {
    // Row 0-3 holds one window; row 4-7 holds three. The answer is the second row's.
    const windows = [
      win('wide', 0, 0, 12, 4, 5, 1),
      win('a', 0, 4, 4, 4, 4, 1),
      win('b', 4, 4, 4, 4, 4, 1),
      win('c', 8, 4, 4, 4, 4, 1),
    ]

    expect(computeArrangementMinimums(windows)).toMatchObject({ minCols: 12 })
  })

  it('counts a window in every band it spans', () => {
    // `tall` spans both bands, so it is summed with `a` and again with `b`.
    const windows = [
      win('tall', 0, 0, 4, 8, 4, 8),
      win('a', 4, 0, 4, 4, 3, 2),
      win('b', 4, 4, 4, 4, 6, 2),
    ]

    expect(computeArrangementMinimums(windows)).toMatchObject({ minCols: 10 })
  })
})

describe('reflowScaleByOverlap — when nothing has to change', () => {
  it('leaves a valid arrangement exactly where it is', () => {
    const windows = [
      win('a', 0, 0, 4, 3, 2, 2),
      win('b', 4, 0, 4, 3, 2, 2),
      win('c', 0, 3, 8, 3, 2, 2),
    ]

    const { next } = reflowScaleByOverlap({ windows, cols: 8, rows: 6 })

    expect(byId(next)).toEqual(byId(windows))
  })

  it('is idempotent: reflowing twice is the same as reflowing once', () => {
    const windows = [win('a', 0, 0, 10, 6, 2, 2), win('b', 6, 2, 6, 6, 2, 2)]

    const once = reflowScaleByOverlap({ windows, cols: 8, rows: 6 }).next
    const twice = reflowScaleByOverlap({ windows: once, cols: 8, rows: 6 }).next

    expect(byId(twice)).toEqual(byId(once))
  })

  it('returns new objects and does not touch the ones it was given', () => {
    const windows = [win('a', 0, 0, 10, 6, 2, 2)]
    const before = structuredClone(windows)

    const { next } = reflowScaleByOverlap({ windows, cols: 4, rows: 4 })

    expect(windows).toEqual(before)
    expect(next[0]).not.toBe(windows[0])
  })

  it('returns nothing for a non-array rather than throwing', () => {
    expect(reflowScaleByOverlap({ windows: null, cols: 8, rows: 6 })).toEqual({
      next: [],
      requiredRows: 6,
    })
  })

  it('handles an empty arrangement', () => {
    expect(reflowScaleByOverlap({ windows: [], cols: 8, rows: 6 })).toEqual({
      next: [],
      requiredRows: 6,
    })
  })
})

describe('reflowScaleByOverlap — shrinking before moving', () => {
  it('clips a window that hangs over the bottom edge to the grid', () => {
    const { next } = reflowScaleByOverlap({
      windows: [win('a', 0, 0, 3, 8, 2, 2)],
      cols: 8,
      rows: 6,
    })

    // Eight rows tall in a six-row grid: loses exactly the two that hang over.
    //
    // Note that this asserts the *outcome*, not the branch. Deleting the bottom-edge shrink
    // leaves this passing — verified by mutation — because a window that overflows always
    // fails the final `canPlace` check, and the safety net below re-clamps it to exactly the
    // same size. The overflow branch is therefore redundant with the safety net rather than
    // load-bearing, which is worth knowing before anyone "fixes" one of the two.
    expect(next[0]).toMatchObject({ x: 0, y: 0, height: 6 })
  })

  it('never reaches its right-edge branch, because normalising has already slid the window in', () => {
    // Worth pinning, because reading the function suggests otherwise. `normalise` clamps `x`
    // to `cols - width` *before* the overflow check, and `width` is itself clamped to `cols`,
    // so `x + width > cols` is unreachable and the right-edge shrink is dead code. The
    // *vertical* twin is live, because `height` is deliberately not clamped to `rows` — that
    // is what lets the grid grow and the desktop scroll.
    const { next } = reflowScaleByOverlap({
      windows: [win('a', 2, 0, 8, 3, 2, 2)],
      cols: 8,
      rows: 6,
    })

    // Slid to x=0 at its full width, not shrunk at x=2.
    expect(next[0]).toMatchObject({ x: 0, width: 8 })
  })

  it('slides a window inward when its minimum still will not fit at that position', () => {
    const { next } = reflowScaleByOverlap({
      windows: [win('a', 5, 0, 6, 3, 6, 2)],
      cols: 8,
      rows: 6,
    })

    expect(next[0]).toMatchObject({ x: 2, width: 6 })
    expect(canPlace(next[0], { cols: 8, rows: 6 })).toBe(true)
  })

  it('splits an overlap between the two windows, then slides the later one into the gap', () => {
    // `a` is placed first (equal y, lower x). `b` overlaps it by two columns; each gives up
    // one, which removes exactly the overlap in total — but only *one* of the two shrinks in
    // a direction that helps (see the DEFECT case below), so `b` also moves one column right
    // to finish the job. The window that was there first keeps its corner.
    const windows = [win('a', 0, 0, 6, 4, 1, 1), win('b', 4, 0, 6, 4, 1, 1)]

    const { next } = reflowScaleByOverlap({ windows, cols: 10, rows: 6 })
    const result = byId(next)

    expect(result.a.width + result.b.width).toBe(10) // two columns of overlap removed
    expect(result.a).toMatchObject({ x: 0, y: 0, width: 5 })
    expect(result.b).toMatchObject({ x: 5, y: 0, width: 5 })
    expect(rectsOverlap(result.a, result.b)).toBe(false)
  })

  it('takes the whole cost from whichever window still has slack', () => {
    // `b` is already at its minimum width, so `a` gives up both columns.
    const windows = [win('a', 0, 0, 6, 4, 1, 1), win('b', 4, 0, 6, 4, 6, 1)]

    const result = byId(reflowScaleByOverlap({ windows, cols: 10, rows: 6 }).next)

    expect(result.b.width).toBe(6)
    expect(result.a.width).toBe(4)
  })

  it('resolves on the axis with the smaller overlap', () => {
    // Overlap is 1 column wide and 3 rows tall; width is the cheaper concession.
    const windows = [win('a', 0, 0, 4, 4, 1, 1), win('b', 3, 1, 4, 3, 1, 1)]

    const result = byId(reflowScaleByOverlap({ windows, cols: 10, rows: 6 }).next)

    expect(result.a.height + result.b.height).toBe(7)
    expect(result.a.width + result.b.width).toBe(7)
    expect(rectsOverlap(result.a, result.b)).toBe(false)
  })

  it('places windows top-to-bottom then left-to-right, so the top row keeps its place', () => {
    const windows = [
      win('bottom', 0, 4, 6, 4, 1, 1),
      win('topRight', 6, 0, 6, 4, 1, 1),
      win('topLeft', 0, 0, 6, 4, 1, 1),
    ]

    const { next } = reflowScaleByOverlap({ windows, cols: 12, rows: 8 })

    expect(next.map((w) => w.id)).toEqual(['topLeft', 'topRight', 'bottom'])
  })
})

describe('reflowScaleByOverlap — defects kept as found', () => {
  /**
   * Three assertions about things that are wrong. They are here rather than fixed because
   * the ticket is a behaviour-preserving port and every one of them changes where windows
   * land; pinning them means a later fix arrives as a failing test with the reason attached.
   */

  it('DEFECT: only one of the two windows shrinks in a useful direction', () => {
    // Both windows shrink from their **right** edge, because that is the only edge `width`
    // controls. When the collision is on `curr`'s left, taking a column off `curr` moves the
    // wrong edge and removes none of the overlap — so a 50/50 split resolves half of it and
    // the rest is paid for by moving the window. `splitCost` has no notion of which side the
    // overlap is on.
    const windows = [win('a', 0, 0, 6, 4, 1, 1), win('b', 4, 0, 6, 4, 1, 1)]

    const result = byId(reflowScaleByOverlap({ windows, cols: 10, rows: 6 }).next)

    // Half the overlap was paid in width by the window it did not help…
    expect(result.b.width).toBe(5)
    // …and the other half in a move `b` should not have needed.
    expect(result.b.x).toBe(5)
  })

  it('DEFECT: shrinks a window below its own minimum when the other has no slack', () => {
    // `b` is already at its minimum, so `splitCost` sends the whole four-column overlap to
    // `a` — whose own floor of 5 it never consults. `a` ends up at 2, three columns under a
    // minimum that exists because its content does not fit any smaller.
    //
    // The correct behaviour is to take what each can afford and *move* the rest, which is the
    // branch immediately below in `reflowScaleByOverlap` — unreachable, because the shrink
    // reports success.
    const windows = [win('a', 0, 0, 6, 4, 5, 1), win('b', 2, 0, 6, 4, 6, 1)]

    const result = byId(reflowScaleByOverlap({ windows, cols: 12, rows: 6 }).next)

    expect(result.a.width).toBe(2)
    expect(result.a.width).toBeLessThan(result.a.minWidth)
  })

  it('DEFECT: shrinks a window to zero width when neither side has any slack', () => {
    // The extreme of the same arithmetic. Two identical windows, both already at their
    // minimum, in a grid exactly one of them wide: the whole overlap is charged to the one
    // already placed, and `other.width - takeOther` is zero. A window with no width renders
    // as nothing, has no header to grab, and cannot be got back.
    const windows = [win('a', 0, 0, 4, 4, 4, 4), win('b', 0, 0, 4, 4, 4, 4)]

    const { next } = reflowScaleByOverlap({ windows, cols: 4, rows: 4 })

    expect(next.some((w) => w.width === 0)).toBe(true)
  })

  it('DEFECT: leaves a window outside the grid when its minimum will not fit', () => {
    // A window whose minimum height is taller than the grid. Nothing can shrink it, nowhere
    // can hold it, `findNearestFreePosition` reports `found: false`, and the safety net puts
    // it back exactly where it could not go — overflowing the grid rather than being refused.
    const { next } = reflowScaleByOverlap({
      windows: [win('a', 0, 0, 4, 4, 4, 4)],
      cols: 4,
      rows: 2,
    })

    expect(canPlace(next[0], { cols: 4, rows: 2 })).toBe(false)
  })
})

describe('reflowScaleByOverlap — the invariants, over many random arrangements', () => {
  /**
   * Build a genuinely feasible arrangement: recursively split the grid, so the windows
   * partition it and therefore cannot overlap and cannot escape.
   */
  const tile = (random, x, y, cols, rows, depth, out) => {
    const splittable = depth < 4 && (cols > 3 || rows > 3) && random.float() < 0.8
    if (!splittable) {
      out.push({
        id: `w${out.length}`,
        x,
        y,
        width: cols,
        height: rows,
        minWidth: random.int(1, cols),
        minHeight: random.int(1, rows),
      })
      return out
    }
    if (cols >= rows && cols > 1) {
      const cut = random.int(1, cols - 1)
      tile(random, x, y, cut, rows, depth + 1, out)
      tile(random, x + cut, y, cols - cut, rows, depth + 1, out)
    } else if (rows > 1) {
      const cut = random.int(1, rows - 1)
      tile(random, x, y, cols, cut, depth + 1, out)
      tile(random, x, y + cut, cols, rows - cut, depth + 1, out)
    } else {
      out.push({
        id: `w${out.length}`,
        x,
        y,
        width: cols,
        height: rows,
        minWidth: 1,
        minHeight: 1,
      })
    }
    return out
  }

  it('leaves a feasible arrangement non-overlapping and inside the grid', () => {
    const random = seededRandom(4242)

    for (let run = 0; run < 200; run += 1) {
      const cols = random.int(4, 16)
      const rows = random.int(4, 12)
      const windows = tile(random, 0, 0, cols, rows, 0, [])

      const { next } = reflowScaleByOverlap({ windows, cols, rows })

      expect(next, `run ${run}: lost or gained a window`).toHaveLength(windows.length)

      for (const w of next) {
        expect(canPlace(w, { cols, rows }), `run ${run}: ${w.id} escapes the grid`).toBe(true)
        expect(w.width, `run ${run}: ${w.id} below its minimum width`).toBeGreaterThanOrEqual(
          w.minWidth,
        )
        expect(w.height, `run ${run}: ${w.id} below its minimum height`).toBeGreaterThanOrEqual(
          w.minHeight,
        )
      }

      for (const [a, b] of pairs(next)) {
        expect(rectsOverlap(a, b), `run ${run}: ${a.id} overlaps ${b.id}`).toBe(false)
      }
    }
  })

  it('is the identity on an arrangement that already fits, whatever the arrangement', () => {
    const random = seededRandom(99)

    for (let run = 0; run < 200; run += 1) {
      const cols = random.int(4, 16)
      const rows = random.int(4, 12)
      const windows = tile(random, 0, 0, cols, rows, 0, [])

      const { next } = reflowScaleByOverlap({ windows, cols, rows })

      expect(byId(next), `run ${run}`).toEqual(byId(windows))
    }
  })

  it('keeps the invariants when the grid grows underneath the arrangement', () => {
    const random = seededRandom(1337)

    for (let run = 0; run < 200; run += 1) {
      const cols = random.int(4, 12)
      const rows = random.int(4, 10)
      const windows = tile(random, 0, 0, cols, rows, 0, [])
      const biggerCols = cols + random.int(1, 6)
      const biggerRows = rows + random.int(1, 6)

      const { next } = reflowScaleByOverlap({ windows, cols: biggerCols, rows: biggerRows })

      for (const w of next) {
        expect(canPlace(w, { cols: biggerCols, rows: biggerRows }), `run ${run}: ${w.id}`).toBe(true)
      }
      for (const [a, b] of pairs(next)) {
        expect(rectsOverlap(a, b), `run ${run}: ${a.id} overlaps ${b.id}`).toBe(false)
      }
    }
  })
})
