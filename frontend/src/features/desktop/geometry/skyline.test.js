import { describe, expect, it } from 'vitest'

import { seededRandom } from '@test/seededRandom'

import { estimateRowsForMinimums, packSkyline } from './skyline'

/**
 * ANV-33 — the Skyline packer.
 *
 * **These tests prove real behaviour.** There is no DOM anywhere in this file: the packer is
 * a function from a list of sizes and a column count to a list of positions, so jsdom's
 * missing layout is not a limitation here, it is simply irrelevant. Every assertion below
 * would read identically in Node.
 */

const item = (id, width, height) => ({ id, width, height })

/** Every pair of placements, for the no-overlap assertions. */
const pairs = (list) =>
  list.flatMap((a, i) => list.slice(i + 1).map((b) => [a, b]))

const overlaps = (a, b) =>
  !(a.x >= b.x + b.width || a.x + a.width <= b.x || a.y >= b.y + b.height || a.y + a.height <= b.y)

describe('packSkyline — placement', () => {
  it('puts a single item in the top-left corner', () => {
    const { placed, requiredRows } = packSkyline([item('a', 3, 2)], 10)

    expect(placed).toEqual([{ id: 'a', x: 0, y: 0, width: 3, height: 2 }])
    expect(requiredRows).toBe(2)
  })

  it('lays items side by side while the row has room', () => {
    const { placed } = packSkyline([item('a', 3, 2), item('b', 4, 2), item('c', 2, 1)], 10)

    expect(placed.map((p) => [p.x, p.y])).toEqual([
      [0, 0],
      [3, 0],
      [7, 0],
    ])
  })

  it('wraps onto the next row when the current one is full', () => {
    // Two 5-wide items exactly fill a 10-column grid; the third has to go below.
    const { placed, requiredRows } = packSkyline(
      [item('a', 5, 2), item('b', 5, 3), item('c', 4, 1)],
      10,
    )

    expect(placed[2]).toEqual({ id: 'c', x: 0, y: 2, width: 4, height: 1 })
    // `c` sits on top of `a` (height 2), so the skyline's high point is still `b` at 3.
    expect(requiredRows).toBe(3)
  })

  it('rests an item on the tallest column beneath it, never floating over a gap', () => {
    // `a` is 2 tall in columns 0-1; `b` is 1 tall in columns 2-3. A 4-wide item spanning all
    // four columns has to sit at y=2, leaving a hole above `b` that nothing fills.
    const { placed } = packSkyline([item('a', 2, 2), item('b', 2, 1), item('c', 4, 1)], 4)

    expect(placed[2]).toMatchObject({ x: 0, y: 2 })
  })

  it('breaks a tie by choosing the leftmost span', () => {
    // Every column is at height 0, so every span ties; the first is taken.
    const { placed } = packSkyline([item('a', 2, 1)], 8)

    expect(placed[0].x).toBe(0)
  })

  it('prefers a lower span over a nearer one', () => {
    // Columns 0-1 are filled to height 3. A 2-wide item must go to the free right-hand side
    // rather than stacking on the left, because y=0 beats y=3.
    const { placed } = packSkyline([item('tall', 2, 3), item('next', 2, 1)], 6)

    expect(placed[1]).toMatchObject({ x: 2, y: 0 })
  })

  it('is deterministic: the same input packs the same way every time', () => {
    const items = [item('a', 3, 2), item('b', 2, 4), item('c', 5, 1), item('d', 1, 1)]

    expect(packSkyline(items, 7)).toEqual(packSkyline(items, 7))
  })
})

describe('packSkyline — sizes it is handed and sizes it uses', () => {
  it('clamps an item wider than the grid down to the full width', () => {
    const { placed } = packSkyline([item('huge', 40, 2)], 6)

    expect(placed[0]).toEqual({ id: 'huge', x: 0, y: 0, width: 6, height: 2 })
  })

  it('floors a fractional size rather than placing a partial cell', () => {
    const { placed } = packSkyline([item('a', 3.9, 2.9)], 10)

    expect(placed[0]).toMatchObject({ width: 3, height: 2 })
  })

  it('raises a zero or negative size to one cell', () => {
    const { placed } = packSkyline([item('a', 0, 0), item('b', -5, -5)], 10)

    expect(placed[0]).toMatchObject({ width: 1, height: 1 })
    expect(placed[1]).toMatchObject({ width: 1, height: 1 })
  })

  it('stacks everything in a single column when there is only one', () => {
    const { placed, requiredRows } = packSkyline(
      [item('a', 1, 2), item('b', 1, 3), item('c', 1, 1)],
      1,
    )

    expect(placed.map((p) => p.y)).toEqual([0, 2, 5])
    expect(requiredRows).toBe(6)
  })
})

describe('packSkyline — degenerate inputs', () => {
  it('returns an empty pack for no items', () => {
    expect(packSkyline([], 10)).toEqual({ placed: [], requiredRows: 0 })
  })

  it('returns an empty pack rather than throwing for a non-array', () => {
    expect(packSkyline(null, 10)).toEqual({ placed: [], requiredRows: 0 })
    expect(packSkyline(undefined, 10)).toEqual({ placed: [], requiredRows: 0 })
  })

  it('degenerates to zero-width placements for a zero-column grid, and does not hang', () => {
    // Documented, not endorsed: a grid with no columns is a caller error, and this layer is
    // not the one that reports it. What matters is that it terminates and produces finite
    // numbers rather than the `NaN`s the un-guarded original produced for a `NaN` column
    // count.
    const { placed, requiredRows } = packSkyline([item('a', 4, 2)], 0)

    expect(placed[0]).toEqual({ id: 'a', x: 0, y: 0, width: 0, height: 2 })
    expect(requiredRows).toBe(0)
  })

  it('survives a NaN column count', () => {
    const { placed } = packSkyline([item('a', 4, 2)], Number.NaN)

    expect(Number.isFinite(placed[0].y)).toBe(true)
  })
})

describe('packSkyline — the invariants, over many random inputs', () => {
  /**
   * The property the packer exists to guarantee. Seeded, so a failure is reproducible: the
   * seed and the case index name the exact input.
   */
  it('never overlaps two items and never lets one escape the columns', () => {
    const random = seededRandom(20250829)

    for (let run = 0; run < 300; run += 1) {
      const cols = random.int(1, 12)
      const count = random.int(0, 10)
      const items = Array.from({ length: count }, (_, i) =>
        item(`w${i}`, random.int(1, 14), random.int(1, 5)),
      )

      const { placed, requiredRows } = packSkyline(items, cols)

      expect(placed).toHaveLength(count)

      for (const p of placed) {
        expect(p.x, `run ${run}: ${p.id} starts left of the grid`).toBeGreaterThanOrEqual(0)
        expect(p.y, `run ${run}: ${p.id} starts above the grid`).toBeGreaterThanOrEqual(0)
        expect(p.x + p.width, `run ${run}: ${p.id} runs past the last column`).toBeLessThanOrEqual(
          cols,
        )
        expect(p.y + p.height, `run ${run}: ${p.id} runs past requiredRows`).toBeLessThanOrEqual(
          requiredRows,
        )
      }

      for (const [a, b] of pairs(placed)) {
        expect(overlaps(a, b), `run ${run}: ${a.id} overlaps ${b.id}`).toBe(false)
      }
    }
  })

  it('reports requiredRows as exactly the lowest edge of anything it placed', () => {
    const random = seededRandom(7)

    for (let run = 0; run < 200; run += 1) {
      const cols = random.int(1, 10)
      const items = Array.from({ length: random.int(1, 8) }, (_, i) =>
        item(`w${i}`, random.int(1, 6), random.int(1, 4)),
      )

      const { placed, requiredRows } = packSkyline(items, cols)
      const lowestEdge = placed.reduce((m, p) => Math.max(m, p.y + p.height), 0)

      expect(requiredRows, `run ${run}`).toBe(lowestEdge)
    }
  })
})

describe('estimateRowsForMinimums', () => {
  it('packs the minimum sizes rather than the current ones', () => {
    const windows = [
      { id: 'a', width: 8, height: 8, minWidth: 2, minHeight: 1 },
      { id: 'b', width: 8, height: 8, minWidth: 2, minHeight: 1 },
    ]

    // At their real sizes these need two rows of 8; at their minima both fit on one row.
    expect(estimateRowsForMinimums(windows, 4)).toBe(1)
  })

  it('treats a missing minimum as one cell', () => {
    expect(estimateRowsForMinimums([{ id: 'a' }, { id: 'b' }], 1)).toBe(2)
  })

  it('is zero for no windows and for a non-array', () => {
    expect(estimateRowsForMinimums([], 5)).toBe(0)
    expect(estimateRowsForMinimums(null, 5)).toBe(0)
  })
})
