import { describe, expect, it } from 'vitest'

import { chartLayout } from './layout'
import { buildScales, describeSeries, formatPrice, linePath } from './scales'
import { toSeries } from './series'

/**
 * ANV-34 — scales, ticks, the path string and the chart's text alternative.
 *
 * **These tests prove real behaviour.** Nothing here renders. `buildScales` maps a series
 * and a box to two functions, and the assertions are about the numbers those functions
 * return — which is exactly the part of a d3 chart that jsdom *can* corroborate, because it
 * involves no measurement.
 *
 * The sparkline mode is used for the arithmetic assertions on purpose: `full` applies
 * `.nice()`, which rounds the price domain outward to the tick values and is therefore the
 * wrong place to assert an exact domain.
 */

/** Two candles half an hour apart, priced so their string ordering is the wrong way round. */
const twoPoints = toSeries([
  {
    datetime: '2026-01-05T09:30:00',
    open_price: '9.5',
    high_price: '9.5',
    low_price: '9.5',
    close_price: '9.5',
    volume: 1,
  },
  {
    datetime: '2026-01-05T10:00:00',
    open_price: '10.2',
    high_price: '10.2',
    low_price: '10.2',
    close_price: '10.2',
    volume: 1,
  },
])

/** 100×50 in sparkline mode is a 96×46 plot area — the margins are 2 on every side. */
const sparklineLayout = chartLayout({ width: 100, height: 50 })
const fullLayout = chartLayout({ width: 640, height: 320 })

describe('buildScales — when there is nothing to scale', () => {
  it.each([
    ['an unmeasured box', chartLayout({ width: 0, height: 0 })],
    ['a value-mode box', chartLayout({ width: 40, height: 40 })],
    ['no layout at all', null],
  ])('returns null for %s', (_label, layout) => {
    expect(buildScales(twoPoints, layout)).toBeNull()
  })

  it('returns null for an empty series', () => {
    expect(buildScales([], sparklineLayout)).toBeNull()
  })
})

describe('buildScales — the price scale', () => {
  it('maps the range onto the plot area, inverted so low prices sit low', () => {
    const { y } = buildScales(twoPoints, sparklineLayout)
    expect(y.range()).toEqual([46, 0])
  })

  it('orders the domain numerically — the assertion a missing Number() fails', () => {
    // As strings, `"10.2" < "9.5"`, so an unconverted series produces a reversed domain and
    // a chart that draws the cheap candle at the top.
    const { y } = buildScales(twoPoints, sparklineLayout)

    expect(y.domain()).toEqual([9.5, 10.2])
    expect(y(9.5)).toBe(46)
    expect(y(10.2)).toBe(0)
    // The property that survives a rescale: a lower price is further down the SVG.
    expect(y(9.5)).toBeGreaterThan(y(10.2))
  })

  it('rounds the domain outward in full mode only', () => {
    // Closes chosen so `.nice()` actually moves the domain. With 9.5/10.2 it is a no-op —
    // both are already tick values — so a mutation that niced the sparkline too would pass
    // unnoticed, which is exactly what the first mutation run found.
    const awkward = toSeries([
      { datetime: '2026-01-05T09:30:00', close_price: '9.53' },
      { datetime: '2026-01-05T10:00:00', close_price: '10.21' },
    ])

    const spark = buildScales(awkward, sparklineLayout)
    const full = buildScales(awkward, fullLayout)

    expect(spark.y.domain()).toEqual([9.53, 10.21])
    // `.nice()` widens to the tick values, so the labels land on round numbers.
    expect(full.y.domain()).toEqual([9.5, 10.3])
  })

  it('puts a flat series through the middle rather than inventing a range', () => {
    const flat = toSeries([
      { datetime: '2026-01-05T09:30:00', close_price: '7.00' },
      { datetime: '2026-01-05T10:00:00', close_price: '7.00' },
    ])
    const { y } = buildScales(flat, sparklineLayout)
    expect(y(7)).toBe(23)
  })
})

describe('buildScales — the time scale', () => {
  it('spans the plot area from the first candle to the last', () => {
    const { x } = buildScales(twoPoints, sparklineLayout)
    expect(x.range()).toEqual([0, 96])
    expect(x(twoPoints[0].t)).toBe(0)
    expect(x(twoPoints[1].t)).toBe(96)
  })

  it('formats a tick in the exchange wall clock the API sent, not the runner clock', () => {
    // `scaleUtc`, not `scaleTime`: the epochs are nominal (see series.js), so reading them
    // back with local getters would shift every label by the viewer's offset.
    const { x, formatX } = buildScales(twoPoints, fullLayout)
    const labels = x.ticks(4).map(formatX)
    expect(labels.join(' ')).toMatch(/09:3|10:0|AM|Jan/)
    expect(formatX(new Date(twoPoints[0].t))).not.toMatch(/GMT|UTC/)
  })

  it('labels a tick in the wall clock even when the runner is not on UTC', () => {
    // The mutation this kills is one character long: `scaleTime` instead of `scaleUtc`.
    // Both are identical on a UTC machine — which is what CI is — so nothing else in this
    // suite can tell them apart. On any other machine `scaleTime` formats the nominal epoch
    // through the *viewer's* zone and every label moves.
    const originalTz = process.env.TZ
    try {
      process.env.TZ = 'America/New_York'
      const { formatX } = buildScales(twoPoints, fullLayout)
      expect(formatX(new Date(twoPoints[0].t))).toBe('09:30')
    } finally {
      process.env.TZ = originalTz
    }
  })

  it('centres a single candle rather than pinning it to an edge', () => {
    const one = toSeries([{ datetime: '2026-01-05T09:30:00', close_price: '5.00' }])
    const { x } = buildScales(one, sparklineLayout)
    expect(x(one[0].t)).toBe(48)
  })
})

describe('buildScales — ticks', () => {
  it('produces ticks in full mode', () => {
    const { xTicks, yTicks } = buildScales(twoPoints, fullLayout)
    expect(xTicks.length).toBeGreaterThan(1)
    expect(yTicks.length).toBeGreaterThan(1)
  })

  it('produces none in sparkline mode, where there is no room to label them', () => {
    const { xTicks, yTicks } = buildScales(twoPoints, sparklineLayout)
    expect(xTicks).toEqual([])
    expect(yTicks).toEqual([])
  })
})

describe('linePath', () => {
  it('draws straight segments between candles', () => {
    const { x, y } = buildScales(twoPoints, sparklineLayout)
    expect(linePath(twoPoints, x, y)).toBe('M0,46L96,0')
  })

  it('rounds to two decimals so the attribute is readable', () => {
    const three = toSeries([
      { datetime: '2026-01-05T09:30:00', close_price: '1' },
      { datetime: '2026-01-05T09:40:00', close_price: '2' },
      { datetime: '2026-01-05T10:00:00', close_price: '3' },
    ])
    const { x, y } = buildScales(three, sparklineLayout)
    expect(linePath(three, x, y)).toBe('M0,46L32,23L96,0')
  })

  it('rounds a sub-pixel coordinate to two decimals', () => {
    // Deliberately awkward spacing, so the coordinates do not land on integers: dropping
    // the rounding leaves `32.053333333333335` in the `d` attribute.
    const uneven = toSeries([
      { datetime: '2026-01-05T09:30:00', close_price: '1' },
      { datetime: '2026-01-05T09:40:01', close_price: '1.7' },
      { datetime: '2026-01-05T10:00:00', close_price: '3' },
    ])
    const { x, y } = buildScales(uneven, sparklineLayout)
    expect(linePath(uneven, x, y)).toBe('M0,46L32.05,29.9L96,0')
  })

  it.each([
    ['an empty series', []],
    ['a non-array', null],
  ])('is an empty string for %s — never a malformed `d`', (_label, series) => {
    expect(linePath(series, () => 0, () => 0)).toBe('')
  })
})

describe('formatPrice', () => {
  it.each([
    [1234.5678, '1234.5678'],
    [10.2, '10.20'],
    [1000, '1000.00'],
    [0, '0.00'],
    [-0.5, '-0.50'],
    [0.123456, '0.1235'],
  ])('formats %s as %s', (value, expected) => {
    expect(formatPrice(value)).toBe(expected)
  })

  it('keeps the fourth decimal the quoted-string transport exists to preserve', () => {
    expect(formatPrice(1234.5678)).toContain('5678')
  })

  it.each([
    ['NaN', Number.NaN],
    ['Infinity', Number.POSITIVE_INFINITY],
    ['a string', '10.2'],
    ['null', null],
    ['undefined', undefined],
  ])('renders an em dash for %s', (_label, value) => {
    expect(formatPrice(value)).toBe('—')
  })

  it('honours an explicit precision', () => {
    expect(formatPrice(1234.5678, { minDecimals: 2, maxDecimals: 2 })).toBe('1234.57')
  })
})

describe('describeSeries — the text alternative', () => {
  it('names the security, the span and the range', () => {
    expect(describeSeries(twoPoints, { label: 'AAPL' })).toBe(
      'AAPL: 2 points from 2026-01-05T09:30:00 to 2026-01-05T10:00:00. ' +
        'Close ranged 9.50 to 10.20, ending at 10.20.',
    )
  })

  it('quotes the timestamps the API sent, with no zone attached', () => {
    expect(describeSeries(twoPoints)).toContain('2026-01-05T09:30:00')
    expect(describeSeries(twoPoints)).not.toMatch(/GMT|UTC|Z\b/)
  })

  it('says so when there is nothing to describe', () => {
    expect(describeSeries([], { label: 'AAPL' })).toBe('AAPL: no price data.')
    expect(describeSeries(null)).toBe('Price: no price data.')
  })

  it('does not say "1 points"', () => {
    const one = toSeries([{ datetime: '2026-01-05T09:30:00', close_price: '5.00' }])
    expect(describeSeries(one)).toContain('1 point from')
  })
})
