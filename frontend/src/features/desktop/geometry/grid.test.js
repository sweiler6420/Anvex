import { describe, expect, it } from 'vitest'

import {
  cellsToPixels,
  computeGridSpec,
  computeGridSpecForWindows,
  EMPTY_GRID_SPEC,
  isGridReady,
  pixelToCell,
} from './grid'

/**
 * ANV-33 — the grid.
 *
 * **These tests prove real behaviour.** The container's size arrives as an argument, so
 * nothing here needs jsdom to lay anything out; these are the assertions the component tests
 * cannot make, because in a component the same numbers would have had to be invented.
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

describe('computeGridSpec', () => {
  it('fills a container that is an exact multiple of the cell size, with no offset', () => {
    expect(computeGridSpec(200, 100, 20, 4, 3)).toEqual({
      cols: 10,
      rows: 5,
      innerW: 200,
      innerH: 100,
      offsetLeft: 0,
      offsetTop: 0,
    })
  })

  it('floors a partial cell and turns the remainder into a centring offset', () => {
    // 205 / 20 = 10.25 columns → 10 columns of 20px = 200px, 5px left over, 2px each side.
    expect(computeGridSpec(205, 111, 20, 4, 3)).toMatchObject({
      cols: 10,
      rows: 5,
      offsetLeft: 2,
      offsetTop: 5,
    })
  })

  it('honours the minimum column and row counts, even past the container', () => {
    const spec = computeGridSpec(40, 20, 20, 6, 4)

    expect(spec).toMatchObject({ cols: 6, rows: 4, innerW: 120, innerH: 80 })
    // The grid is bigger than the container, so there is no leftover space to centre in.
    expect(spec).toMatchObject({ offsetLeft: 0, offsetTop: 0 })
  })

  it('falls back to the minimum for a container with no size', () => {
    expect(computeGridSpec(0, 0, 20, 4, 3)).toMatchObject({ cols: 4, rows: 3 })
  })

  it('falls back to the minimum for a negative container', () => {
    expect(computeGridSpec(-500, -500, 20, 4, 3)).toMatchObject({ cols: 4, rows: 3 })
  })
})

describe('computeGridSpecForWindows — when there is nothing to measure', () => {
  it.each([
    ['a zero width', { containerWidth: 0, containerHeight: 400 }],
    ['a zero height', { containerWidth: 800, containerHeight: 0 }],
    ['a negative size', { containerWidth: -10, containerHeight: -10 }],
    ['a NaN size', { containerWidth: Number.NaN, containerHeight: 400 }],
  ])('returns the empty spec for %s — which is what jsdom always reports', (_label, size) => {
    expect(
      computeGridSpecForWindows({
        ...size,
        cellSize: 20,
        minCols: 4,
        minRows: 3,
        windows: [win('a', 0, 0, 4, 4)],
      }),
    ).toBe(EMPTY_GRID_SPEC)
  })

  it('returns the empty spec for a zero cell size rather than an infinite grid', () => {
    // `Math.floor(800 / 0)` is `Infinity`. The original had no guard and would have tried to
    // render an infinite number of grid lines.
    expect(
      computeGridSpecForWindows({
        containerWidth: 800,
        containerHeight: 400,
        cellSize: 0,
        minCols: 4,
        minRows: 3,
        windows: [],
      }),
    ).toBe(EMPTY_GRID_SPEC)
  })
})

describe('computeGridSpecForWindows — sizing and centring', () => {
  const base = { cellSize: 20, minCols: 4, minRows: 3, windows: [] }

  it('centres a grid that fits, and reports no overflow', () => {
    const spec = computeGridSpecForWindows({
      ...base,
      containerWidth: 810,
      containerHeight: 415,
    })

    expect(spec).toMatchObject({
      cols: 40,
      rows: 20,
      innerW: 800,
      innerH: 400,
      offsetLeft: 5,
      offsetTop: 7,
      overflowX: false,
      overflowY: false,
    })
  })

  it('pins an overflowing grid to the top-left and flags the axis', () => {
    // Minimums bigger than the container: 10 columns of 20px is 200px in a 100px box.
    //
    // The assertion is on the outcome, not the `overflowX ? 0 :` branch that produces it —
    // and mutation shows the branch is redundant: overflow means `containerWidth - innerW` is
    // negative, and `Math.max(0, …)` already yields 0. The ternary documents the intent; it
    // decides nothing. Worth knowing before anyone deletes the `Math.max` instead.
    const spec = computeGridSpecForWindows({
      ...base,
      minCols: 10,
      minRows: 10,
      containerWidth: 100,
      containerHeight: 100,
    })

    expect(spec).toMatchObject({
      cols: 10,
      rows: 10,
      offsetLeft: 0,
      offsetTop: 0,
      overflowX: true,
      overflowY: true,
    })
  })

  it('flags one axis at a time', () => {
    const spec = computeGridSpecForWindows({
      ...base,
      minCols: 20,
      containerWidth: 100,
      containerHeight: 415,
    })

    expect(spec).toMatchObject({ overflowX: true, overflowY: false })
    expect(spec.offsetTop).toBeGreaterThan(0)
  })
})

describe('computeGridSpecForWindows — the windows push back', () => {
  it('widens the grid past what the container can show, rather than crushing a row', () => {
    // Three windows side by side, each needing 6 columns at its minimum: 18 columns, in a
    // container that could only show 5.
    const windows = [
      win('a', 0, 0, 6, 4, 6, 4),
      win('b', 6, 0, 6, 4, 6, 4),
      win('c', 12, 0, 6, 4, 6, 4),
    ]

    const spec = computeGridSpecForWindows({
      containerWidth: 100,
      containerHeight: 400,
      cellSize: 20,
      minCols: 4,
      minRows: 3,
      windows,
    })

    expect(spec.cols).toBe(18)
    expect(spec.overflowX).toBe(true)
  })

  it('does not widen for windows that are stacked rather than side by side', () => {
    // The same three windows, one above the other. No band contains more than one of them,
    // so the arrangement asks for 6 columns, not 18 — and 6 is under what the container can
    // show, so the container wins.
    const windows = [
      win('a', 0, 0, 6, 4, 6, 4),
      win('b', 0, 4, 6, 4, 6, 4),
      win('c', 0, 8, 6, 4, 6, 4),
    ]

    const spec = computeGridSpecForWindows({
      containerWidth: 400,
      containerHeight: 400,
      cellSize: 20,
      minCols: 4,
      minRows: 3,
      windows,
    })

    expect(spec.cols).toBe(20)
    expect(spec.overflowX).toBe(false)
  })

  it('grows taller when the minimum-size pack needs more rows than the container has', () => {
    // Six windows, each 4 columns minimum, in a 4-column grid: they can only stack, six deep.
    const windows = Array.from({ length: 6 }, (_, i) => win(`w${i}`, 0, i * 3, 4, 3, 4, 3))

    const spec = computeGridSpecForWindows({
      containerWidth: 80,
      containerHeight: 60,
      cellSize: 20,
      minCols: 4,
      minRows: 3,
      windows,
    })

    expect(spec.cols).toBe(4)
    expect(spec.rows).toBe(18)
    expect(spec.overflowY).toBe(true)
  })

  it('never shrinks below what the container alone would give', () => {
    const empty = computeGridSpecForWindows({
      containerWidth: 800,
      containerHeight: 400,
      cellSize: 20,
      minCols: 4,
      minRows: 3,
      windows: [],
    })
    const withWindows = computeGridSpecForWindows({
      containerWidth: 800,
      containerHeight: 400,
      cellSize: 20,
      minCols: 4,
      minRows: 3,
      windows: [win('a', 0, 0, 3, 2, 3, 2)],
    })

    expect(withWindows.cols).toBeGreaterThanOrEqual(empty.cols)
    expect(withWindows.rows).toBeGreaterThanOrEqual(empty.rows)
  })
})

describe('isGridReady', () => {
  it('is false for the empty spec — the state every jsdom render is in', () => {
    expect(isGridReady(EMPTY_GRID_SPEC, 20)).toBe(false)
  })

  it('is false when the cell size is zero, however many cells the spec claims', () => {
    expect(isGridReady({ cols: 10, rows: 10 }, 0)).toBe(false)
  })

  it('is true once there are cells and a cell size', () => {
    expect(isGridReady({ cols: 1, rows: 1 }, 20)).toBe(true)
  })
})

describe('cellsToPixels', () => {
  const spec = { offsetLeft: 5, offsetTop: 7 }

  it('scales by the cell size and adds the grid offset', () => {
    expect(cellsToPixels({ x: 2, y: 3, width: 4, height: 5 }, spec, 20)).toEqual({
      left: 45,
      top: 67,
      width: 80,
      height: 100,
    })
  })

  it('puts the origin cell exactly at the grid offset', () => {
    expect(cellsToPixels({ x: 0, y: 0, width: 1, height: 1 }, spec, 20)).toMatchObject({
      left: 5,
      top: 7,
    })
  })
})

describe('pixelToCell', () => {
  const spec = { offsetLeft: 10, offsetTop: 20 }

  it('subtracts the container position and the grid offset', () => {
    expect(
      pixelToCell({
        clientX: 130,
        clientY: 140,
        containerRect: { left: 30, top: 40 },
        gridSpec: spec,
        cellSize: 20,
      }),
    ).toEqual({ cellX: 4.5, cellY: 4 })
  })

  it('adds the container scroll, so a scrolled desktop maps the pointer correctly', () => {
    expect(
      pixelToCell({
        clientX: 130,
        clientY: 140,
        containerRect: { left: 30, top: 40 },
        scrollLeft: 100,
        scrollTop: 200,
        gridSpec: spec,
        cellSize: 20,
      }),
    ).toEqual({ cellX: 9.5, cellY: 14 })
  })

  it('returns a fractional cell, so the caller can centre before it rounds', () => {
    const cell = pixelToCell({
      clientX: 41,
      clientY: 61,
      containerRect: { left: 0, top: 0 },
      gridSpec: { offsetLeft: 0, offsetTop: 0 },
      cellSize: 20,
    })

    expect(cell).toEqual({ cellX: 2.05, cellY: 3.05 })
  })

  it('goes negative for a pointer left of or above the grid, rather than clamping', () => {
    // Clamping here would make a drag that leaves the grid look like a drag pinned to its
    // edge; the caller decides what an out-of-grid pointer means.
    expect(
      pixelToCell({
        clientX: 0,
        clientY: 0,
        containerRect: { left: 30, top: 40 },
        gridSpec: spec,
        cellSize: 20,
      }),
    ).toEqual({ cellX: -2, cellY: -3 })
  })
})
