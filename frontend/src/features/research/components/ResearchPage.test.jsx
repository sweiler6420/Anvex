import { RouterProvider, createMemoryHistory } from '@tanstack/react-router'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from '@/App'
import { REFRESH_TOKEN_KEY } from '@features/auth/authStorage'
import { resetWindowIdCounter } from '@features/desktop'
import { PUBLIC_WIDGET_PALETTE, WIDGET_PALETTE } from '@features/widgets'
import { RESEARCH_WINDOWS } from '@features/workspace'
import { REFRESH_PATH, resetTokenStore } from '@lib/api'
import { apiUrl } from '@lib/env'
import { createAppRouter } from '@lib/router'
import { AuthContext } from '@providers/AuthContext'
import { ThemeProvider } from '@providers/ThemeProvider'
import { errorResponse, pageResponse } from '@test/msw/handlers'
import { server } from '@test/msw/server'

import { STOCKS_PATH } from '../api'
import ResearchPage from './ResearchPage'

/**
 * `/research` — the research page (ANV-36).
 *
 * ---------------------------------------------------------------------------------------
 * ## Which of these prove behaviour and which prove wiring
 *
 * **REAL** — the ported markup, the guard, the palette's contents, everything the
 * securities list does, and the whole of the cold-load-with-a-refresh-token block. None of
 * them needs a box model: what the page *says*, what it *offers*, what it *requests* and
 * what the transport does with a 401 are all observable in an unmeasured jsdom exactly as
 * they are in a browser. The cold-load block is the most load-bearing thing in the file and
 * it needs no fabrication at all — which is *because* the securities panel exists. Before
 * it, the only protected request on this page came from a widget inside a window, and jsdom
 * renders no windows, so there was no way to drive the refresh path through the real app.
 *
 * **WIRING (the size is invented)** — anything that asserts a window exists. jsdom has no
 * layout, `useContainerSize` reports 0×0 and `BinPackingLayout` correctly renders an empty
 * container, so those tests pass a `fixedSize`. Following ANV-33's rule the fabrication is a
 * **named prop** threaded through `ResearchPage`, so the invented number sits beside the
 * assertion it supports rather than in a `ResizeObserver` mock two files away. What they
 * establish is that this page hands the palette, the arrangement and the picked security to
 * the desktop and renders what the desktop answered — not that a real panel is 1200 × 800.
 *
 * ---------------------------------------------------------------------------------------
 * ## Two harnesses, and neither is optional
 *
 * `renderAt` mounts the **real router** (ANV-28's helper) and is what the guard tests and
 * the cold-load block need. `renderPage` mounts the component directly, because the
 * measurement seam is a prop and a route renders `<ResearchPage />` with none — ANV-29's
 * "a page ticket that only writes one harness has not tested the two things it is most
 * likely to have got wrong", from the other direction.
 */

/** The fabricated measurement. 1200 × 800 at 20px cells is a 60 × 40 grid. */
const fixedSize = (width, height) => () => ({ width, height })
const size1200x800 = fixedSize(1200, 800)

const WATCHLIST_ID = '22222222-2222-4222-8222-222222222222'

const stock = (ticker, company) => ({
  stock_id: `id-${ticker}`,
  ticker_symbol: ticker,
  company,
  market: 'NASDAQ',
  isin: null,
})

const candle = (datetime, close) => ({
  stock_id: 'id-AAPL',
  datetime,
  open_price: close,
  high_price: close,
  low_price: close,
  close_price: close,
  volume: 7,
})

/**
 * Every endpoint the page can reach, answered. Written as one helper because the three
 * resources are what "the research page is backed by the live API" *means* — a test that
 * mocked one of them and let `onUnhandledRequest: 'error'` catch the others would be
 * asserting the page is incomplete.
 */
function mockResearchApi({ securities = [stock('AAPL', 'Apple Inc.')] } = {}) {
  server.use(
    http.get(apiUrl(STOCKS_PATH), () => pageResponse(securities, { total: securities.length })),
    http.get(apiUrl('/v1/stocks/by-ticker/AAPL/data'), () =>
      pageResponse([candle('2026-01-05T09:30:00', '187.2500')], { limit: 200 }),
    ),
    http.get(apiUrl('/v1/watchlists'), () =>
      pageResponse([{ watchlist_id: WATCHLIST_ID, user_id: 'u', title: 'Semis' }]),
    ),
    http.get(apiUrl(`/v1/watchlists/${WATCHLIST_ID}`), () =>
      HttpResponse.json({
        watchlist_id: WATCHLIST_ID,
        user_id: 'u',
        title: 'Semis',
        entries: [
          {
            watchlist_id: WATCHLIST_ID,
            stock_id: 'id-NVDA',
            position: 0,
            stock: stock('NVDA', 'NVIDIA Corporation'),
          },
        ],
      }),
    ),
  )
}

/** ANV-28's helper: the same `auth` object in the React context and the router context. */
function renderAt(path, { isAuthenticated = true } = {}) {
  const router = createAppRouter({ history: createMemoryHistory({ initialEntries: [path] }) })
  const auth = { isAuthenticated, login: vi.fn(), logout: vi.fn(), restore: vi.fn() }

  render(
    <ThemeProvider>
      <AuthContext.Provider value={auth}>
        <RouterProvider router={router} context={{ auth }} />
      </AuthContext.Provider>
    </ThemeProvider>,
  )

  return { router, location: () => router.state.location }
}

const renderPage = (props) => render(<ResearchPage {...props} />)

const menu = () => within(screen.getByTestId('window-menu'))
const desktopWindows = () =>
  [...document.querySelectorAll('[data-testid^="desktop-window-"]')].map((node) =>
    node.getAttribute('data-testid'),
  )

/** Every request MSW saw during a test, whether or not a handler answered it. */
const requests = []

beforeEach(() => {
  resetWindowIdCounter()
  requests.length = 0
  window.localStorage.clear()
  server.events.on('request:start', ({ request }) => requests.push(request.url))
})

afterEach(() => {
  server.events.removeAllListeners('request:start')
  resetTokenStore()
  window.localStorage.clear()
})

describe('the guard (REAL)', () => {
  it('admits a signed-in user', async () => {
    mockResearchApi()
    const { location } = renderAt('/research')

    expect(await screen.findByTestId('route-research')).toBeInTheDocument()
    expect(location().pathname).toBe('/research')
  })

  it('sends an anonymous visitor to /login carrying where they were going', async () => {
    const { location } = renderAt('/research', { isAuthenticated: false })

    expect(await screen.findByTestId('route-login')).toBeInTheDocument()
    expect(location().search).toEqual({ redirect: '/research' })
    // Not "it ended up at /login": the page's own effects must never have run, which is
    // what `beforeLoad` buys over the old `RequireAuth` element (ANV-27).
    expect(screen.queryByTestId('route-research')).not.toBeInTheDocument()
    expect(requests).toEqual([])
  })
})

describe('the ported page (REAL)', () => {
  it('keeps the old page s headings, in order and at the right levels', async () => {
    mockResearchApi()
    renderPage()

    expect(screen.getByRole('heading', { level: 1, name: 'Research' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'Research Dashboard' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 3, name: 'Stock Analysis' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 3, name: 'Market Research' })).toBeInTheDocument()
    await screen.findByTestId('securities-panel')
  })

  it('keeps the old copy verbatim', async () => {
    // ANV-32: a port that quietly improves the wording is a port nobody can review against
    // the original. These three sentences are the page owner's, typos and all.
    mockResearchApi()
    renderPage()

    expect(
      screen.getByText(
        'This is the Research route. You can add your research tools and features here.',
      ),
    ).toBeInTheDocument()
    expect(screen.getByText('Analyze stocks and market trends')).toBeInTheDocument()
    expect(screen.getByText('Research market conditions and opportunities')).toBeInTheDocument()
    await screen.findByTestId('securities-panel')
  })

  it('marks the two repeated cards up as a list', async () => {
    // ANV-32's port checklist. Layout-neutral — Tailwind's preflight zeroes the marker and
    // the padding — and it is what makes "two of the same thing" true in the a11y tree.
    mockResearchApi()
    renderPage()

    const cards = screen.getByRole('heading', { level: 3, name: 'Stock Analysis' }).closest('ul')
    expect(cards).not.toBeNull()
    expect(within(cards).getAllByRole('listitem')).toHaveLength(2)
    await screen.findByTestId('securities-panel')
  })

  it('keeps the placeholder s data-testid, so the ANV-27 routing tests need no edit', async () => {
    mockResearchApi()
    renderPage()

    expect(screen.getByTestId('route-research')).toBeInTheDocument()
    await screen.findByTestId('securities-panel')
  })
})

describe('the palette a signed-in user is offered (REAL)', () => {
  it('is the full one, not the public subset', async () => {
    // Derived, not restated: a literal list of five names would keep passing on the day
    // somebody marks a fetching widget `network: false`.
    mockResearchApi()
    renderPage()

    const offered = menu()
      .getAllByRole('listitem')
      .map((chip) => chip.textContent.trim())

    expect(offered).toEqual(WIDGET_PALETTE.map((item) => item.name))
    await screen.findByTestId('securities-panel')
  })

  it('offers strictly more than the marketing page does', async () => {
    // The discriminating half. Without it, "the full palette" also passes for a page that
    // took the default — on the day the two lists happen to be equal.
    mockResearchApi()
    renderPage()

    const offered = menu().getAllByRole('listitem')
    expect(offered.length).toBeGreaterThan(PUBLIC_WIDGET_PALETTE.length)
    // The two the marketing page must not offer, named through the data rather than by
    // hand: these are the rows that fetch.
    WIDGET_PALETTE.filter((item) => item.network === true).forEach((item) => {
      expect(menu().getByRole('button', { name: `Add ${item.name}` })).toBeInTheDocument()
    })
    await screen.findByTestId('securities-panel')
  })
})

describe('the securities list (REAL)', () => {
  it('loads through the API layer and lists what the server sent', async () => {
    mockResearchApi({ securities: [stock('AAPL', 'Apple Inc.'), stock('NVDA', 'NVIDIA Corporation')] })
    renderPage()

    expect(await screen.findByRole('button', { name: 'Open NVDA price chart' })).toBeInTheDocument()
    // MSW at the boundary, so this is the URL the real `authApi` built (CLAUDE.md §5).
    expect(requests.some((url) => url.endsWith('/v1/stocks?limit=50'))).toBe(true)
  })

  it('reaches all three of the endpoints the page is built on', async () => {
    // The chart and the watchlist only mount inside windows, so this one needs the grid —
    // which makes it the one test in this block whose *coverage* depends on an invented
    // size. The assertion itself is still about requests.
    mockResearchApi()
    renderPage({ useContainerSize: size1200x800 })

    await screen.findByTestId('securities-count')
    await waitFor(() => {
      expect(requests.some((url) => url.includes('/v1/stocks?'))).toBe(true)
      expect(requests.some((url) => url.includes('/v1/stocks/by-ticker/AAPL/data'))).toBe(true)
      expect(requests.some((url) => url.includes('/v1/watchlists'))).toBe(true)
    })
  })
})

describe('the opening arrangement (WIRING — the size is invented)', () => {
  it('opens on the widgets that need a session, not on the demo s three', async () => {
    mockResearchApi()
    renderPage({ useContainerSize: size1200x800 })

    await waitFor(() =>
      expect(desktopWindows()).toEqual(RESEARCH_WINDOWS.map((w) => `desktop-window-${w.id}`)),
    )
    // The mutation this kills: dropping `initialWindows` from the page falls back to
    // `InteractiveDesktop`'s demo set and this asserts the ids are `research-*`.
    expect(desktopWindows()).toContain('desktop-window-research-chart')
    expect(desktopWindows()).toContain('desktop-window-research-watchlist')
  })

  it('renders the live data inside those windows', async () => {
    mockResearchApi()
    renderPage({ useContainerSize: size1200x800 })

    // The watchlist the server sent, in the window the arrangement opened.
    const watchlist = within(await screen.findByTestId('desktop-window-research-watchlist'))
    expect(await watchlist.findByText('NVDA')).toBeInTheDocument()

    // And the chart, drawn from a price that arrived as the quoted string "187.2500".
    const chart = within(screen.getByTestId('desktop-window-research-chart'))
    await waitFor(() => expect(chart.queryByTestId('stock-chart-loading')).not.toBeInTheDocument())
  })

  it('renders an unmeasured desktop with no windows at all, which is what jsdom reports', async () => {
    // The honest baseline, and the reason the three tests above say WIRING. It is also what
    // keeps ANV-27's and ANV-28's routing tests from suddenly issuing requests.
    mockResearchApi()
    renderPage()

    await screen.findByTestId('securities-count')
    expect(screen.getByTestId('binpacking-desktop')).toHaveAttribute('data-grid-ready', 'false')
    expect(desktopWindows()).toEqual([])
  })
})

describe('opening a chart for a picked security (WIRING for the mount, REAL for the request)', () => {
  it('charts the security that was clicked, keyed on its id', async () => {
    let chartedPath = null
    mockResearchApi({ securities: [stock('NVDA', 'NVIDIA Corporation')] })
    server.use(
      http.get(apiUrl('/v1/stocks/id-NVDA/data'), ({ request }) => {
        chartedPath = new URL(request.url).pathname
        return pageResponse([candle('2026-01-05T09:30:00', '900.1200')], { limit: 200 })
      }),
    )
    const user = userEvent.setup()
    renderPage({ useContainerSize: size1200x800 })

    await user.click(await screen.findByRole('button', { name: 'Open NVDA price chart' }))

    // A new window, carrying a chart named for the security — not the palette's AAPL.
    const added = within(await screen.findByTestId('desktop-window-win_1'))
    expect(added.getByRole('region', { name: 'NVDA price chart' })).toBeInTheDocument()
    // Keyed on `stock_id`, so the server is not asked to resolve the ticker a second time.
    await waitFor(() => expect(chartedPath).toBe('/v1/stocks/id-NVDA/data'))
  })

  it('says so out loud, in the desktop s own live region', async () => {
    // Adding a window is invisible to a screen reader — a new absolutely-positioned box in
    // a canvas with no reading order — and the announcement belongs to `openWindow` so a
    // chip and this list cannot say different things.
    mockResearchApi({ securities: [stock('NVDA', 'NVIDIA Corporation')] })
    server.use(
      http.get(apiUrl('/v1/stocks/id-NVDA/data'), () => pageResponse([], { limit: 200 })),
    )
    const user = userEvent.setup()
    renderPage({ useContainerSize: size1200x800 })

    expect(await screen.findByTestId('desktop-announcement')).toHaveTextContent('')
    await user.click(await screen.findByRole('button', { name: 'Open NVDA price chart' }))

    expect(screen.getByTestId('desktop-announcement')).toHaveTextContent('NVDA price chart added.')
  })

  it('reports a refusal rather than doing nothing silently', async () => {
    // The unmeasured desktop is the reachable refusal: `addFromTemplate` answers `null`
    // whenever there is nowhere to put the window, and a button that does nothing and says
    // nothing is indistinguishable from a broken one.
    mockResearchApi({ securities: [stock('NVDA', 'NVIDIA Corporation')] })
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: 'Open NVDA price chart' }))

    expect(screen.getByTestId('desktop-announcement')).toHaveTextContent(
      'No room for NVDA price chart.',
    )
    expect(desktopWindows()).toEqual([])
  })
})

/**
 * The cold load, end to end, through the real application (ANV-36) — **all REAL**.
 *
 * This is the path ANV-26 and ANV-24 designed together and which nothing had ever exercised
 * as one thing, because until this ticket no page behind the guard issued a request:
 *
 *  1. a reload holds a refresh token in `localStorage` and **no access token** (it lives in
 *     memory and memory is gone);
 *  2. `restore()` reads storage synchronously during `AuthProvider`'s first render, so
 *     `requireAuth` admits on the very first `beforeLoad` — no boot window, no spinner, no
 *     flash of `/login`;
 *  3. that admission is **provisional**, so the page mounts and its first protected call
 *     goes out with no `Authorization` header and comes back **401 `unauthorized`** — which
 *     is precisely why ANV-24's rule is "refresh on any 401 *except* `invalid_token` /
 *     `wrong_token_type`" rather than "refresh on `token_expired`";
 *  4. the interceptor refreshes once, stores the whole rotated pair, and replays the
 *     original request with the new bearer;
 *  5. the data renders and the user never saw a thing.
 *
 * The mock of `/v1/auth/refresh` is **single-use**, exactly like the real endpoint: it
 * rotates the pair and refuses an already-spent token. So this cannot pass by accident from
 * a second refresh, and it is the same discriminating mock ANV-24 used for its
 * single-flight test.
 */
describe('a cold load with a stored refresh token (REAL)', () => {
  function renderAppAt(path) {
    window.history.replaceState(null, '', path)
    return render(
      <ThemeProvider>
        <App />
      </ThemeProvider>,
    )
  }

  let seenAuthorization = null
  let refreshCalls = 0

  /** Refuses once with a 401, then answers whatever the replay presents. */
  function protectedOnce(path, response) {
    let calls = 0
    server.use(
      http.get(apiUrl(path), ({ request }) => {
        calls += 1
        if (calls === 1) {
          return errorResponse('unauthorized', 'Not authenticated.', { status: 401 })
        }
        seenAuthorization = request.headers.get('Authorization')
        return response()
      }),
    )
    return () => calls
  }

  beforeEach(() => {
    seenAuthorization = null
    refreshCalls = 0
    server.use(
      http.post(apiUrl(REFRESH_PATH), async ({ request }) => {
        refreshCalls += 1
        const body = await request.json()
        // Single-use, like the real endpoint: a spent token is refused.
        if (body?.refresh_token !== 'cold-refresh-1') {
          return errorResponse('invalid_token', 'Token is invalid.', { status: 401 })
        }
        return HttpResponse.json({
          access_token: 'access-2',
          refresh_token: 'refresh-2',
          token_type: 'bearer',
        })
      }),
    )
  })

  it('admits the session, refreshes on the first 401, replays, and renders the data', async () => {
    window.localStorage.setItem(REFRESH_TOKEN_KEY, 'cold-refresh-1')
    const stocksCalls = protectedOnce(STOCKS_PATH, () =>
      pageResponse([stock('NVDA', 'NVIDIA Corporation')], { total: 1 }),
    )

    renderAppAt('/research')

    // The guard admitted immediately — the page is what rendered, not `/login`.
    expect(await screen.findByTestId('route-research')).toBeInTheDocument()
    expect(screen.queryByTestId('route-login')).not.toBeInTheDocument()

    // …and the data arrived, which it could only do through the refresh-and-replay path.
    expect(await screen.findByText('NVIDIA Corporation')).toBeInTheDocument()
    expect(stocksCalls()).toBe(2)
    expect(refreshCalls).toBe(1)
    // The replay carried the *rotated* access token, not the empty one it started with.
    expect(seenAuthorization).toBe('Bearer access-2')
    expect(window.location.pathname).toBe('/research')
  })

  it('stores the whole rotated pair, so the next refresh is not the spent one', async () => {
    // ANV-24: storing only `access_token` breaks the *next* refresh, and nothing on screen
    // shows it until the second expiry. The stored value is the assertion.
    window.localStorage.setItem(REFRESH_TOKEN_KEY, 'cold-refresh-1')
    protectedOnce(STOCKS_PATH, () => pageResponse([stock('NVDA', 'NVIDIA Corporation')]))

    renderAppAt('/research')
    await screen.findByText('NVIDIA Corporation')

    expect(window.localStorage.getItem(REFRESH_TOKEN_KEY)).toBe('refresh-2')
  })

  it('ends the session when the stored refresh token is refused', async () => {
    // The other half, and the one that makes the test above discriminating: a *dead*
    // refresh token must not leave the user staring at a research page that never loads.
    // `provisional` becomes false the moment the server says so, and `onSignOut` carries
    // where they were.
    window.localStorage.setItem(REFRESH_TOKEN_KEY, 'a-token-the-server-has-killed')
    protectedOnce(STOCKS_PATH, () => pageResponse([]))

    renderAppAt('/research')

    expect(await screen.findByTestId('route-login')).toBeInTheDocument()
    await waitFor(() =>
      expect(`${window.location.pathname}${window.location.search}`).toBe(
        '/login?redirect=%2Fresearch',
      ),
    )
    expect(window.localStorage.getItem(REFRESH_TOKEN_KEY)).toBeNull()
  })
})
