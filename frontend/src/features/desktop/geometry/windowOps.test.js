import { describe, expect, it } from 'vitest'

import { canPlace, rectsOverlap } from './rects'
import {
  closeWindow,
  collapseToMinimum,
  growToFill,
  moveWindow,
  resizeWindow,
  restoreWindow,
} from './windowOps'

/**
 * ANV-33 — the window operations.
 *
 * **These tests prove real behaviour.** In the original every one of these lived as an
 * anonymous callback inside `BinPackingLayout`'s JSX, so the only way to ask "does maximise
 * grow upward before it grows right" was to render a desktop, measure it, and click — which
 * jsdom cannot do. As functions they answer directly.
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

const byId = (list, id) => list.find((w) => w.id === id)

describe('collapseToMinimum', () => {
  it('shrinks the window to its minimum without moving its corner', () => {
    const windows = [win('a', 3, 2, 8, 6, 2, 3)]

    expect(collapseToMinimum(windows, 'a')[0]).toMatchObject({
      x: 3,
      y: 2,
      width: 2,
      height: 3,
    })
  })

  it('treats a missing minimum as one cell', () => {
    const windows = [{ id: 'a', x: 0, y: 0, width: 8, height: 6 }]

    expect(collapseToMinimum(windows, 'a')[0]).toMatchObject({ width: 1, height: 1 })
  })

  it('is idempotent — a window already at its minimum does not change', () => {
    const windows = [win('a', 0, 0, 2, 3, 2, 3)]

    expect(collapseToMinimum(collapseToMinimum(windows, 'a'), 'a')[0]).toMatchObject({
      width: 2,
      height: 3,
    })
  })

  it('leaves every other window alone, by identity', () => {
    const windows = [win('a', 0, 0, 8, 6, 2, 2), win('b', 8, 0, 4, 4, 2, 2)]

    const next = collapseToMinimum(windows, 'a')

    expect(next[1]).toBe(windows[1])
  })

  it('does nothing for an id that names no window', () => {
    const windows = [win('a', 0, 0, 8, 6, 2, 2)]

    expect(collapseToMinimum(windows, 'nope')).toEqual(windows)
  })

  it('does not mutate its input', () => {
    const windows = [win('a', 0, 0, 8, 6, 2, 2)]

    collapseToMinimum(windows, 'a')

    expect(windows[0]).toMatchObject({ width: 8, height: 6 })
  })
})

describe('growToFill', () => {
  const grid = { cols: 10, rows: 8 }

  it('fills the whole grid when the window is alone on it', () => {
    const windows = [win('a', 3, 2, 2, 2)]

    expect(growToFill(windows, 'a', grid)[0]).toMatchObject({
      x: 0,
      y: 0,
      width: 10,
      height: 8,
    })
  })

  it('grows up first, keeping the bottom edge exactly where it was', () => {
    // A wall along the whole left column stops the leftward pass, so the only evidence of the
    // ordering is that the *height* grew by exactly the rows gained above: the bottom edge is
    // still at y=4 after the upward pass, and the downward pass then takes it to the floor.
    const windows = [win('a', 4, 2, 3, 2), win('wall', 0, 0, 4, 8, 1, 1)]

    const grown = byId(growToFill(windows, 'a', grid), 'a')

    expect(grown).toMatchObject({ x: 4, y: 0, width: 6, height: 8 })
  })

  it('grows left before it grows right, keeping the right edge anchored', () => {
    // `blocker` occupies the top-right. Growing left first claims columns 0-3 at full height;
    // a right-first implementation would have taken the free bottom-right instead and then
    // been unable to reach the left at all.
    const windows = [win('a', 4, 4, 2, 2), win('blocker', 6, 0, 4, 8, 1, 1)]

    const grown = byId(growToFill(windows, 'a', grid), 'a')

    expect(grown).toMatchObject({ x: 0, y: 0, width: 6, height: 8 })
  })

  it('grows up BEFORE it grows right, so an obstacle in the top-right cannot cap the height', () => {
    // The discriminating fixture for the pass *order*. Growing right first would take the
    // free bottom half at full width (0, 4, 10, 4) and then be unable to rise past the
    // obstacle at all; growing up first claims the full height of the left six columns.
    // Both results are legal and neither is smaller, which is why a simpler fixture cannot
    // tell them apart — the shapes differ, and the shape is the behaviour.
    const windows = [win('a', 4, 4, 2, 2), win('topRight', 6, 0, 4, 4, 1, 1)]

    expect(byId(growToFill(windows, 'a', grid), 'a')).toMatchObject({
      x: 0,
      y: 0,
      width: 6,
      height: 8,
    })
  })

  it('keeps the bottom edge pinned while growing up, so the height it gains is not given back', () => {
    // The discriminating fixture for the *pinning*. Sliding up without gaining height would
    // leave a 2-tall window at the top, which then widens to the full ten columns (nothing
    // is in its way up there) and is stopped at four rows by the obstacle below — (0, 4)
    // shaped wrong and a third smaller. Pinning the bottom means the window is already six
    // rows tall when it starts widening, so the obstacle stops the *width* instead.
    const windows = [win('a', 4, 4, 2, 2), win('bottomRight', 6, 4, 4, 4, 1, 1)]

    expect(byId(growToFill(windows, 'a', grid), 'a')).toMatchObject({
      x: 0,
      y: 0,
      width: 6,
      height: 8,
    })
  })

  it('stops at a neighbour rather than growing over it', () => {
    const windows = [win('a', 0, 0, 2, 2), win('b', 5, 0, 5, 8, 1, 1)]

    const next = growToFill(windows, 'a', grid)

    expect(byId(next, 'a')).toMatchObject({ x: 0, y: 0, width: 5, height: 8 })
    expect(rectsOverlap(byId(next, 'a'), byId(next, 'b'))).toBe(false)
  })

  it('tiles rather than stacks when two windows are grown in turn', () => {
    const windows = [win('a', 0, 0, 2, 2), win('b', 6, 4, 2, 2)]

    const afterA = growToFill(windows, 'a', grid)
    const afterB = growToFill(afterA, 'b', grid)

    expect(rectsOverlap(byId(afterB, 'a'), byId(afterB, 'b'))).toBe(false)
    for (const w of afterB) expect(canPlace(w, grid)).toBe(true)
  })

  it('changes nothing when the window already fills the grid', () => {
    const windows = [win('a', 0, 0, 10, 8)]

    expect(growToFill(windows, 'a', grid)[0]).toMatchObject({
      x: 0,
      y: 0,
      width: 10,
      height: 8,
    })
  })

  it('cannot grow at all when it is boxed in on every side', () => {
    const windows = [
      win('a', 4, 4, 2, 2),
      win('top', 0, 0, 10, 4, 1, 1),
      win('bottom', 0, 6, 10, 2, 1, 1),
      win('left', 0, 4, 4, 2, 1, 1),
      win('right', 6, 4, 4, 2, 1, 1),
    ]

    expect(byId(growToFill(windows, 'a', grid), 'a')).toMatchObject({
      x: 4,
      y: 4,
      width: 2,
      height: 2,
    })
  })

  it('returns the same array when the id names no window', () => {
    const windows = [win('a', 0, 0, 2, 2)]

    expect(growToFill(windows, 'nope', grid)).toBe(windows)
  })

  it('never produces an overlap, wherever the window starts', () => {
    const others = [win('x', 0, 0, 3, 3, 1, 1), win('y', 7, 5, 3, 3, 1, 1)]

    for (let x = 3; x <= 6; x += 1) {
      for (let y = 3; y <= 4; y += 1) {
        const windows = [win('a', x, y, 1, 1), ...others]
        const next = growToFill(windows, 'a', grid)
        const grown = byId(next, 'a')

        expect(canPlace(grown, grid), `from (${x}, ${y})`).toBe(true)
        expect(rectsOverlap(grown, byId(next, 'x')), `from (${x}, ${y})`).toBe(false)
        expect(rectsOverlap(grown, byId(next, 'y')), `from (${x}, ${y})`).toBe(false)
      }
    }
  })

  it('does not mutate its input', () => {
    const windows = [win('a', 3, 2, 2, 2)]

    growToFill(windows, 'a', grid)

    expect(windows[0]).toMatchObject({ x: 3, y: 2, width: 2, height: 2 })
  })
})

describe('moveWindow, resizeWindow, restoreWindow and closeWindow', () => {
  const windows = [win('a', 0, 0, 4, 4, 2, 2), win('b', 4, 0, 4, 4, 2, 2)]

  it('moveWindow changes the position and nothing else', () => {
    const moved = byId(moveWindow(windows, 'a', { x: 6, y: 3 }), 'a')

    expect(moved).toMatchObject({ x: 6, y: 3, width: 4, height: 4, minWidth: 2 })
  })

  it('resizeWindow applies the position and the size together', () => {
    const resized = byId(resizeWindow(windows, 'b', { x: 1, y: 1, width: 9, height: 2 }), 'b')

    expect(resized).toMatchObject({ x: 1, y: 1, width: 9, height: 2 })
  })

  it('restoreWindow puts back a remembered rectangle', () => {
    const collapsed = collapseToMinimum(windows, 'a')

    expect(byId(restoreWindow(collapsed, 'a', { x: 0, y: 0, width: 4, height: 4 }), 'a')).toMatchObject(
      { x: 0, y: 0, width: 4, height: 4 },
    )
  })

  it('closeWindow removes exactly the one window', () => {
    expect(closeWindow(windows, 'a').map((w) => w.id)).toEqual(['b'])
  })

  it('closeWindow is a no-op for an unknown id', () => {
    expect(closeWindow(windows, 'nope')).toHaveLength(2)
  })

  it('none of them mutates the input', () => {
    moveWindow(windows, 'a', { x: 9, y: 9 })
    resizeWindow(windows, 'a', { x: 9, y: 9, width: 1, height: 1 })
    closeWindow(windows, 'a')

    expect(windows[0]).toMatchObject({ id: 'a', x: 0, y: 0, width: 4, height: 4 })
    expect(windows).toHaveLength(2)
  })
})
