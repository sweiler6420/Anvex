import { WIDGET_PALETTE } from '@features/widgets'

/**
 * `/research`'s opening arrangement (ANV-36).
 *
 * ## It is derived from the palette, not written out again
 *
 * `InteractiveDesktop`'s demo set (`INITIAL_WINDOWS`) is a literal because the marketing
 * page's three windows exist only there. This one is not, for the same reason
 * `publicPalette.js` is not: the palette rows already carry a `window` template — the
 * title, the colour, the comfortable size, the minimum and the `content` element — and a
 * second copy of those numbers here would drift the first time somebody resizes the chart
 * in `palette.jsx`. Re-using `item.window` means the research desktop opens the *same*
 * windows the palette adds, which is also the property the page's tests can assert.
 *
 * ## The filter is `network === true`, and that is the same fact read the other way
 *
 * ANV-35 put `network` on each row so `PUBLIC_WIDGET_PALETTE` could be an opt-in filter of
 * the widgets a logged-out visitor may be offered. The complement is exactly the set that
 * *only* means anything behind a session — the ones that fetch from the API — and those
 * are what a signed-in research surface should already have open. Counter, Info and Echo
 * stay in the palette (`/research` offers the full one) but nobody arrives at a research
 * page wanting a counter.
 *
 * `=== true` rather than a truthiness test, mirroring `publicPalette.js`: a row that never
 * declares the field is `undefined` and belongs in neither list, and `palette.test.jsx`
 * fails the suite for the omission separately.
 *
 * ## The layout
 *
 * A single row, left to right, each window at the palette's own width. There is no
 * arithmetic beyond a running total on purpose — `BinPackingLayout` is a packer, and an
 * arrangement wider or taller than the measured grid is reflowed and scaled down by
 * `reflowScaleByOverlap` rather than overflowing. So this says "side by side, in palette
 * order" and lets the desktop decide what that means on a phone.
 */
export const RESEARCH_WINDOWS = WIDGET_PALETTE.filter((item) => item.network === true).reduce(
  (placed, item) => [
    ...placed,
    {
      ...item.window,
      // Stable, readable, and derived — `research-chart`, `research-watchlist`. Window ids
      // must be unique within a desktop; `nextWindowId()` is for windows created at
      // runtime, and using it here would make the opening arrangement depend on how many
      // desktops a test had already mounted.
      id: `research-${item.name.toLowerCase()}`,
      x: placed.reduce((sum, window) => sum + window.width, 0),
      y: 0,
    },
  ],
  [],
)
