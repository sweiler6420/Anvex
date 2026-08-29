import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { clearPendingWindow, peekPendingWindow, resetWindowIdCounter } from '@features/desktop'
import { PUBLIC_WIDGET_PALETTE, WIDGET_PALETTE } from '@features/widgets'
import { apiUrl } from '@lib/env'
import { pageResponse } from '@test/msw/handlers'
import { server } from '@test/msw/server'

import InteractiveDesktop from './InteractiveDesktop'

/**
 * ANV-35 — the interactive desktop demo.
 *
 * ## Which of these prove behaviour and which prove wiring
 *
 * **REAL** — the palette's contents, the network assertions, the drag hand-off and the
 * click-to-add announcement. None of them needs a box model: what the demo *offers*, what it
 * *requests*, and what it *says* are all observable in an unmeasured jsdom exactly as they
 * are in a browser. The network ones are the point of the ticket.
 *
 * **WIRING (the size is invented)** — anything that asserts a window exists. jsdom has no
 * layout, `useContainerSize` reports 0×0 and `BinPackingLayout` correctly renders an empty
 * container, so every test below that needs a grid passes `fixedSize`. Following ANV-33's
 * rule, the fabrication is a **named prop** threaded through `InteractiveDesktop` rather than
 * a `ResizeObserver` mock, so the invented number sits beside the assertion it supports. What
 * those tests establish is that this component hands the palette and the window list to the
 * desktop and renders what the desktop answered — not that a real panel is 800 × 400.
 */

/** The fabricated measurement. 800 × 400 at 20px cells is a 40 × 20 grid. */
const fixedSize = (width, height) => () => ({ width, height })
const size800x400 = fixedSize(800, 400)

const WATCHLIST_ID = '22222222-2222-4222-8222-222222222222'

/** Every request MSW saw during a test, whether or not a handler answered it. */
const requests = []

beforeEach(() => {
  resetWindowIdCounter()
  requests.length = 0
  // Installed here rather than in `src/test/setup.js`: a shared listener is state leaking
  // between tests, and only this file cares (ANV-27's rule for `window.scrollTo`).
  server.events.on('request:start', ({ request }) => requests.push(request.url))
})

afterEach(() => {
  server.events.removeAllListeners('request:start')
  clearPendingWindow()
})

const menu = () => within(screen.getByTestId('window-menu'))
const desktopWindows = () =>
  [...document.querySelectorAll('[data-testid^="desktop-window-"]')].map((node) =>
    node.getAttribute('data-testid'),
  )

describe('what the demo offers (REAL — no measurement involved)', () => {
  it('offers exactly the widgets that make no network call', () => {
    // **Derived, not restated.** A literal `['Counter', 'Info', 'Echo']` would keep passing
    // on the day somebody marks a fetching widget `network: false`, which is the mistake
    // this whole split exists to catch.
    render(<InteractiveDesktop />)

    const offered = menu()
      .getAllByRole('listitem')
      .map((chip) => chip.textContent.trim())

    expect(offered).toEqual(PUBLIC_WIDGET_PALETTE.map((item) => item.name))
  })

  it('leaves something out, so the assertion above is not the whole palette wearing a hat', () => {
    // The guard against the other failure: if every row were `network: false` the subset
    // would equal the palette and "the public demo offers only pure widgets" would be true
    // and worthless.
    expect(PUBLIC_WIDGET_PALETTE.length).toBeGreaterThan(0)
    expect(PUBLIC_WIDGET_PALETTE.length).toBeLessThan(WIDGET_PALETTE.length)
  })

  it('takes a caller-supplied palette, which is how /research gets the full one', () => {
    render(<InteractiveDesktop items={WIDGET_PALETTE} />)

    expect(menu().getAllByRole('listitem')).toHaveLength(WIDGET_PALETTE.length)
  })

  it('renders an unmeasured desktop, which is what jsdom always reports', () => {
    render(<InteractiveDesktop />)

    expect(screen.getByTestId('binpacking-desktop')).toHaveAttribute('data-grid-ready', 'false')
    expect(desktopWindows()).toEqual([])
  })
})

describe('the marketing page makes no requests (REAL — this is the ticket s decision)', () => {
  it('mounts and can be fully driven without issuing one', async () => {
    const user = userEvent.setup()
    render(<InteractiveDesktop useContainerSize={size800x400} />)

    // Not just "it mounted quietly": open one of everything the visitor is offered, which is
    // the only way a widget's `useEffect` ever runs.
    for (const item of PUBLIC_WIDGET_PALETTE) {
      await user.click(menu().getByRole('button', { name: `Add ${item.name}` }))
    }

    await waitFor(() =>
      expect(desktopWindows()).toHaveLength(3 + PUBLIC_WIDGET_PALETTE.length),
    )
    expect(requests).toEqual([])
  })

  it('and the same drive with the FULL palette does issue them — so the test above can fail', async () => {
    // The discriminating half. Without it, "no requests" would also pass for a demo that
    // renders nothing, offers nothing, or never mounts a widget's effects.
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
    const user = userEvent.setup()
    render(<InteractiveDesktop items={WIDGET_PALETTE} useContainerSize={size800x400} />)

    for (const item of WIDGET_PALETTE) {
      await user.click(menu().getByRole('button', { name: `Add ${item.name}` }))
    }

    await waitFor(() => expect(requests.length).toBeGreaterThan(0))
  })
})

describe('the default window set (WIRING — the size is invented)', () => {
  it('opens with three windows, each carrying a widget', async () => {
    render(<InteractiveDesktop useContainerSize={size800x400} />)

    expect(desktopWindows()).toEqual([
      'desktop-window-demo-counter',
      'desktop-window-demo-info',
      'desktop-window-demo-echo',
    ])

    // The original shipped two of its three windows with no `content` at all. Every widget's
    // frame is a named region, so an empty window has none.
    const regions = await screen.findAllByRole('region')
    expect(regions).toHaveLength(3)
  })

  it('advertises 2 × 2 as every window s minimum, which is the size ANV-33 fixed', async () => {
    const user = userEvent.setup()
    render(<InteractiveDesktop useContainerSize={size800x400} />)

    // The collapse control shrinks to `minWidth`/`minHeight`; at 20px cells that is 40 × 40.
    await user.click(
      within(screen.getByTestId('desktop-window-demo-counter')).getByRole('button', {
        name: 'Collapse to minimum size',
      }),
    )

    expect(screen.getByTestId('desktop-window-demo-counter')).toHaveStyle({
      width: '40px',
      height: '40px',
    })
  })
})

describe('click-to-add — the last mouse-only path (WIRING for the mount, REAL for the rest)', () => {
  it('mounts the widget into the layout when its chip is pressed', async () => {
    const user = userEvent.setup()
    render(<InteractiveDesktop useContainerSize={size800x400} />)

    await user.click(menu().getByRole('button', { name: 'Add Echo' }))

    // A fourth window, created by the desktop, carrying a second independent Echo widget.
    expect(desktopWindows()).toContain('desktop-window-win_1')
    const added = within(screen.getByTestId('desktop-window-win_1'))
    expect(added.getByRole('textbox')).toBeInTheDocument()
  })

  it('is reachable from the keyboard, which the drag never was', async () => {
    const user = userEvent.setup()
    render(<InteractiveDesktop useContainerSize={size800x400} />)

    const chip = menu().getByRole('button', { name: 'Add Counter' })
    chip.focus()
    await user.keyboard('{Enter}')

    expect(desktopWindows()).toContain('desktop-window-win_1')
  })

  it('says so out loud, because a new box in a canvas has no reading order', async () => {
    const user = userEvent.setup()
    render(<InteractiveDesktop useContainerSize={size800x400} />)

    // Rendered before its text arrives (ANV-29): inserting a live region and its content
    // together is the case screen readers handle worst.
    expect(screen.getByTestId('desktop-announcement')).toHaveTextContent('')

    await user.click(menu().getByRole('button', { name: 'Add Info' }))

    expect(screen.getByTestId('desktop-announcement')).toHaveTextContent('Info added.')
  })

  it('reports a refusal rather than doing nothing silently', async () => {
    // The unmeasured desktop is the reachable refusal: `addFromTemplate` returns `null`
    // whenever there is nowhere to put the window, and a button that does nothing and says
    // nothing is indistinguishable from a broken one. (The *grid is full* refusal is proved
    // deterministically in `features/desktop/components/BinPackingLayout.test.jsx`, where the
    // window list is the test's to fill; here it would depend on how the reflow happened to
    // repack three windows.)
    const user = userEvent.setup()
    render(<InteractiveDesktop />)

    await user.click(menu().getByRole('button', { name: 'Add Counter' }))

    expect(screen.getByTestId('desktop-announcement')).toHaveTextContent('No room for Counter.')
    expect(desktopWindows()).toEqual([])
  })
})

describe('the two props ANV-36 added', () => {
  /** A one-window arrangement a caller could not get from the default set. */
  const custom = [
    {
      id: 'caller-supplied',
      title: 'Supplied',
      color: '#3b82f6',
      x: 0,
      y: 0,
      width: 4,
      height: 4,
      minWidth: 2,
      minHeight: 2,
      content: null,
    },
  ]

  it('opens on the caller s arrangement instead of the demo s (WIRING — invented size)', () => {
    render(<InteractiveDesktop initialWindows={custom} useContainerSize={size800x400} />)

    expect(desktopWindows()).toEqual(['desktop-window-caller-supplied'])
  })

  it('still opens on the demo s three when nobody says otherwise (WIRING)', () => {
    // The default is what `/` takes, and it must not become "whatever the last caller
    // passed" — this is the assertion that fails if the prop loses its default.
    render(<InteractiveDesktop useContainerSize={size800x400} />)

    expect(desktopWindows()).toEqual([
      'desktop-window-demo-counter',
      'desktop-window-demo-info',
      'desktop-window-demo-echo',
    ])
  })

  it('ignores a later change to initialWindows, because the desktop is the user s now', async () => {
    // Read once, in the lazy state initialiser. A component that treated the prop as
    // controlled would throw away every window the user had moved on any parent re-render.
    const user = userEvent.setup()
    const { rerender } = render(
      <InteractiveDesktop initialWindows={custom} useContainerSize={size800x400} />,
    )
    await user.click(menu().getByRole('button', { name: 'Add Counter' }))
    expect(desktopWindows()).toContain('desktop-window-win_1')

    rerender(<InteractiveDesktop initialWindows={[]} useContainerSize={size800x400} />)

    expect(desktopWindows()).toContain('desktop-window-caller-supplied')
    expect(desktopWindows()).toContain('desktop-window-win_1')
  })

  it('lets a holder of the ref open a window, and announces it the same way', () => {
    // `/research`'s securities list is the caller. The announcement is the REAL half: it
    // belongs to `openWindow`, so a window opened from outside and a window opened from a
    // chip say the same thing in the same live region.
    const ref = { current: null }
    render(<InteractiveDesktop ref={ref} useContainerSize={size800x400} />)

    // `act` because this is a state update driven from outside React's event system —
    // exactly the case the securities list produces, one `onClick` further out.
    let id = null
    act(() => {
      id = ref.current.openWindow(PUBLIC_WIDGET_PALETTE[0])
    })

    expect(id).toBe('win_1')
    expect(desktopWindows()).toContain('desktop-window-win_1')
    expect(screen.getByTestId('desktop-announcement')).toHaveTextContent('Counter added.')
  })

  it('tells the ref holder about a refusal instead of pretending (REAL)', () => {
    // `null`, not a throw and not a silent nothing: "there is nowhere to put it" is an
    // ordinary outcome and the caller has to be able to see it.
    const ref = { current: null }
    render(<InteractiveDesktop ref={ref} />)

    let id = 'not-null'
    act(() => {
      id = ref.current.openWindow(PUBLIC_WIDGET_PALETTE[0])
    })

    expect(id).toBeNull()
    expect(screen.getByTestId('desktop-announcement')).toHaveTextContent('No room for Counter.')
  })

  it('exposes only openWindow, not the layout s whole handle (REAL)', () => {
    // Deliberately narrow. `addWindow`/`removeWindow`/`replaceWindows` would let a caller
    // write the arrangement behind this component's back, and a bare `addFromTemplate`
    // would move the announcement out of the one place that owns it.
    const ref = { current: null }
    render(<InteractiveDesktop ref={ref} useContainerSize={size800x400} />)

    expect(Object.keys(ref.current)).toEqual(['openWindow'])
  })
})

describe('the drag was not traded away for the click (REAL)', () => {
  it('still arms the pending window from the chip s button', () => {
    // A form control inside a `draggable` ancestor swallows the gesture in several browsers,
    // so the button carries `draggable` and the handlers itself. Without that this passes as
    // `null` and every mouse user silently loses the palette.
    render(<InteractiveDesktop />)

    const chip = menu().getByRole('button', { name: 'Add Counter' })
    expect(chip).toHaveAttribute('draggable', 'true')

    fireEvent.dragStart(chip, {
      dataTransfer: { setData: () => {}, effectAllowed: 'uninitialized' },
    })

    expect(peekPendingWindow()).toBe(PUBLIC_WIDGET_PALETTE[0].window)
  })
})
