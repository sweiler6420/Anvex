import { describe, expect, it } from 'vitest'

import { StockChartWidget, WIDGET_PALETTE } from '@features/widgets'

import { stockChartWindow } from './windowTemplates'

/**
 * The per-security window template (ANV-36).
 *
 * **Real behaviour, no DOM.** A React element is an immutable descriptor, so everything
 * below is an assertion about a plain object: which component the window will mount, and
 * with which props. Nothing is rendered and nothing is measured.
 *
 * The test that matters is the second one. The palette's chart row carries
 * `<StockChartWidget />` with **no props**, i.e. the widget's `ticker = 'AAPL'` default —
 * so a template built by spreading the row and forgetting to replace `content` produces a
 * window that looks right in every other respect and charts Apple whichever security was
 * picked. That failure is silent on screen: the header says NVDA and the line is AAPL's.
 */

const SECURITY = { stockId: '33333333-3333-4333-8333-333333333333', ticker: 'NVDA' }

describe('stockChartWindow', () => {
  it('mounts a chart for the security it was given, not the palette s default', () => {
    const { content } = stockChartWindow(SECURITY).window

    expect(content.type).toBe(StockChartWidget)
    expect(content.props.stockId).toBe(SECURITY.stockId)
    expect(content.props.ticker).toBe(SECURITY.ticker)
  })

  it('is not the palette s element, which charts AAPL by default', () => {
    // The discriminating half of the test above: without it, an implementation that
    // returned the palette row untouched would still satisfy `content.type`.
    const paletteRow = WIDGET_PALETTE.find((item) => item.window.content?.type === StockChartWidget)

    expect(paletteRow.window.content.props.ticker).toBeUndefined()
    expect(stockChartWindow(SECURITY).window.content).not.toBe(paletteRow.window.content)
  })

  it('keeps the palette s geometry and chrome, so the two windows are the same window', () => {
    const paletteRow = WIDGET_PALETTE.find((item) => item.window.content?.type === StockChartWidget)
    const { window } = stockChartWindow(SECURITY)

    expect(window.color).toBe(paletteRow.window.color)
    expect(window.width).toBe(paletteRow.window.width)
    expect(window.height).toBe(paletteRow.window.height)
    expect(window.minWidth).toBe(paletteRow.window.minWidth)
    expect(window.minHeight).toBe(paletteRow.window.minHeight)
  })

  it('names the security in the window title and in the announcement', () => {
    const item = stockChartWindow(SECURITY)

    // `name` is what `openWindow` reads into the live region; `title` is what
    // `DesktopWindow` puts in the header. Both have to say which security, because a
    // desktop with three charts on it is otherwise three windows called "Price chart".
    expect(item.name).toBe('NVDA price chart')
    expect(item.window.title).toBe('NVDA price')
  })
})
