import { render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { isValidElement } from 'react'
import { beforeEach, describe, expect, it } from 'vitest'

import { apiUrl } from '@lib/env'
import { pageResponse } from '@test/msw/handlers'
import { server } from '@test/msw/server'

import { WIDGET_PALETTE } from './palette'
import { PUBLIC_WIDGET_PALETTE } from './publicPalette'

/**
 * ANV-34 — the palette ANV-35 drags onto the desktop.
 *
 * ## Which of these prove behaviour and which prove wiring
 *
 * The **shape** assertions are real: the palette is data, and a row that promises a minimum
 * size or a name it does not have is a defect a test can see.
 *
 * The **rendering** sweep is wiring, and its limits are worth stating. jsdom has no box
 * model, so putting a widget in a 40×40 `<div>` does not make it 40×40 and nothing here can
 * observe an overflow. What it does prove is that every widget in the palette **mounts with
 * no props at all** and produces its named region — which is exactly the failure mode of a
 * widget whose defaults are wrong, and which is unreachable from a per-component test that
 * always passes props. The chart's `value`-mode fallback is proved for real in
 * `components/LineChart.test.jsx`, where the size is a number rather than a CSS declaration.
 */

const WATCHLIST_ID = '22222222-2222-4222-8222-222222222222'

beforeEach(() => {
  // The two widgets that fetch do so on mount; the palette's entries carry no props, so
  // these are the defaults they reach for.
  server.use(
    http.get(apiUrl('/v1/stocks/by-ticker/AAPL/data'), () => pageResponse([], { limit: 200 })),
    http.get(apiUrl('/v1/watchlists'), () =>
      pageResponse([{ watchlist_id: WATCHLIST_ID, user_id: 'u', title: 'Semis' }]),
    ),
    http.get(apiUrl(`/v1/watchlists/${WATCHLIST_ID}`), () =>
      HttpResponse.json({
        watchlist_id: WATCHLIST_ID,
        user_id: 'u',
        title: 'Semis',
        entries: [],
      }),
    ),
  )
})

describe('WIDGET_PALETTE — shape', () => {
  it('is non-empty, so nothing below can pass vacuously', () => {
    expect(WIDGET_PALETTE.length).toBeGreaterThan(0)
  })

  it('names each entry uniquely — `WindowMenu` keys its list on the name', () => {
    const names = WIDGET_PALETTE.map((item) => item.name)
    expect(new Set(names).size).toBe(names.length)
  })

  it.each(WIDGET_PALETTE.map((item) => [item.name, item]))(
    '%s carries a complete window template',
    (_name, item) => {
      expect(item.color).toMatch(/^#[0-9a-f]{6}$/i)
      expect(item.window).toMatchObject({
        title: expect.any(String),
        color: item.color,
        width: expect.any(Number),
        height: expect.any(Number),
      })
      expect(isValidElement(item.window.content)).toBe(true)
    },
  )

  it.each(WIDGET_PALETTE.map((item) => [item.name, item]))(
    '%s promises it survives a 2×2 window',
    (_name, item) => {
      // ANV-33 fixed 2×2 cells — 40×40 px — as the size a widget must survive, and
      // `minWidth`/`minHeight` are what the collapse control shrinks to. Advertising a
      // larger minimum would be hiding the constraint rather than meeting it.
      expect(item.window.minWidth).toBe(2)
      expect(item.window.minHeight).toBe(2)
    },
  )

  it('starts every widget larger than its minimum', () => {
    for (const { window: w } of WIDGET_PALETTE) {
      expect(w.width).toBeGreaterThan(w.minWidth)
      expect(w.height).toBeGreaterThan(w.minHeight)
    }
  })
})

describe('WIDGET_PALETTE — every entry mounts with no props (WIRING)', () => {
  it.each(WIDGET_PALETTE.map((item) => [item.name, item]))(
    '%s renders a named region in a 2×2 box',
    async (_name, item) => {
      render(
        <div style={{ width: 40, height: 40, overflow: 'auto' }}>{item.window.content}</div>,
      )

      // Every widget's frame is a named region; a widget that threw on mount, or that
      // needed a prop the palette does not pass, has none.
      const regions = await screen.findAllByRole('region')
      expect(regions).toHaveLength(1)
      expect(regions[0].getAttribute('aria-label')).toBeTruthy()
    },
  )
})

describe('the public subset (ANV-35) — what the marketing demo may offer', () => {
  it('makes every row declare whether it touches the network', () => {
    // The flag is *required*, not optional-with-a-default. A row that forgets it is excluded
    // from the public palette by the strict `=== false` — safe — and fails right here, so
    // "nobody thought about it" is never silent.
    for (const item of WIDGET_PALETTE) {
      expect(typeof item.network, `${item.name} does not declare \`network\``).toBe('boolean')
    }
  })

  it('keeps only the rows that say false', () => {
    expect(PUBLIC_WIDGET_PALETTE).toEqual(
      WIDGET_PALETTE.filter((item) => item.network === false),
    )
  })

  it('is a proper subset, so "the public demo is pure" is a claim and not a tautology', () => {
    expect(PUBLIC_WIDGET_PALETTE.length).toBeGreaterThan(0)
    expect(PUBLIC_WIDGET_PALETTE.length).toBeLessThan(WIDGET_PALETTE.length)
  })

  it.each(PUBLIC_WIDGET_PALETTE.map((item) => [item.name, item]))(
    '%s really does mount without issuing a request',
    async (_name, item) => {
      // The flag is a claim somebody typed; this is the check on it. `beforeEach` above
      // installs handlers for the two fetching widgets, so a mis-flagged row would be
      // answered rather than erroring — which is exactly why this counts requests instead of
      // relying on `onUnhandledRequest: 'error'`.
      const seen = []
      const record = ({ request }) => seen.push(request.url)
      server.events.on('request:start', record)

      try {
        render(<div>{item.window.content}</div>)
        await screen.findByRole('region')
        expect(seen).toEqual([])
      } finally {
        server.events.removeAllListeners('request:start')
      }
    },
  )

  it.each(WIDGET_PALETTE.filter((item) => item.network).map((item) => [item.name, item]))(
    '%s really does issue one — the flag is not decoration',
    async (_name, item) => {
      const seen = []
      const record = ({ request }) => seen.push(request.url)
      server.events.on('request:start', record)

      try {
        render(<div>{item.window.content}</div>)
        await screen.findByRole('region')
        await waitFor(() => expect(seen.length).toBeGreaterThan(0))
      } finally {
        server.events.removeAllListeners('request:start')
      }
    },
  )
})
