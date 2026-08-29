import { describe, expect, it } from 'vitest'

import {
  chartLayout,
  FULL_MARGIN,
  MIN_FULL_HEIGHT,
  MIN_FULL_WIDTH,
  MIN_SPARKLINE_HEIGHT,
  MIN_SPARKLINE_WIDTH,
  SPARKLINE_MARGIN,
  tickCount,
} from './layout'

/**
 * ANV-34 — how much chart fits in a box.
 *
 * **These tests prove real behaviour.** `chartLayout` is a function from two numbers to a
 * mode and four more numbers; nothing here renders, measures or mounts anything, and the
 * assertions would read identically in Node.
 *
 * The boundaries are asserted from the exported constants rather than from literals, so
 * moving a threshold cannot leave a test asserting the old one. The 40×40 case is a literal
 * on purpose: it is ANV-33's 2×2 window at the default `cellSize`, and it is the size the
 * whole mode system exists for.
 */

describe('chartLayout — unmeasured', () => {
  it.each([
    ['0×0, which is what jsdom and a hidden element report', { width: 0, height: 0 }],
    ['zero width', { width: 0, height: 400 }],
    ['zero height', { width: 400, height: 0 }],
    ['a negative dimension', { width: -10, height: 100 }],
    ['NaN', { width: Number.NaN, height: 100 }],
    ['Infinity', { width: Number.POSITIVE_INFINITY, height: 100 }],
    ['no argument at all', undefined],
  ])('is unmeasured for %s', (_label, size) => {
    expect(chartLayout(size).mode).toBe('unmeasured')
  })

  it('reports a zero plot area rather than a negative one', () => {
    expect(chartLayout({ width: 0, height: 0 })).toMatchObject({
      innerWidth: 0,
      innerHeight: 0,
    })
  })
})

describe('chartLayout — value mode', () => {
  it('is the mode for a 2×2 window (40×40 px at the default cellSize)', () => {
    expect(chartLayout({ width: 40, height: 40 }).mode).toBe('value')
  })

  it.each([
    ['one pixel below the sparkline width', MIN_SPARKLINE_WIDTH - 1, MIN_SPARKLINE_HEIGHT],
    ['one pixel below the sparkline height', MIN_SPARKLINE_WIDTH, MIN_SPARKLINE_HEIGHT - 1],
  ])('is the mode %s', (_label, width, height) => {
    expect(chartLayout({ width, height }).mode).toBe('value')
  })

  it('keeps the box it was given, so a caller can still lay something out', () => {
    expect(chartLayout({ width: 40, height: 40 })).toMatchObject({ width: 40, height: 40 })
  })
})

describe('chartLayout — sparkline mode', () => {
  it('starts exactly at the sparkline minimums', () => {
    const layout = chartLayout({ width: MIN_SPARKLINE_WIDTH, height: MIN_SPARKLINE_HEIGHT })
    expect(layout.mode).toBe('sparkline')
    expect(layout.margin).toEqual(SPARKLINE_MARGIN)
  })

  it.each([
    ['too narrow for axes', MIN_FULL_WIDTH - 1, MIN_FULL_HEIGHT],
    ['too short for axes', MIN_FULL_WIDTH, MIN_FULL_HEIGHT - 1],
  ])('is the mode when the box is %s', (_label, width, height) => {
    expect(chartLayout({ width, height }).mode).toBe('sparkline')
  })

  it('subtracts its own padding from the plot area', () => {
    const layout = chartLayout({ width: 100, height: 50 })
    expect(layout.innerWidth).toBe(100 - SPARKLINE_MARGIN.left - SPARKLINE_MARGIN.right)
    expect(layout.innerHeight).toBe(50 - SPARKLINE_MARGIN.top - SPARKLINE_MARGIN.bottom)
  })
})

describe('chartLayout — full mode', () => {
  it('starts exactly at the full minimums', () => {
    const layout = chartLayout({ width: MIN_FULL_WIDTH, height: MIN_FULL_HEIGHT })
    expect(layout.mode).toBe('full')
    expect(layout.margin).toEqual(FULL_MARGIN)
  })

  it('subtracts the axis margins from the plot area', () => {
    const layout = chartLayout({ width: 640, height: 320 })
    expect(layout.innerWidth).toBe(640 - FULL_MARGIN.left - FULL_MARGIN.right)
    expect(layout.innerHeight).toBe(320 - FULL_MARGIN.top - FULL_MARGIN.bottom)
  })

  it('never produces a negative plot area at its own threshold', () => {
    const layout = chartLayout({ width: MIN_FULL_WIDTH, height: MIN_FULL_HEIGHT })
    expect(layout.innerWidth).toBeGreaterThan(0)
    expect(layout.innerHeight).toBeGreaterThan(0)
  })
})

describe('chartLayout — the modes are ordered by size', () => {
  it('never skips a mode as the box grows', () => {
    const seen = [10, 40, 80, 200, 400, 900].map(
      (n) => chartLayout({ width: n, height: n / 2 }).mode,
    )
    const rank = { unmeasured: 0, value: 1, sparkline: 2, full: 3 }
    const ranks = seen.map((mode) => rank[mode])
    expect(ranks).toEqual([...ranks].sort((a, b) => a - b))
  })
})

describe('tickCount', () => {
  it('asks for roughly one tick per label-width of axis', () => {
    expect(tickCount(700, 70)).toBe(10)
    expect(tickCount(345, 70)).toBe(4)
  })

  it('never asks for fewer than two', () => {
    expect(tickCount(10, 70)).toBe(2)
    expect(tickCount(0, 70)).toBe(2)
  })

  it.each([
    ['a non-finite length', Number.NaN, 70],
    ['a zero spacing', 700, 0],
    ['a negative spacing', 700, -10],
  ])('falls back to two for %s', (_label, length, per) => {
    expect(tickCount(length, per)).toBe(2)
  })
})
