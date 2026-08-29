import { describe, expect, it } from 'vitest'

import {
  canPlace,
  computeAllowedSize,
  findNearestFreePosition,
  fitCenteredRect,
  rectsOverlap,
} from './rects'

/**
 * ANV-33 — rectangle geometry.
 *
 * **These tests prove real behaviour.** No DOM, no React, no measurement: every case is a
 * claim about integers.
 */

const rect = (x, y, width, height, id) => ({ x, y, width, height, id })

describe('rectsOverlap', () => {
  it('is true when the rectangles share area', () => {
    expect(rectsOverlap(rect(0, 0, 4, 4), rect(2, 2, 4, 4))).toBe(true)
  })

  it('is true when one contains the other', () => {
    expect(rectsOverlap(rect(0, 0, 10, 10), rect(3, 3, 2, 2))).toBe(true)
    expect(rectsOverlap(rect(3, 3, 2, 2), rect(0, 0, 10, 10))).toBe(true)
  })

  it('is true for identical rectangles', () => {
    expect(rectsOverlap(rect(1, 1, 3, 3), rect(1, 1, 3, 3))).toBe(true)
  })

  it.each([
    ['flush to the right', rect(0, 0, 4, 4), rect(4, 0, 4, 4)],
    ['flush to the left', rect(4, 0, 4, 4), rect(0, 0, 4, 4)],
    ['flush below', rect(0, 0, 4, 4), rect(0, 4, 4, 4)],
    ['flush above', rect(0, 4, 4, 4), rect(0, 0, 4, 4)],
  ])('is false for a neighbour %s — touching is not overlapping', (_label, a, b) => {
    expect(rectsOverlap(a, b)).toBe(false)
  })

  it('is false for rectangles that miss on both axes', () => {
    expect(rectsOverlap(rect(0, 0, 2, 2), rect(5, 5, 2, 2))).toBe(false)
  })
})

describe('canPlace', () => {
  const grid = { cols: 10, rows: 6 }

  it('accepts a rectangle that fits with room to spare', () => {
    expect(canPlace(rect(1, 1, 3, 2), grid)).toBe(true)
  })

  it('accepts a rectangle flush against the bottom-right corner', () => {
    expect(canPlace(rect(7, 4, 3, 2), grid)).toBe(true)
  })

  it.each([
    ['a negative x', rect(-1, 0, 2, 2)],
    ['a negative y', rect(0, -1, 2, 2)],
    ['a right edge past the last column', rect(8, 0, 3, 2)],
    ['a bottom edge past the last row', rect(0, 5, 2, 2)],
  ])('rejects %s', (_label, candidate) => {
    expect(canPlace(candidate, grid)).toBe(false)
  })

  it('rejects a rectangle wider than the entire grid', () => {
    expect(canPlace(rect(0, 0, 11, 2), grid)).toBe(false)
  })

  it('rejects a rectangle that collides with something already placed', () => {
    const placed = [rect(2, 2, 4, 2, 'a')]

    expect(canPlace(rect(3, 1, 2, 2), { ...grid, placed })).toBe(false)
  })

  it('ignores the window it was told to exclude, so a resize does not collide with itself', () => {
    const placed = [rect(2, 2, 4, 2, 'a')]

    expect(canPlace(rect(2, 2, 5, 2), { ...grid, placed })).toBe(false)
    expect(canPlace(rect(2, 2, 5, 2), { ...grid, placed, excludeId: 'a' })).toBe(true)
  })

  it('excludes by id only, not by position', () => {
    const placed = [rect(2, 2, 4, 2, 'a'), rect(6, 2, 2, 2, 'b')]

    expect(canPlace(rect(2, 2, 6, 2), { ...grid, placed, excludeId: 'a' })).toBe(false)
  })
})

describe('findNearestFreePosition', () => {
  const grid = { cols: 8, rows: 6 }

  it('returns the requested position untouched when it is free', () => {
    expect(findNearestFreePosition({ x: 3, y: 2, width: 2, height: 2 }, grid)).toEqual({
      x: 3,
      y: 2,
      found: true,
    })
  })

  it('steps outward ring by ring until something fits', () => {
    // A 2×2 blocker at (3, 2) covers every ring-1 offset for a 2×2 candidate — each of the
    // eight neighbouring origins still puts at least one cell on top of it — so the first
    // legal position is at Chebyshev distance 2, and the search must have tried and rejected
    // all eight before getting there.
    const placed = [rect(3, 2, 2, 2, 'a')]

    const spot = findNearestFreePosition({ x: 3, y: 2, width: 2, height: 2 }, { ...grid, placed })

    expect(spot.found).toBe(true)
    expect(Math.max(Math.abs(spot.x - 3), Math.abs(spot.y - 2))).toBe(2)
    expect(canPlace({ x: spot.x, y: spot.y, width: 2, height: 2 }, { ...grid, placed })).toBe(true)
  })

  it('resolves a tie up and to the left, and does so every time', () => {
    // Wide open grid with the target cell blocked: every ring-1 neighbour is free, so the
    // scan order decides. `dx` ascends first, then `dy`, so (-1, -1) wins.
    const placed = [rect(3, 3, 1, 1, 'a')]

    const spot = findNearestFreePosition({ x: 3, y: 3, width: 1, height: 1 }, { ...grid, placed })

    expect(spot).toEqual({ x: 2, y: 2, found: true })
  })

  it('reports found:false and a clamped guess when the grid is completely full', () => {
    // Four 2×3 windows fill a 4×6 grid exactly. Nothing else can go anywhere.
    const placed = [
      rect(0, 0, 2, 3, 'a'),
      rect(2, 0, 2, 3, 'b'),
      rect(0, 3, 2, 3, 'c'),
      rect(2, 3, 2, 3, 'd'),
    ]

    const spot = findNearestFreePosition(
      { x: 1, y: 1, width: 2, height: 2 },
      { cols: 4, rows: 6, placed },
    )

    // The fallback is a *guess*, and it is a colliding one — which is exactly why the flag
    // exists. The original returned these coordinates with no way to tell.
    expect(spot.found).toBe(false)
    expect(canPlace({ x: spot.x, y: spot.y, width: 2, height: 2 }, { cols: 4, rows: 6, placed })).toBe(
      false,
    )
  })

  it('reports found:false for a window larger than the grid', () => {
    const spot = findNearestFreePosition({ x: 0, y: 0, width: 20, height: 20 }, grid)

    expect(spot.found).toBe(false)
    // Clamping a window bigger than the grid puts it at the origin; it still does not fit.
    expect(spot).toMatchObject({ x: 0, y: 0 })
  })

  it('pulls a request that starts outside the grid back inside', () => {
    const spot = findNearestFreePosition({ x: -4, y: -4, width: 2, height: 2 }, grid)

    expect(spot.found).toBe(true)
    expect(canPlace({ x: spot.x, y: spot.y, width: 2, height: 2 }, grid)).toBe(true)
  })

  it('finds the one remaining hole in an almost-full grid', () => {
    // A 4×4 grid with everything filled except the single cell at (3, 3).
    const placed = [rect(0, 0, 4, 3, 'a'), rect(0, 3, 3, 1, 'b')]

    expect(
      findNearestFreePosition({ x: 0, y: 0, width: 1, height: 1 }, { cols: 4, rows: 4, placed }),
    ).toEqual({ x: 3, y: 3, found: true })
  })
})

describe('computeAllowedSize', () => {
  const grid = { cols: 10, rows: 8 }
  const base = { x: 2, y: 1, minWidth: 2, minHeight: 2 }

  it('grants the requested size when nothing is in the way', () => {
    expect(computeAllowedSize({ ...base, width: 5, height: 4 }, grid)).toEqual({
      width: 5,
      height: 4,
    })
  })

  it('clips a request at the grid edge rather than letting it overflow', () => {
    expect(computeAllowedSize({ ...base, width: 40, height: 40 }, grid)).toEqual({
      width: 8, // cols 10 - x 2
      height: 7, // rows 8 - y 1
    })
  })

  it('stops growing at a neighbour — and gives up some height on the way, as it always has', () => {
    // A full-height wall from x=6. Only width is genuinely in the way, but the alternating
    // shrink concedes a row of height on every other step before it has taken enough width,
    // so the result is 4×2 rather than the 4×4 a reader would predict. That costs the user
    // two rows they did not have to lose, and it is the original's behaviour: the loop has no
    // notion of *which* axis the collision is on, unlike `fitCenteredRect`'s bounds branch.
    // Preserved deliberately; pinned here so a future fix is a visible change, not a drift.
    const placed = [rect(6, 0, 4, 8, 'wall')]

    expect(computeAllowedSize({ ...base, width: 8, height: 4 }, { ...grid, placed })).toEqual({
      width: 4, // x 2 .. the wall at x 6
      height: 2, // conceded to the alternation, not to the wall
    })
  })

  it('never returns less than the minimum, even when the grid edge is closer than that', () => {
    // One free column to the right of x=9, and a minimum of 3. The floor wins and the result
    // knowingly overflows: the caller's job is to move the window, not to render it at a
    // width its content cannot use.
    const allowed = computeAllowedSize(
      { x: 9, y: 0, width: 3, height: 3, minWidth: 3, minHeight: 3 },
      grid,
    )

    expect(allowed.width).toBe(3)
    expect(canPlace({ x: 9, y: 0, ...allowed }, grid)).toBe(false)
  })

  it('never returns less than the minimum, even when the minimum does not fit', () => {
    const placed = [rect(3, 0, 7, 8, 'wall')]

    // Only one free column at x=2, but the minimum width is 2.
    const allowed = computeAllowedSize({ ...base, width: 6, height: 4 }, { ...grid, placed })

    expect(allowed.width).toBe(base.minWidth)
    // And the result is knowingly colliding: the caller has to move the window.
    expect(canPlace({ x: 2, y: 1, ...allowed }, { ...grid, placed })).toBe(false)
  })

  it('does not collide with the window being resized itself', () => {
    const placed = [rect(2, 1, 3, 3, 'self')]

    expect(
      computeAllowedSize({ ...base, width: 5, height: 4 }, { ...grid, placed, excludeId: 'self' }),
    ).toEqual({ width: 5, height: 4 })
  })

  it('gives up width and height alternately rather than collapsing one axis', () => {
    // A neighbour occupying the bottom-right of the requested area. The sequence is width,
    // height, width — three concessions, two of them on one axis and one on the other —
    // reaching 4×5. Exhausting width first would have reached 4×6 by luck here, but the
    // alternation is what stops a diagonal drag into a tall neighbour collapsing the window
    // to a one-cell sliver.
    const placed = [rect(6, 4, 4, 4, 'corner')]

    const allowed = computeAllowedSize(
      { x: 2, y: 1, width: 6, height: 6, minWidth: 1, minHeight: 1 },
      { ...grid, placed },
    )

    expect(allowed).toEqual({ width: 4, height: 5 })
    expect(canPlace({ x: 2, y: 1, ...allowed }, { ...grid, placed })).toBe(true)
  })
})

describe('fitCenteredRect', () => {
  const grid = { cols: 12, rows: 10 }

  it('centres the rectangle on the point when there is room', () => {
    const fit = fitCenteredRect(
      { centerX: 6, centerY: 5, width: 4, height: 2, minWidth: 2, minHeight: 1 },
      grid,
    )

    expect(fit).toEqual({ x: 4, y: 4, width: 4, height: 2 })
  })

  it('keeps the requested size when the cursor is well inside the grid', () => {
    const fit = fitCenteredRect(
      { centerX: 5.5, centerY: 5.5, width: 6, height: 4, minWidth: 1, minHeight: 1 },
      grid,
    )

    expect(fit).toMatchObject({ width: 6, height: 4 })
  })

  it('shrinks on the axis that hangs off the edge, not on the other one', () => {
    const fit = fitCenteredRect(
      { centerX: 0, centerY: 5, width: 6, height: 4, minWidth: 1, minHeight: 1 },
      grid,
    )

    // Width had to give; height did not.
    expect(fit.height).toBe(4)
    expect(fit.width).toBeLessThan(6)
    expect(fit.x).toBeGreaterThanOrEqual(0)
  })

  it('shrinks rather than moving when a neighbour is in the way', () => {
    const placed = [rect(8, 0, 4, 10, 'wall')]

    const fit = fitCenteredRect(
      { centerX: 6, centerY: 5, width: 6, height: 4, minWidth: 1, minHeight: 1 },
      { ...grid, placed },
    )

    expect(fit.width).toBeLessThan(6)
    expect(canPlace(fit, { ...grid, placed })).toBe(true)
  })

  it('ignores the window being dragged, so it can be dropped where it already is', () => {
    const placed = [rect(4, 4, 4, 2, 'self')]

    const fit = fitCenteredRect(
      { centerX: 6, centerY: 5, width: 4, height: 2, minWidth: 4, minHeight: 2 },
      { ...grid, placed, excludeId: 'self' },
    )

    expect(fit).toEqual({ x: 4, y: 4, width: 4, height: 2 })
  })

  it('gives up centring and takes the nearest free cell before it gives up entirely', () => {
    // Minimum equals requested, so nothing can shrink; the whole left half is walled off.
    const placed = [rect(0, 0, 8, 10, 'wall')]

    const fit = fitCenteredRect(
      { centerX: 4, centerY: 5, width: 4, height: 4, minWidth: 4, minHeight: 4 },
      { ...grid, placed },
    )

    expect(fit).not.toBeNull()
    expect(canPlace(fit, { ...grid, placed })).toBe(true)
  })

  it('returns null when there is nowhere at all for the rectangle', () => {
    const placed = [rect(0, 0, 12, 10, 'everything')]

    expect(
      fitCenteredRect(
        { centerX: 6, centerY: 5, width: 4, height: 4, minWidth: 4, minHeight: 4 },
        { ...grid, placed },
      ),
    ).toBeNull()
  })

  it.each([
    ['NaN', Number.NaN],
    ['Infinity', Number.POSITIVE_INFINITY],
  ])('returns null for a %s cursor rather than committing a NaN position', (_label, value) => {
    // Reachable whenever a pointer event arrives without coordinates. Unguarded, the
    // comparisons below all evaluate false against `NaN`, so an impossible rectangle reads as
    // legal and a window is placed at a position nothing can ever find again.
    expect(
      fitCenteredRect(
        { centerX: value, centerY: 5, width: 4, height: 4, minWidth: 1, minHeight: 1 },
        grid,
      ),
    ).toBeNull()
  })

  it('returns null for a grid with no cells in it', () => {
    expect(
      fitCenteredRect(
        { centerX: 0, centerY: 0, width: 2, height: 2, minWidth: 1, minHeight: 1 },
        { cols: 0, rows: 0 },
      ),
    ).toBeNull()
  })

  it('never returns a rectangle that collides or escapes, wherever the cursor is', () => {
    const placed = [rect(2, 2, 3, 3, 'a'), rect(7, 1, 4, 6, 'b')]
    const bounds = { ...grid, placed }

    for (let cx = -2; cx <= 14; cx += 1) {
      for (let cy = -2; cy <= 12; cy += 1) {
        const fit = fitCenteredRect(
          { centerX: cx, centerY: cy, width: 5, height: 4, minWidth: 2, minHeight: 2 },
          bounds,
        )
        if (fit === null) continue
        expect(canPlace(fit, bounds), `cursor (${cx}, ${cy}) produced an illegal fit`).toBe(true)
        expect(fit.width).toBeGreaterThanOrEqual(2)
        expect(fit.height).toBeGreaterThanOrEqual(2)
      }
    }
  })
})
