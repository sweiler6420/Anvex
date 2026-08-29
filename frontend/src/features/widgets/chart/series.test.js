import { afterEach, describe, expect, it } from 'vitest'

import { closeExtent, parseNominalEpoch, timeExtent, toNumber, toSeries } from './series'

/**
 * ANV-34 — the port of `utils.js`.
 *
 * **These tests prove real behaviour.** There is no DOM in this file and no React: every
 * function under test maps strings to numbers, so jsdom's missing layout is not a limitation
 * here, it is irrelevant. Every assertion would read identically in Node.
 *
 * Two of them are the reason the file exists, and both catch a mistake that produces a
 * *plausible wrong chart* rather than an error:
 *
 *  - `"9.5"` versus `"10.2"` — as strings, `"10.2" < "9.5"`, so a missing `Number()` gives a
 *    confident, inverted price domain.
 *  - the nominal-epoch tests — a naive timestamp read as local time still *labels* correctly
 *    on a UTC machine, and shifts every candle everywhere else.
 */

describe('toNumber — the quoted-string price conversion', () => {
  it('converts the quoted decimal the API actually sends', () => {
    expect(toNumber('1234.5678')).toBe(1234.5678)
  })

  it('keeps a plain finite number', () => {
    expect(toNumber(12.5)).toBe(12.5)
    expect(toNumber(0)).toBe(0)
  })

  it('tolerates surrounding whitespace', () => {
    expect(toNumber('  42.25 ')).toBe(42.25)
  })

  it.each([
    ['an empty string', ''],
    ['whitespace only', '   '],
    ['a non-numeric string', 'AAPL'],
    ['a partially numeric string', '12.5abc'],
    ['null', null],
    ['undefined', undefined],
    ['an object', {}],
    ['an array', []],
    ['NaN', Number.NaN],
    ['Infinity', Number.POSITIVE_INFINITY],
  ])('rejects %s', (_label, value) => {
    expect(toNumber(value)).toBeNull()
  })

  it('rejects blank input rather than letting Number() call it zero', () => {
    // The trap this guard exists for: both of these are 0 to `Number`, and a chart that
    // plots a silent zero for a missing price is lying in the same way an inverted domain is.
    expect(Number('')).toBe(0)
    expect(Number('   ')).toBe(0)
    expect(toNumber('')).toBeNull()
  })
})

describe('parseNominalEpoch — the naive datetime', () => {
  it('reads the digits back through UTC, exactly', () => {
    // A literal, not `Date.UTC(...)` restated: the point is that the number means those
    // digits and no others.
    expect(new Date(parseNominalEpoch('2026-01-05T09:30:00')).toISOString()).toBe(
      '2026-01-05T09:30:00.000Z',
    )
  })

  it('spaces two candles by their wall-clock difference', () => {
    const open = parseNominalEpoch('2026-01-05T09:30:00')
    const later = parseNominalEpoch('2026-01-05T10:00:00')
    expect(later - open).toBe(30 * 60 * 1000)
  })

  it('accepts a space separator and an omitted seconds field', () => {
    expect(parseNominalEpoch('2026-01-05 09:30')).toBe(parseNominalEpoch('2026-01-05T09:30:00'))
  })

  it('scales a fractional second by its own width', () => {
    expect(parseNominalEpoch('2026-01-05T09:30:00.5')).toBe(
      parseNominalEpoch('2026-01-05T09:30:00') + 500,
    )
    expect(parseNominalEpoch('2026-01-05T09:30:00.500')).toBe(
      parseNominalEpoch('2026-01-05T09:30:00') + 500,
    )
  })

  it.each([
    ['a zoned instant (Z)', '2026-01-05T09:30:00Z'],
    ['a zoned instant (offset)', '2026-01-05T09:30:00+01:00'],
    ['a date with no time', '2026-01-05'],
    ['a rolled-over day', '2026-02-30T09:30:00'],
    ['month 13', '2026-13-05T09:30:00'],
    ['month 0', '2026-00-05T09:30:00'],
    ['day 0', '2026-01-00T09:30:00'],
    ['hour 24', '2026-01-05T24:00:00'],
    ['minute 60', '2026-01-05T09:60:00'],
    ['second 60', '2026-01-05T09:30:60'],
    ['a two-digit year', '26-01-05T09:30:00'],
    ['nonsense', 'yesterday'],
    ['a number', 1767605400000],
    ['null', null],
  ])('rejects %s', (_label, value) => {
    expect(parseNominalEpoch(value)).toBeNull()
  })

  it('rejects a rolled-over day rather than moving the candle a month', () => {
    // The trap: `Date.UTC` is perfectly happy to answer March for the 30th of February.
    expect(new Date(Date.UTC(2026, 1, 30)).toISOString()).toBe('2026-03-02T00:00:00.000Z')
    expect(parseNominalEpoch('2026-02-30T09:30:00')).toBeNull()
  })
})

describe('parseNominalEpoch — independence from the machine it runs on', () => {
  const originalTz = process.env.TZ

  afterEach(() => {
    process.env.TZ = originalTz
  })

  it('does not move when the runner is not on UTC', () => {
    process.env.TZ = 'America/New_York'
    const naive = '2026-07-05T09:30:00'

    // Guard: the timezone change actually took effect, so the assertion below is not
    // vacuous. `new Date(naive)` is parsed as *local* time by every JS engine — this is the
    // implementation `parseNominalEpoch` exists to avoid.
    const localParse = new Date(naive).getTime()
    expect(localParse).not.toBe(Date.UTC(2026, 6, 5, 9, 30))

    expect(new Date(parseNominalEpoch(naive)).toISOString()).toBe('2026-07-05T09:30:00.000Z')
  })

  it('spaces candles identically across a DST boundary', () => {
    // 2026-03-08 is the US spring-forward date: 02:00 local does not exist, and a local
    // parse spaces 01:00 and 03:00 an hour apart instead of two.
    process.env.TZ = 'America/New_York'

    const localGap = new Date('2026-03-08T03:00:00') - new Date('2026-03-08T01:00:00')
    expect(localGap).toBe(60 * 60 * 1000)

    const nominalGap =
      parseNominalEpoch('2026-03-08T03:00:00') - parseNominalEpoch('2026-03-08T01:00:00')
    expect(nominalGap).toBe(2 * 60 * 60 * 1000)
  })
})

const point = (datetime, close, extra = {}) => ({
  stock_id: 'a1',
  datetime,
  open_price: '1.0000',
  high_price: '2.0000',
  low_price: '0.5000',
  close_price: close,
  volume: 1000,
  ...extra,
})

describe('toSeries', () => {
  it('converts every price and keeps the raw datetime as a label', () => {
    const [row] = toSeries([
      point('2026-01-05T09:30:00', '1234.5678', {
        open_price: '1230.0001',
        high_price: '1240.9999',
        low_price: '1229.0000',
        volume: 4096,
      }),
    ])

    expect(row).toEqual({
      t: parseNominalEpoch('2026-01-05T09:30:00'),
      label: '2026-01-05T09:30:00',
      open: 1230.0001,
      high: 1240.9999,
      low: 1229,
      close: 1234.5678,
      volume: 4096,
    })
    expect(typeof row.close).toBe('number')
  })

  it('sorts ascending by nominal epoch', () => {
    const series = toSeries([
      point('2026-01-05T11:00:00', '3'),
      point('2026-01-05T09:30:00', '1'),
      point('2026-01-05T10:00:00', '2'),
    ])
    expect(series.map((d) => d.close)).toEqual([1, 2, 3])
  })

  it('does not mutate the rows it was given', () => {
    const raw = point('2026-01-05T09:30:00', '10.50')
    const snapshot = { ...raw }
    toSeries([raw])
    expect(raw).toEqual(snapshot)
    expect(typeof raw.close_price).toBe('string')
  })

  it('drops a point with an unparseable close', () => {
    const series = toSeries([
      point('2026-01-05T09:30:00', '10.50'),
      point('2026-01-05T09:35:00', ''),
      point('2026-01-05T09:40:00', null),
    ])
    expect(series.map((d) => d.label)).toEqual(['2026-01-05T09:30:00'])
  })

  it('drops a point with an unparseable datetime', () => {
    const series = toSeries([
      point('2026-01-05T09:30:00', '10.50'),
      point('not a datetime', '11.50'),
      point('2026-01-05T09:30:00Z', '12.50'),
    ])
    expect(series).toHaveLength(1)
  })

  it('keeps a point whose optional prices are missing, with nulls', () => {
    const [row] = toSeries([
      {
        datetime: '2026-01-05T09:30:00',
        close_price: '10.50',
        open_price: null,
        high_price: undefined,
        low_price: 'n/a',
        volume: '',
      },
    ])
    expect(row).toMatchObject({ close: 10.5, open: null, high: null, low: null, volume: null })
  })

  it.each([
    ['a non-array', { items: [] }],
    ['undefined', undefined],
    ['null', null],
  ])('returns an empty series for %s', (_label, value) => {
    expect(toSeries(value)).toEqual([])
  })

  it('skips null and non-object entries', () => {
    expect(toSeries([null, 7, 'x', undefined])).toEqual([])
  })
})

describe('closeExtent — the assertion a missing Number() fails', () => {
  it('orders numerically, not lexicographically', () => {
    // As strings these compare the other way round, and nothing throws:
    expect('10.2' < '9.5').toBe(true)

    const series = toSeries([
      point('2026-01-05T09:30:00', '9.5'),
      point('2026-01-05T09:35:00', '10.2'),
    ])

    expect(closeExtent(series)).toEqual([9.5, 10.2])
  })

  it('handles a single point and a flat series', () => {
    expect(closeExtent(toSeries([point('2026-01-05T09:30:00', '7.25')]))).toEqual([7.25, 7.25])
  })

  it.each([
    ['an empty series', []],
    ['a non-array', null],
  ])('returns null for %s', (_label, value) => {
    expect(closeExtent(value)).toBeNull()
  })
})

describe('timeExtent', () => {
  it('spans the first and last point after sorting', () => {
    const series = toSeries([
      point('2026-01-05T11:00:00', '3'),
      point('2026-01-05T09:30:00', '1'),
    ])
    expect(timeExtent(series)).toEqual([
      parseNominalEpoch('2026-01-05T09:30:00'),
      parseNominalEpoch('2026-01-05T11:00:00'),
    ])
  })

  it('returns null for an empty series', () => {
    expect(timeExtent([])).toBeNull()
    expect(timeExtent(undefined)).toBeNull()
  })
})
