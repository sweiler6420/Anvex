import { forwardRef, useCallback, useImperativeHandle, useRef, useState } from 'react'

import { BinPackingLayout, WindowMenu } from '@features/desktop'
import { CounterWidget, PUBLIC_WIDGET_PALETTE, StaticInfoWidget, TextInputWidget } from '@features/widgets'

/**
 * The interactive desktop demo (ANV-35), ported from
 * `AverageInvestorWeb/src/components/home/InteractiveDesktop.jsx` (109 lines).
 *
 * A palette strip over a `BinPackingLayout`, and nothing else: it is **composition, not
 * code**. ANV-33 owns the window system, ANV-34 owns the widgets, and the entire integration
 * between them is `WIDGET_PALETTE`, a table of `{name, color, window}` in the shape
 * `WindowMenu` already took.
 *
 * ---------------------------------------------------------------------------------------
 * ## Why this is its own feature and not `features/home/`
 *
 * Because `/research` (ANV-36) wants the same component with the *other* palette. Putting it
 * in `features/home/` would make the authenticated workspace import the marketing page, and
 * putting it in `features/desktop/` would make the window system import the widgets — the one
 * dependency `features/desktop/index.js` exists to avoid. A composition of two features is
 * its own domain area (CLAUDE.md §5, feature-first), so it gets a folder.
 *
 * ---------------------------------------------------------------------------------------
 * ## The palette default is the public one, and that is the security decision
 *
 * `items` defaults to `PUBLIC_WIDGET_PALETTE` — the rows that declare `network: false`. Two
 * of the five widgets fetch through `authApi` on mount, so on a page a logged-out visitor
 * reaches they 401 and render an error state; the marketing demo must therefore not offer
 * them, and *the way it must not offer them is by default*. `/research` opts in with an
 * explicit `items={WIDGET_PALETTE}`, where there is a session and a 401 means something.
 *
 * The subset is derived in `features/widgets/palette.jsx` from a flag on each row rather
 * than listed here, so a sixth widget added by somebody who never opens this file is
 * excluded rather than admitted. See that file for the full argument.
 *
 * ---------------------------------------------------------------------------------------
 * ## What ANV-36 added, and why each is a prop rather than a second component
 *
 *  - **`initialWindows`** — `/` opens on three pure widgets because it is a demo of a
 *    window manager; `/research` opens on the two that need a session, because that is
 *    what a signed-in user came for. The arrangement is read **once**, in the lazy state
 *    initialiser, so a caller cannot reset a user's desktop by re-rendering with a new
 *    array. See `../researchWindows.js` for the research set, which is *derived* from the
 *    palette rather than restated.
 *  - **An imperative handle exposing exactly `openWindow`** — see below.
 *
 * ---------------------------------------------------------------------------------------
 * ## Two contracts `BinPackingLayout` will not warn you about
 *
 *  - **`onWindowsChange` is always called with an updater function**, so the prop must be a
 *    React state setter. A `(next) => …` callback silently loses every drag. **This one is
 *    unkillable by mutation from here** and the reason is worth knowing: `BinPackingLayout`
 *    renders from its own internal copy and only *tells* the owner, so replacing `setWindows`
 *    with `() => {}` still draws every window correctly — the arrangement is simply no longer
 *    anywhere the owner can read, and nothing shows it until something re-renders with a new
 *    `windows` identity. ANV-33 pins the contract one level down, in
 *    `BinPackingLayout.test.jsx`'s "calls onWindowsChange with an updater, not with a value".
 *  - **`windows` must not be an inline array literal.** A new identity every render re-syncs
 *    the component's internal copy every render, which throws away the arrangement mid-drag.
 *    Hence `useState`, and hence the initial set built once in a lazy initialiser.
 *
 * ---------------------------------------------------------------------------------------
 * ## Changed from the original
 *
 *  - **`apiRef` is now used.** The original created a ref, passed it to `BinPackingLayout`
 *    and never read it. Here it is how the click-to-add palette places a window.
 *  - **The palette is click-operable, not only draggable.** ANV-33 flagged the `draggable`
 *    `<li>`s as the last mouse-only path in the desktop and ANV-34 answered the same problem
 *    in the watchlist with buttons; this is that answer, applied here. The drag is untouched.
 *  - **Two of the three initial windows had no `content` at all** — the marketing page shipped
 *    two empty boxes and a `<div>` reading "Hello World". They carry the pure widgets now.
 *  - **`minWidth: 5, minHeight: 4` became `2`/`2`**, matching the floor ANV-33 fixed and the
 *    palette's own claim. A window whose advertised minimum is larger than the size its
 *    content survives is hiding a constraint rather than meeting it.
 *  - **The menu items lost their colour names.** The original's chips were "Blue", "Green"
 *    and "Red" — the name said what the chrome looked like rather than what the window was,
 *    which is unreadable to anyone who cannot see the chip. ANV-34's palette had already
 *    named them Counter / Info / Echo.
 *  - **`font-neutral-500` on the caption is `text-neutral-500`.** The original is not a
 *    Tailwind class and emitted nothing.
 *  - **The palette strip scrolls horizontally** rather than being clipped. It is a
 *    `flex` row inside a panel with `overflow-hidden`, so at a phone width the last chip
 *    simply disappeared.
 */

/**
 * The **demo's** opening arrangement, in grid cells — the default for `initialWindows`.
 *
 * Sized for the smallest panel it has to look right in: `Workflow`'s is `h-96` below `lg`,
 * which after the strip and the caption leaves roughly 13 rows, and about 22 columns beside
 * a `lg` breakpoint's half-width column. The extent below is 18 × 11. Narrower than that and
 * the grid does not grow — every window's minimum is 2 × 2, so `reflowScaleByOverlap` scales
 * the arrangement down instead of forcing the panel to scroll.
 */
const INITIAL_WINDOWS = [
  {
    id: 'demo-counter',
    title: 'Counter',
    color: '#3b82f6',
    x: 0,
    y: 0,
    width: 8,
    height: 5,
    minWidth: 2,
    minHeight: 2,
    content: <CounterWidget />,
  },
  {
    id: 'demo-info',
    title: 'Info',
    color: '#8b5cf6',
    x: 8,
    y: 0,
    width: 10,
    height: 6,
    minWidth: 2,
    minHeight: 2,
    content: <StaticInfoWidget />,
  },
  {
    id: 'demo-echo',
    title: 'Echo input',
    color: '#f59e0b',
    x: 0,
    y: 6,
    width: 10,
    height: 5,
    minWidth: 2,
    minHeight: 2,
    content: <TextInputWidget />,
  },
]

/**
 * @param {object} props
 * @param {Array<{name: string, color: string, window: object}>} [props.items] the palette.
 *   Defaults to the rows that make no network call; `/research` passes `WIDGET_PALETTE`.
 * @param {Array<object>} [props.initialWindows] the opening arrangement. Read **once**, in
 *   a lazy state initialiser — a later change to this prop is ignored, because after the
 *   first render the arrangement belongs to the user (ANV-36).
 * @param {Function} [props.useContainerSize] ANV-33's measurement seam, forwarded so a test
 *   can fabricate a size in the file that asserts against it. Never passed in production.
 * @param {React.Ref<{openWindow: Function}>} ref the imperative handle described above.
 */
const InteractiveDesktop = forwardRef(function InteractiveDesktop(
  { items = PUBLIC_WIDGET_PALETTE, initialWindows = INITIAL_WINDOWS, useContainerSize },
  ref,
) {
  const apiRef = useRef(null)
  /**
   * Read once. The lazy form is an **EQUIVALENT MUTANT** now that the value is a prop —
   * `useState(initialWindows)` passes the whole suite, because the argument is already
   * evaluated either way and `useState` ignores it after the first render. It is kept for
   * what it says: this is an *initial* value, not a controlled one, and a reader who
   * changes it to look controlled has to delete the arrow first. Recorded rather than
   * simplified (ANV-33's rule about the wrong half of a redundant pair).
   */
  const [windows, setWindows] = useState(() => initialWindows)

  /**
   * Adding a window is invisible to a screen reader — a new absolutely-positioned box
   * appears somewhere in a canvas that has no reading order — so the palette says so out
   * loud. It is also the only way to report the refusal: `addFromTemplate` returns `null`
   * when the grid has no room, and a button that does nothing and says nothing is
   * indistinguishable from a broken one.
   */
  const [announcement, setAnnouncement] = useState('')

  /**
   * Open one window and say so. The palette's chips call it, and so does anything holding
   * the imperative handle below.
   *
   * @param {{name: string, window: object}} item
   * @returns {string | null} the new window's id, or `null` when there was no room
   */
  const openWindow = useCallback((item) => {
    const id = apiRef.current?.addFromTemplate(item.window) ?? null
    setAnnouncement(id ? `${item.name} added.` : `No room for ${item.name}.`)
    return id
  }, [])

  /**
   * The one thing a caller may do to this desktop from outside (ANV-36).
   *
   * `/research`'s securities list opens a chart for the security the user picked, and only
   * `BinPackingLayout` knows where a window fits — so the answer is handed up
   * imperatively rather than the grid being published downward (ANV-35's rule). What is
   * *not* forwarded is `BinPackingLayout`'s handle itself: `addWindow`/`removeWindow`/
   * `replaceWindows` would let a caller write the arrangement behind this component's
   * back, and `addFromTemplate` alone would move the announcement out of the one place
   * that owns it. A page that adds a window silently is a page where a screen-reader user
   * cannot tell a full grid from a broken button.
   */
  useImperativeHandle(ref, () => ({ openWindow }), [openWindow])

  return (
    <div className="flex h-full w-full flex-col" data-testid="interactive-desktop">
      <div className="overflow-x-auto rounded-lg border border-neutral-500 bg-white/50 p-2 backdrop-blur dark:bg-neutral-900/50">
        <WindowMenu items={items} label="Add a window:" onAdd={openWindow} />
      </div>

      <div className="flex-1">
        <BinPackingLayout
          ref={apiRef}
          cellSize={20}
          minGridWidth={4}
          minGridHeight={3}
          windows={windows}
          onWindowsChange={setWindows}
          useContainerSize={useContainerSize}
        />
      </div>

      {/* Rendered unconditionally and left empty (ANV-29): a live region has to exist before
          its text arrives, since inserting the region and the text together is the case
          screen readers handle worst. */}
      <p role="status" aria-live="polite" className="sr-only" data-testid="desktop-announcement">
        {announcement}
      </p>

      <p className="m-2 rounded px-2 text-center font-gothic text-sm font-medium text-neutral-500">
        Drag a window to move it, or drop a new one in — they pack without overlapping.
      </p>
    </div>
  )
})

export default InteractiveDesktop
