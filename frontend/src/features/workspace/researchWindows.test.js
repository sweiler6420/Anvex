import { describe, expect, it } from 'vitest'

import { PUBLIC_WIDGET_PALETTE, WIDGET_PALETTE } from '@features/widgets'

import { RESEARCH_WINDOWS } from './researchWindows'

/**
 * `/research`'s opening arrangement (ANV-36).
 *
 * **Every test in this file proves real behaviour.** There is no React and no DOM below:
 * `RESEARCH_WINDOWS` is a derivation over an array, so these are claims about data that
 * would read identically in Node. What a *rendered* desktop does with them is
 * `ResearchPage.test.jsx`'s problem, and it needs a fabricated measurement to ask.
 */

describe('what the research desktop opens with', () => {
  it('is exactly the widgets that need a session', () => {
    // Derived on both sides, deliberately. A literal `['Price chart', 'Watchlist']` would
    // keep passing on the day somebody marks the chart `network: false` — which is the
    // mistake the flag exists to catch, in `publicPalette.js` and here alike.
    const expected = WIDGET_PALETTE.filter((item) => item.network === true).map(
      (item) => item.window.title,
    )

    expect(RESEARCH_WINDOWS.map((window) => window.title)).toEqual(expected)
  })

  it('leaves the pure widgets out, so the assertion above is not the whole palette', () => {
    // The discriminating half: if every row were `network: true` the filter would be the
    // identity and "opens on the data widgets" would be true and worthless.
    expect(RESEARCH_WINDOWS.length).toBeGreaterThan(0)
    expect(RESEARCH_WINDOWS.length).toBeLessThan(WIDGET_PALETTE.length)
  })

  it('is the exact complement of the palette the marketing page is offered', () => {
    // The two derivations read one flag in opposite directions; between them they must
    // account for every row. A row that declared neither would vanish from both, which is
    // safe but silent, and this is what makes it loud.
    expect(RESEARCH_WINDOWS.length + PUBLIC_WIDGET_PALETTE.length).toBe(WIDGET_PALETTE.length)
  })

  it('re-uses the palette s own window template rather than restating it', () => {
    // The property that stops the two drifting: change the chart's size in `palette.jsx`
    // and the research desktop opens at the new size, with no edit here.
    const rows = WIDGET_PALETTE.filter((item) => item.network === true)

    RESEARCH_WINDOWS.forEach((window, index) => {
      const template = rows[index].window
      expect(window.width).toBe(template.width)
      expect(window.height).toBe(template.height)
      expect(window.minWidth).toBe(template.minWidth)
      expect(window.minHeight).toBe(template.minHeight)
      expect(window.color).toBe(template.color)
      // The element itself, not a copy — the seam `features/desktop/` documents is a React
      // node held on the window object, and a cloned element would mount a second time.
      expect(window.content).toBe(template.content)
    })
  })
})

describe('the arrangement itself', () => {
  it('gives every window a unique, stable id', () => {
    const ids = RESEARCH_WINDOWS.map((window) => window.id)

    expect(new Set(ids).size).toBe(ids.length)
    // Stable across mounts: `nextWindowId()` would make the ids depend on how many desktops
    // the process had already built, which is a test-order dependency in production code.
    expect(ids.every((id) => id.startsWith('research-'))).toBe(true)
  })

  it('lays the windows out side by side without overlapping', () => {
    // The invariant the whole packer exists to keep, asserted on the input it is handed —
    // an initial arrangement that already overlaps is reflowed on the first frame, which
    // looks like a bug in the desktop rather than in the constant that caused it.
    RESEARCH_WINDOWS.forEach((window, index) => {
      expect(window.y).toBe(0)
      const left = RESEARCH_WINDOWS.slice(0, index)
      expect(window.x).toBe(left.reduce((sum, other) => sum + other.width, 0))
    })
  })
})
