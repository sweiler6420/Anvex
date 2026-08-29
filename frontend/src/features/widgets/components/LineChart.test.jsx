import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { toSeries } from '../chart/series'
import LineChart from './LineChart'

/**
 * ANV-34 — the chart.
 *
 * ## Which of these prove behaviour and which prove wiring
 *
 * `LineChart` measures nothing: `width` and `height` are props, so **the size is fabricated
 * by the test, in the test, on the line above the assertion it supports** (ANV-33's rule for
 * `useContainerSize`). What that buys is that everything downstream of the size is *real* —
 * the mode, the scales, the `d` attribute and the text alternative are the numbers the
 * component actually computed, and they are the same numbers a browser would compute for a
 * panel of that size.
 *
 * So: **REAL** — the mode boundaries, the path string, the accessible description, and the
 * unmeasured case (which needs no fabrication at all, being what jsdom genuinely reports).
 * **WIRING ONLY** — nothing here shows that a real panel is 640×320, or that the SVG is
 * visible, or that the line is where the eye would put it. jsdom has no box model and cannot
 * contradict any of that.
 */

const series = toSeries([
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

describe('LineChart — unmeasured (REAL: this is what jsdom reports)', () => {
  it('renders nothing measurable before anything has a size', () => {
    render(<LineChart series={series} />)

    expect(screen.getByTestId('line-chart')).toHaveAttribute('data-mode', 'unmeasured')
    expect(screen.queryByTestId('price-line')).not.toBeInTheDocument()
  })
})

describe('LineChart — a 2×2 window (40×40 px)', () => {
  it('falls back to the latest price instead of a clipped 640 px canvas', () => {
    render(<LineChart series={series} width={40} height={40} />)

    const chart = screen.getByTestId('line-chart')
    expect(chart).toHaveAttribute('data-mode', 'value')
    expect(chart).toHaveTextContent('10.20')
    expect(chart.querySelector('svg')).toBeNull()
  })

  it('still carries the whole description for a reader', () => {
    render(<LineChart series={series} width={40} height={40} label="AAPL" />)
    expect(screen.getByText(/AAPL: 2 points from/)).toBeInTheDocument()
  })
})

describe('LineChart — sparkline', () => {
  it('draws the line and no axes', () => {
    render(<LineChart series={series} width={100} height={50} />)

    expect(screen.getByTestId('line-chart')).toHaveAttribute('data-mode', 'sparkline')
    expect(screen.queryAllByTestId('y-tick')).toHaveLength(0)
    expect(screen.queryAllByTestId('x-tick')).toHaveLength(0)
  })

  it('puts the cheaper candle lower on the canvas', () => {
    // 100×50 is a 96×46 plot area. The first close is 9.5 and the second 10.2, so the path
    // must start at the bottom and end at the top. An unconverted (string) series inverts
    // this and still draws.
    render(<LineChart series={series} width={100} height={50} />)
    expect(screen.getByTestId('price-line')).toHaveAttribute('d', 'M0,46L96,0')
  })
})

describe('LineChart — full', () => {
  it('renders axes, ticks and the line', () => {
    render(<LineChart series={series} width={640} height={320} />)

    expect(screen.getByTestId('line-chart')).toHaveAttribute('data-mode', 'full')
    expect(screen.getAllByTestId('y-tick').length).toBeGreaterThan(1)
    expect(screen.getAllByTestId('x-tick').length).toBeGreaterThan(1)
    expect(screen.getByTestId('price-line').getAttribute('d')).toMatch(/^M[\d.]+,[\d.]+L/)
  })

  it('labels the time axis in the exchange wall clock, with no zone attached', () => {
    render(<LineChart series={series} width={640} height={320} />)

    const labels = screen.getAllByTestId('x-tick').map((t) => t.textContent)
    expect(labels.join(' ')).not.toMatch(/GMT|UTC/)
  })

  it('is an image with a name, not an unlabelled bag of paths', () => {
    render(<LineChart series={series} width={640} height={320} label="NVDA" />)

    const svg = screen.getByRole('img', {
      name: /NVDA: 2 points from 2026-01-05T09:30:00 to 2026-01-05T10:00:00\./,
    })
    expect(svg.tagName.toLowerCase()).toBe('svg')
  })

  it('renders each tick once — the original appended a new axis on every data change', () => {
    const { rerender } = render(<LineChart series={series} width={640} height={320} />)
    const before = screen.getAllByTestId('y-tick').length

    rerender(<LineChart series={series} width={640} height={320} />)

    expect(screen.getAllByTestId('y-tick')).toHaveLength(before)
  })
})

describe('LineChart — nothing to draw', () => {
  it('says so rather than rendering an empty canvas', () => {
    render(<LineChart series={[]} width={640} height={320} />)

    expect(screen.getByTestId('line-chart')).toHaveAttribute('data-mode', 'empty')
    expect(screen.getByText('No price data for this range.')).toBeInTheDocument()
  })

  it('uses the message it was given', () => {
    render(<LineChart series={[]} width={640} height={320} emptyMessage="Market closed." />)
    expect(screen.getByText('Market closed.')).toBeInTheDocument()
  })
})
