import { RouterProvider, createMemoryHistory } from '@tanstack/react-router'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ANONYMOUS_NAV_ITEMS } from '@components/layout/navItems'
import { PUBLIC_WIDGET_PALETTE, WIDGET_PALETTE } from '@features/widgets'
import { createAppRouter } from '@lib/router'
import { AuthContext } from '@providers/AuthContext'
import { ThemeProvider } from '@providers/ThemeProvider'
import { server } from '@test/msw/server'

import Workflow from './Workflow'

/**
 * The home marketing page (ANV-32).
 *
 * The **real router**, for the same reason `Header.test.jsx` uses one: every call to action
 * on this page is a TanStack `<Link>`, and the single most important property of the port
 * is that none of them is an `<a href>`. Those two render the *same* `href` and navigate
 * completely differently — an anchor reloads the document and ANV-26 keeps the access token
 * in memory, so the old "Start Researching Today" button signed out anyone who pressed it.
 * An `href` assertion cannot see the difference, so every one of these tests **clicks** and
 * asserts the router moved. Under `createMemoryHistory` a bare anchor moves nothing, which
 * is exactly the mutation that has to fail.
 */
function renderHome(path = '/') {
  const router = createAppRouter({ history: createMemoryHistory({ initialEntries: [path] }) })
  const auth = { isAuthenticated: false, login: vi.fn(), logout: vi.fn(), restore: vi.fn() }

  render(
    <ThemeProvider>
      <AuthContext.Provider value={auth}>
        <RouterProvider router={router} context={{ auth }} />
      </AuthContext.Provider>
    </ThemeProvider>,
  )

  return { router, location: () => router.state.location }
}

const pageRoot = () => screen.getByTestId('route-home')
const page = () => within(pageRoot())

beforeEach(() => {
  window.localStorage.clear()
  document.documentElement.className = ''
})

describe('the page replaces the placeholder', () => {
  it('renders at / and keeps the route-home testid', async () => {
    // ANV-27's and ANV-28's routing tests assert on this id; keeping it is what makes
    // "ANV-32 replaced a placeholder" a one-line diff in routes/home.jsx.
    renderHome()

    expect(await screen.findByTestId('route-home')).toBeInTheDocument()
    expect(page().queryByText(/Coming in ANV/)).not.toBeInTheDocument()
  })
})

describe('every section renders its copy', () => {
  it('shows the hero headline', async () => {
    // Split across four `<span>`s for the two-colour treatment, so the text is only whole
    // in the accessible name — which is the thing that matters anyway.
    renderHome()
    await screen.findByTestId('route-home')

    expect(
      page().getByRole('heading', { level: 1, name: 'Research Clearer. Invest Sharper.' }),
    ).toBeInTheDocument()
  })

  it.each([
    ['the hero pitch', /democratizing investment research/],
    ['a feature', /Advanced Charting/],
    ['the last feature', /Journal your investment decisions/],
    ['a workflow benefit', /Streamlined Research/],
    ['the free tier price', /\$0/],
    ['the pro price', /\$9\.99/],
    ['the premium price', /\$19\.99/],
    ['a footer column', /Community/],
  ])('shows %s', async (_what, pattern) => {
    renderHome()
    await screen.findByTestId('route-home')

    expect(page().getByText(pattern)).toBeInTheDocument()
  })

  it.each([
    ['Research & Analysis Tools', 'features'],
    ['Smart investing through Technology', 'workflow'],
    ['Pricing', 'pricing'],
    ['Contact Anvex', 'contact'],
  ])('heads the %s section', async (heading, id) => {
    renderHome()
    await screen.findByTestId('route-home')

    const level2 = page().getByRole('heading', { level: 2, name: heading })
    expect(document.getElementById(id)).toContainElement(level2)
  })
})

describe('the fragment ids the header links to', () => {
  /**
   * The test that stops this rotting.
   *
   * The list is **derived** from `ANONYMOUS_NAV_ITEMS`, not restated here: `navItems.js`
   * is the single definition of what the header links to, so adding a sixth marketing item
   * without a section — or renaming a section without its nav item — fails right here
   * instead of turning a nav link into a silent no-op nobody notices until a user does.
   */
  const linkedHashes = ANONYMOUS_NAV_ITEMS.map((item) => item.hash).filter(Boolean)

  it('is a non-empty list, so the sweep below cannot pass vacuously', () => {
    expect(linkedHashes).toEqual(['features', 'workflow', 'pricing', 'contact'])
  })

  it.each(linkedHashes)('#%s exists on the home page', async (hash) => {
    renderHome()
    await screen.findByTestId('route-home')

    const target = document.getElementById(hash)
    expect(
      target,
      `the header links to #${hash} and nothing on the page has that id`,
    ).not.toBeNull()
    expect(pageRoot()).toContainElement(target)
  })

  it('makes each of them a landmark a screen reader can jump to', async () => {
    renderHome()
    await screen.findByTestId('route-home')

    // An unnamed <section> is a generic element; `aria-labelledby` is what promotes it to a
    // region, which is the only way to reach these without scrolling.
    const ids = page()
      .getAllByRole('region')
      .map((region) => region.getAttribute('id'))

    expect(ids).toEqual(linkedHashes)
  })
})

describe('every in-app destination goes through the router', () => {
  /**
   * `[accessible name, which one, where it lands]`. Two links share the name "Subscribe",
   * so the index disambiguates rather than a brittle container query.
   */
  const DESTINATIONS = [
    ['Start Researching Today', 0, '/signup', ''],
    ['Learn More', 0, '/', 'features'],
    ['Get Started', 0, '/signup', ''],
    ['Subscribe', 0, '/signup', ''],
    ['Subscribe', 1, '/signup', ''],
    ['Contact Us', 0, '/', 'contact'],
    ['Features', 0, '/', 'features'],
    ['How It Works', 0, '/', 'workflow'],
    ['Pricing', 0, '/', 'pricing'],
  ]

  it.each(DESTINATIONS)(
    'clicking "%s" (#%i) moves the router to %s#%s',
    async (name, index, pathname, hash) => {
      const user = userEvent.setup()
      const { location } = renderHome()
      await screen.findByTestId('route-home')

      await user.click(page().getAllByRole('link', { name })[index])

      await waitFor(() => {
        expect(location().pathname).toBe(pathname)
        expect(location().hash).toBe(hash)
      })
    },
  )

  it('has no link that goes nowhere', async () => {
    // The old footer had three `<a href="#">` entries for pages Anvex does not have: a
    // control that takes focus, announces itself as a link, and scrolls you to the top.
    renderHome()
    await screen.findByTestId('route-home')

    for (const anchor of pageRoot().querySelectorAll('a')) {
      const href = anchor.getAttribute('href')
      const label = anchor.textContent.trim()
      expect(href, `"${label}" has no href`).toBeTruthy()
      expect(href, `"${label}" links to #`).not.toBe('#')
    }
  })

  it.each(['Investment Guides', 'Investment Forums', 'Educational Resources'])(
    'keeps "%s" as words rather than a link to nowhere',
    async (label) => {
      renderHome()
      await screen.findByTestId('route-home')

      expect(page().getByText(label)).toBeInTheDocument()
      expect(page().queryByRole('link', { name: label })).not.toBeInTheDocument()
    },
  )
})

describe('the heading outline', () => {
  it('starts at h1 and never skips a level', async () => {
    // The old sections used <h5> under an <h2>: a two-level jump, which a screen-reader
    // user navigating by heading hears as two sections that are not there.
    renderHome()
    await screen.findByTestId('route-home')

    const levels = [...pageRoot().querySelectorAll('h1, h2, h3, h4, h5, h6')].map((node) =>
      Number(node.tagName[1]),
    )

    expect(levels.length).toBeGreaterThan(10)
    expect(levels[0]).toBe(1)
    for (let i = 1; i < levels.length; i += 1) {
      expect(levels[i], `h${levels[i]} follows h${levels[i - 1]}`).toBeLessThanOrEqual(
        levels[i - 1] + 1,
      )
    }
  })

  it('has exactly one h1 in the accessibility tree', async () => {
    renderHome()
    await screen.findByTestId('route-home')

    // The header's wordmark is also an <h1>, but it is aria-hidden, so a document-wide
    // role query is the honest place to assert this.
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
  })
})

describe('pricing says what it means without relying on colour', () => {
  it.each([
    ['Basic Stock Charts', 'Included:'],
    ['Up to 5 Watchlist Items', 'Included:'],
    ['Advanced Analytics', 'Not included:'],
    ['Portfolio Tracking', 'Not included:'],
  ])('announces "%s" as "%s"', async (label, prefix) => {
    // Both glyphs are aria-hidden, so before ANV-32 the free tier read as though it
    // included the two things it exists to withhold — and a red/green colour deficiency
    // left a sighted reader with only the shape.
    renderHome()
    await screen.findByTestId('route-home')

    const row = page().getByText(label).closest('li')
    expect(row).toHaveTextContent(`${prefix} ${label}`)
  })

  it('keeps the tick and the cross decorative', async () => {
    renderHome()
    await screen.findByTestId('route-home')

    const row = page().getByText('Advanced Analytics').closest('li')
    expect(row.querySelector('svg')).toHaveAttribute('aria-hidden', 'true')
  })

  it('makes each plan name a heading', async () => {
    renderHome()
    await screen.findByTestId('route-home')

    for (const name of [/^Basic/, /^Pro$/, /^Premium/]) {
      expect(page().getByRole('heading', { level: 3, name })).toBeInTheDocument()
    }
  })
})

describe('the contact form', () => {
  it('names every control', async () => {
    renderHome()
    await screen.findByTestId('route-home')

    for (const label of ['Name', 'Email', 'Message']) {
      expect(page().getByLabelText(label)).toBeInTheDocument()
    }
  })

  it('leaves the send button reachable and says why it does nothing', async () => {
    // `disabled` would take it out of the tab order *and* out of most screen readers'
    // browse output, so a keyboard user met a section with no control and no explanation.
    renderHome()
    await screen.findByTestId('route-home')

    const send = page().getByRole('button', { name: 'Send' })
    expect(send).toHaveAttribute('aria-disabled', 'true')
    expect(send).toHaveAccessibleDescription(/not connected yet/i)
  })

  it('still refuses to submit', async () => {
    const user = userEvent.setup()
    const { location } = renderHome()
    await screen.findByTestId('route-home')

    await user.type(page().getByLabelText('Name'), 'Ada')
    await user.click(page().getByRole('button', { name: 'Send' }))

    // Nothing sent (MSW's `onUnhandledRequest: 'error'` would fail the test if it had) and
    // nothing navigated.
    expect(location().pathname).toBe('/')
    expect(page().getByLabelText('Name')).toHaveValue('Ada')
  })
})

describe('the interactive desktop in the workflow panel (ANV-35)', () => {
  it('mounts inside the panel the seam promised', async () => {
    renderHome()
    await screen.findByTestId('route-home')

    const panel = screen.getByTestId('workflow-demo-panel')
    expect(within(panel).getByTestId('interactive-desktop')).toBeInTheDocument()
    expect(screen.queryByTestId('workflow-demo-placeholder')).not.toBeInTheDocument()
  })

  it('offers only the widgets that make no network call', async () => {
    // **Derived from the palette, not restated.** Naming the three here would keep passing
    // on the day a fetching widget is mis-flagged, which is the mistake the split exists to
    // catch. This page is read by logged-out visitors and `authApi` would 401 at them.
    renderHome()
    await screen.findByTestId('route-home')

    const offered = within(screen.getByTestId('window-menu'))
      .getAllByRole('listitem')
      .map((chip) => chip.textContent.trim())

    expect(offered).toEqual(PUBLIC_WIDGET_PALETTE.map((item) => item.name))
    for (const item of WIDGET_PALETTE.filter((entry) => entry.network)) {
      expect(offered).not.toContain(item.name)
    }
  })

  it('issues no requests at all while a visitor is on it', async () => {
    // `onUnhandledRequest: 'error'` alone is not enough: a widget that catches its own 401
    // and renders an error state would leave this suite green while showing a broken panel
    // to every logged-out visitor. So count what MSW actually saw.
    const seen = []
    const record = ({ request }) => seen.push(request.url)
    server.events.on('request:start', record)

    try {
      renderHome()
      await screen.findByTestId('route-home')
      await screen.findByTestId('interactive-desktop')

      expect(seen).toEqual([])
    } finally {
      server.events.removeAllListeners('request:start')
    }
  })
})

describe('the seam ANV-35 plugs InteractiveDesktop into', () => {
  it('renders whatever is passed as the demo, inside the panel', () => {
    // No router needed: `Workflow` has no links, which is also what lets ANV-35 test the
    // window system here without mounting the app.
    render(<Workflow demo={<div data-testid="interactive-desktop" />} />)

    const panel = screen.getByTestId('workflow-demo-panel')
    expect(within(panel).getByTestId('interactive-desktop')).toBeInTheDocument()
    expect(screen.queryByTestId('workflow-demo-placeholder')).not.toBeInTheDocument()
  })

  it('holds a decorative placeholder until then', () => {
    render(<Workflow />)

    const placeholder = within(screen.getByTestId('workflow-demo-panel')).getByTestId(
      'workflow-demo-placeholder',
    )
    // Textless and out of the accessibility tree, so it invents no marketing copy and
    // promises a screen-reader user nothing they cannot operate.
    expect(placeholder).toHaveAttribute('aria-hidden', 'true')
    expect(placeholder).toHaveTextContent('')
  })
})
