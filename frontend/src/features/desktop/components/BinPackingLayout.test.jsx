import { act, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useRef, useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { clearPendingWindow, setPendingWindow } from '../dragPayload'
import { resetWindowIdCounter } from '../windowIds'
import BinPackingLayout from './BinPackingLayout'

/**
 * ANV-33 — the desktop.
 *
 * ## Read this before trusting anything below
 *
 * **Almost every test in this file proves wiring, not layout.** jsdom implements no box
 * model: `getBoundingClientRect()` returns 0×0 for every element and there is no
 * `ResizeObserver`. A desktop that measures its container measures nothing, so any test that
 * needs a grid has to invent one — and an invented grid cannot corroborate itself.
 *
 * The seam is explicit rather than hidden: `useContainerSize` is a prop, and `fixedSize`
 * below is a function returning two numbers somebody typed. What these tests can therefore
 * establish is that the component **passes what it measured to the geometry, and renders and
 * commits what the geometry answered** — that closing a window calls `closeWindow`, that the
 * grid it draws has the dimensions `computeGridSpecForWindows` returned, that a fullscreen
 * window covers the grid's extent. What they cannot establish is that a real panel produces
 * those numbers. The behaviour of the geometry itself is proved without any of this, in
 * `../geometry/*.test.js`.
 *
 * The three tests marked **REAL** need no fabricated size and are claims about the component
 * as a browser would run it: an unmeasured desktop renders nothing, the imperative handle
 * forwards, and the drop handler ignores a drag that is not ours.
 */

/** The fabricated measurement. Every grid in this file comes from these two numbers. */
const fixedSize = (width, height) => () => ({ width, height })

/** 800×400 at 20px cells is a 40×20 grid, exactly filling the container: no offsets. */
const size800x400 = fixedSize(800, 400)

const win = (id, overrides = {}) => ({
  id,
  title: `Window ${id}`,
  color: '#3b82f6',
  x: 0,
  y: 0,
  width: 6,
  height: 4,
  minWidth: 2,
  minHeight: 2,
  ...overrides,
})

/** A controlled host, because `onWindowsChange` is always called with an updater. */
function Host({ initial = [], apiRef, ...props }) {
  const [windows, setWindows] = useState(initial)
  return (
    <BinPackingLayout
      ref={apiRef}
      windows={windows}
      onWindowsChange={setWindows}
      useContainerSize={size800x400}
      {...props}
    />
  )
}

const desktop = () => screen.getByTestId('binpacking-desktop')

beforeEach(resetWindowIdCounter)
afterEach(clearPendingWindow)

describe('before anything has been measured (REAL — this is what jsdom always reports)', () => {
  it('renders an empty container and no grid', () => {
    render(
      <BinPackingLayout
        windows={[win('a'), win('b', { x: 6 })]}
        onWindowsChange={() => {}}
        useContainerSize={fixedSize(0, 0)}
      />,
    )

    expect(desktop()).toHaveAttribute('data-grid-ready', 'false')
    expect(desktop()).toBeEmptyDOMElement()
  })

  it('renders nothing under the real hook either, because jsdom never fires a ResizeObserver', () => {
    // No `useContainerSize` prop at all: this is the production path, with `setup.js`'s inert
    // observer behind it. The desktop is correctly empty, which is why the component tests
    // below all pass a size.
    render(<BinPackingLayout windows={[win('a')]} onWindowsChange={() => {}} />)

    expect(desktop()).toHaveAttribute('data-grid-ready', 'false')
    expect(desktop()).toBeEmptyDOMElement()
  })

  it('starts no drag and no resize while unmeasured', () => {
    render(
      <BinPackingLayout
        windows={[win('a')]}
        onWindowsChange={() => {}}
        useContainerSize={fixedSize(0, 0)}
      />,
    )

    expect(screen.queryByTestId('desktop-window-a')).not.toBeInTheDocument()
  })
})

describe('what it renders once it has a size (WIRING — the size is invented)', () => {
  it('renders one window per entry', () => {
    render(<Host initial={[win('a'), win('b', { x: 6 })]} />)

    expect(screen.getByTestId('desktop-window-a')).toBeInTheDocument()
    expect(screen.getByTestId('desktop-window-b')).toBeInTheDocument()
  })

  it('places each window at the pixels the grid geometry computed', () => {
    render(<Host initial={[win('a', { x: 3, y: 2 })]} />)

    // 40×20 cells in 800×400 leaves no remainder, so the grid offset is zero and the window
    // sits at (3, 2) × 20px with a 6×4 size.
    expect(screen.getByTestId('desktop-window-a')).toHaveStyle({
      left: '60px',
      top: '40px',
      width: '120px',
      height: '80px',
    })
  })

  it('draws the grid at the size the geometry reported', () => {
    render(<Host initial={[]} />)

    expect(screen.getByTestId('binpacking-grid')).toHaveStyle({
      width: '800px',
      height: '400px',
    })
  })

  it('draws one more line than there are cells, on both axes', () => {
    render(<Host initial={[]} />)

    const lines = screen.getByTestId('binpacking-grid').querySelectorAll('line')

    expect(lines).toHaveLength(41 + 21)
  })

  it('hides the grid the caller asked it to hide', () => {
    render(<Host initial={[]} showGridLines={false} />)

    expect(screen.queryByTestId('binpacking-grid')).not.toBeInTheDocument()
  })

  it('keeps the grid out of the accessibility tree — it is decoration', () => {
    render(<Host initial={[]} />)

    expect(screen.getByTestId('binpacking-grid')).toHaveAttribute('aria-hidden', 'true')
  })

  it('renders each window s content in its content box', () => {
    render(<Host initial={[win('a', { content: <p>a widget</p> })]} />)

    expect(within(screen.getByTestId('desktop-window-a')).getByText('a widget')).toBeInTheDocument()
  })

  it('offsets the grid inside a container that is not a whole number of cells', () => {
    render(<Host initial={[]} useContainerSize={fixedSize(810, 415)} />)

    expect(screen.getByTestId('binpacking-grid')).toHaveStyle({ left: '5px', top: '7px' })
  })

  it('scrolls rather than crushing the windows when the grid outgrows the container', () => {
    // Three windows side by side each needing 12 columns: 36 columns of 20px is 720px in a
    // 200px box.
    render(
      <Host
        initial={[
          win('a', { x: 0, width: 12, minWidth: 12 }),
          win('b', { x: 12, width: 12, minWidth: 12 }),
          win('c', { x: 24, width: 12, minWidth: 12 }),
        ]}
        useContainerSize={fixedSize(200, 400)}
      />,
    )

    expect(desktop()).toHaveStyle({ overflowX: 'auto' })
  })

  it('does not scroll when the caller forbade it', () => {
    render(
      <Host
        initial={[win('a', { width: 12, minWidth: 12 })]}
        useContainerSize={fixedSize(100, 100)}
        allowOverflowScroll={false}
      />,
    )

    expect(desktop()).toHaveStyle({ overflowX: 'hidden', overflowY: 'hidden' })
  })
})

describe('the window controls (WIRING — the state transitions are proved in ../geometry)', () => {
  it('close removes the window', async () => {
    const user = userEvent.setup()
    render(<Host initial={[win('a'), win('b', { x: 6 })]} />)

    await user.click(
      within(screen.getByTestId('desktop-window-a')).getByRole('button', { name: 'Close window' }),
    )

    expect(screen.queryByTestId('desktop-window-a')).not.toBeInTheDocument()
    expect(screen.getByTestId('desktop-window-b')).toBeInTheDocument()
  })

  it('collapse shrinks the window to its minimum size', async () => {
    const user = userEvent.setup()
    render(<Host initial={[win('a', { width: 6, height: 4, minWidth: 2, minHeight: 3 })]} />)

    await user.click(screen.getByRole('button', { name: 'Collapse to minimum size' }))

    expect(screen.getByTestId('desktop-window-a')).toHaveStyle({
      width: '40px',
      height: '60px',
    })
  })

  it('grow expands the window to fill the free space', async () => {
    const user = userEvent.setup()
    render(<Host initial={[win('a', { x: 4, y: 4 })]} />)

    await user.click(screen.getByRole('button', { name: 'Grow to fill free space' }))

    expect(screen.getByTestId('desktop-window-a')).toHaveStyle({
      left: '0px',
      top: '0px',
      width: '800px',
      height: '400px',
    })
  })

  it('fullscreen covers the grid and puts an overlay over everything else', async () => {
    const user = userEvent.setup()
    render(<Host initial={[win('a'), win('b', { x: 6 })]} />)

    await user.click(
      within(screen.getByTestId('desktop-window-a')).getByRole('button', {
        name: 'Fill the desktop',
      }),
    )

    expect(screen.getByTestId('desktop-window-a')).toHaveStyle({
      width: '800px',
      height: '400px',
    })
    expect(screen.getByTestId('binpacking-fullscreen-overlay')).toBeInTheDocument()
  })

  it('leaving fullscreen puts the window back exactly where it was', async () => {
    const user = userEvent.setup()
    render(<Host initial={[win('a', { x: 3, y: 2 })]} />)
    const before = screen.getByTestId('desktop-window-a').getAttribute('style')

    const control = () =>
      within(screen.getByTestId('desktop-window-a')).getByRole('button', {
        name: /desktop|Exit fullscreen/,
      })
    await user.click(control())
    await user.click(control())

    expect(screen.getByTestId('desktop-window-a').getAttribute('style')).toBe(before)
    expect(screen.queryByTestId('binpacking-fullscreen-overlay')).not.toBeInTheDocument()
  })

  it('refuses to fullscreen a second window while one already is', async () => {
    const user = userEvent.setup()
    render(<Host initial={[win('a'), win('b', { x: 6 })]} />)

    await user.click(
      within(screen.getByTestId('desktop-window-a')).getByRole('button', {
        name: 'Fill the desktop',
      }),
    )
    // `b`'s controls are under the overlay in a browser; in jsdom they are still clickable,
    // which is exactly why the component refuses rather than relying on the overlay.
    await user.click(
      within(screen.getByTestId('desktop-window-b')).getByRole('button', {
        name: 'Fill the desktop',
      }),
    )

    expect(screen.getByTestId('desktop-window-b')).not.toHaveStyle({ width: '800px' })
  })

  it('closing a fullscreen window takes the overlay with it', async () => {
    const user = userEvent.setup()
    render(<Host initial={[win('a')]} />)

    await user.click(screen.getByRole('button', { name: 'Fill the desktop' }))
    await user.click(screen.getByRole('button', { name: 'Close window' }))

    expect(screen.queryByTestId('binpacking-fullscreen-overlay')).not.toBeInTheDocument()
    expect(screen.queryByTestId('desktop-window-a')).not.toBeInTheDocument()
  })
})

describe('the imperative handle (REAL — no measurement involved)', () => {
  it('adds, removes and replaces through the owner s state', async () => {
    const api = { current: null }

    function Harness() {
      const ref = useRef(null)
      api.current = ref
      return <Host apiRef={ref} initial={[win('a')]} />
    }

    render(<Harness />)

    act(() => api.current.current.addWindow(win('b', { x: 6 })))
    expect(screen.getByTestId('desktop-window-b')).toBeInTheDocument()

    act(() => api.current.current.removeWindow('a'))
    expect(screen.queryByTestId('desktop-window-a')).not.toBeInTheDocument()

    act(() => api.current.current.replaceWindows([win('c')]))
    expect(screen.getByTestId('desktop-window-c')).toBeInTheDocument()
    expect(screen.queryByTestId('desktop-window-b')).not.toBeInTheDocument()
  })
})

describe('dropping a window in from the menu (WIRING)', () => {
  /**
   * A drag event carrying a cursor position.
   *
   * `fireEvent.dragOver(el, {clientX})` **does not work**: jsdom has no `DragEvent`
   * constructor, so Testing Library falls back to a plain `Event`, which ignores `clientX`
   * entirely and leaves it `undefined`. Everything downstream then computes with `NaN` — and
   * because every comparison against `NaN` is false, an impossible position reads as legal
   * and the test passes for the wrong reason. (That is how `fitCenteredRect` acquired its
   * finite-cursor guard: this fixture is what found it.)
   *
   * A `MouseEvent` named `drop` reaches React's `onDrop` — the synthetic system dispatches on
   * the event's *type*, not its constructor — and carries the coordinates.
   */
  const dragEvent = (type, clientX = 400, clientY = 200) => {
    const event = new MouseEvent(type, { bubbles: true, cancelable: true, clientX, clientY })
    Object.defineProperty(event, 'dataTransfer', {
      value: { dropEffect: 'none', getData: () => '', setData: () => {} },
    })
    return event
  }

  const template = {
    title: 'Dropped',
    color: '#22c55e',
    width: 6,
    height: 4,
    minWidth: 2,
    minHeight: 2,
  }

  it('shows a ghost while a window template is being dragged over it', () => {
    render(<Host initial={[]} />)
    setPendingWindow(template)

    fireEvent(desktop(), dragEvent('dragover'))

    expect(screen.getByTestId('binpacking-ghost')).toBeInTheDocument()
  })

  it('drops a new window and gives it a fresh id', () => {
    render(<Host initial={[]} />)
    setPendingWindow(template)

    fireEvent(desktop(), dragEvent('dragover'))
    fireEvent(desktop(), dragEvent('drop'))

    expect(screen.getByTestId('desktop-window-win_1')).toBeInTheDocument()
    expect(screen.getByText('Dropped')).toBeInTheDocument()
    expect(screen.queryByTestId('binpacking-ghost')).not.toBeInTheDocument()
  })

  it('gives two drops two different ids', () => {
    render(<Host initial={[]} />)

    setPendingWindow(template)
    fireEvent(desktop(), dragEvent('drop', 200, 100))
    setPendingWindow(template)
    fireEvent(desktop(), dragEvent('drop', 600, 300))

    expect(screen.getByTestId('desktop-window-win_1')).toBeInTheDocument()
    expect(screen.getByTestId('desktop-window-win_2')).toBeInTheDocument()
  })

  it('drops the window at a position that does not overlap what is already there', () => {
    render(<Host initial={[win('a', { x: 0, y: 0, width: 40, height: 10 })]} />)
    setPendingWindow(template)

    fireEvent(desktop(), dragEvent('drop', 0, 0))

    // The top half of the grid is taken; the geometry found somewhere else.
    const dropped = screen.getByTestId('desktop-window-win_1')
    expect(Number.parseInt(dropped.style.top, 10)).toBeGreaterThanOrEqual(200)
  })

  it('IGNORES a drag that is not ours (REAL — no measurement involved)', () => {
    render(<Host initial={[]} />)
    // Nothing armed: somebody dragging a file, or another component's payload.

    fireEvent(desktop(), dragEvent('dragover'))
    fireEvent(desktop(), dragEvent('drop'))

    expect(screen.queryByTestId('binpacking-ghost')).not.toBeInTheDocument()
    expect(desktop().querySelectorAll('[data-window-id]')).toHaveLength(0)
  })

  it('clears the ghost when the drag leaves', () => {
    render(<Host initial={[]} />)
    setPendingWindow(template)

    fireEvent(desktop(), dragEvent('dragover'))
    fireEvent(desktop(), dragEvent('dragleave'))

    expect(screen.queryByTestId('binpacking-ghost')).not.toBeInTheDocument()
  })

  it('refuses a drop while a window is fullscreen', async () => {
    const user = userEvent.setup()
    render(<Host initial={[win('a')]} />)
    await user.click(screen.getByRole('button', { name: 'Fill the desktop' }))

    setPendingWindow(template)
    fireEvent(desktop(), dragEvent('drop'))

    expect(screen.queryByTestId('desktop-window-win_1')).not.toBeInTheDocument()
  })
})

describe('dragging and resizing an existing window (WIRING)', () => {
  it('hides the window and shows a ghost once a drag produces a position', () => {
    render(<Host initial={[win('a', { x: 0, y: 0 })]} />)

    fireEvent.mouseDown(screen.getByText('Window a'))
    fireEvent.mouseMove(window, { clientX: 400, clientY: 200 })

    expect(screen.getByTestId('binpacking-ghost')).toBeInTheDocument()
    expect(screen.getByTestId('desktop-window-a')).toHaveStyle({ display: 'none' })
  })

  it('commits the ghost s position on mouse-up, and puts the window back on screen', () => {
    render(<Host initial={[win('a', { x: 0, y: 0 })]} />)

    fireEvent.mouseDown(screen.getByText('Window a'))
    fireEvent.mouseMove(window, { clientX: 400, clientY: 200 })
    fireEvent.mouseUp(window, { clientX: 400, clientY: 200 })

    const moved = screen.getByTestId('desktop-window-a')
    expect(moved).not.toHaveStyle({ display: 'none' })
    expect(screen.queryByTestId('binpacking-ghost')).not.toBeInTheDocument()
    // The cursor was at (400, 200) — the middle of the fabricated 800×400 container — and a
    // 6×4 window centred there starts at cell (17, 8).
    expect(moved).toHaveStyle({ left: '340px', top: '160px' })
  })

  it('leaves the window where it was when the drag never moved', () => {
    render(<Host initial={[win('a', { x: 3, y: 2 })]} />)
    const before = screen.getByTestId('desktop-window-a').getAttribute('style')

    // The cursor is over the centre of the window's own cells: (3 + 3, 2 + 2) × 20px. A
    // mouse-up there is a drag that ended where it began, and the window must not shift.
    fireEvent.mouseDown(screen.getByText('Window a'))
    fireEvent.mouseUp(window, { clientX: 120, clientY: 80 })

    expect(screen.getByTestId('desktop-window-a').getAttribute('style')).toBe(before)
  })

  it('previews a resize from state, without writing to the element behind React s back', () => {
    render(<Host initial={[win('a', { x: 0, y: 0, width: 6, height: 4 })]} />)

    fireEvent.mouseDown(screen.getByTestId('window-resize-a'), { clientX: 0, clientY: 0 })
    fireEvent.mouseMove(window, { clientX: 80, clientY: 40 })

    // Started 120×80px; the pointer moved +80×+40, i.e. four cells wider and two taller.
    expect(screen.getByTestId('desktop-window-a')).toHaveStyle({
      width: '200px',
      height: '120px',
    })
  })

  it('commits the previewed size on mouse-up', () => {
    render(<Host initial={[win('a', { x: 0, y: 0, width: 6, height: 4 })]} />)

    fireEvent.mouseDown(screen.getByTestId('window-resize-a'), { clientX: 0, clientY: 0 })
    fireEvent.mouseMove(window, { clientX: 80, clientY: 40 })
    fireEvent.mouseUp(window)

    expect(screen.getByTestId('desktop-window-a')).toHaveStyle({
      width: '200px',
      height: '120px',
    })
  })

  it('stops a resize at a neighbour rather than growing over it', () => {
    render(
      <Host initial={[win('a', { x: 0, y: 0, width: 6, height: 4 }), win('b', { x: 10, y: 0 })]} />,
    )

    fireEvent.mouseDown(screen.getByTestId('window-resize-a'), { clientX: 0, clientY: 0 })
    fireEvent.mouseMove(window, { clientX: 400, clientY: 0 })
    fireEvent.mouseUp(window)

    // `b` starts at cell 10, so `a` can reach 10 cells wide and no further.
    expect(screen.getByTestId('desktop-window-a')).toHaveStyle({ width: '200px' })
  })

  it('leaves the size alone when the resize never moved', () => {
    render(<Host initial={[win('a', { width: 6, height: 4 })]} />)
    const before = screen.getByTestId('desktop-window-a').getAttribute('style')

    fireEvent.mouseDown(screen.getByTestId('window-resize-a'), { clientX: 0, clientY: 0 })
    fireEvent.mouseUp(window)

    expect(screen.getByTestId('desktop-window-a').getAttribute('style')).toBe(before)
  })

  it('unbinds its window listeners when the gesture ends', () => {
    const remove = vi.spyOn(window, 'removeEventListener')
    render(<Host initial={[win('a')]} />)

    fireEvent.mouseDown(screen.getByText('Window a'))
    fireEvent.mouseUp(window)

    expect(remove).toHaveBeenCalledWith('mousemove', expect.any(Function))
    expect(remove).toHaveBeenCalledWith('mouseup', expect.any(Function))
  })
})

describe('the owner s state is the source of truth (WIRING)', () => {
  it('calls onWindowsChange with an updater, not with a value', async () => {
    const user = userEvent.setup()
    const onWindowsChange = vi.fn()
    render(
      <BinPackingLayout
        windows={[win('a'), win('b', { x: 6 })]}
        onWindowsChange={onWindowsChange}
        useContainerSize={size800x400}
      />,
    )
    onWindowsChange.mockClear()

    await user.click(
      within(screen.getByTestId('desktop-window-a')).getByRole('button', { name: 'Close window' }),
    )

    // This is the contract the original never wrote down: the prop must be a React state
    // setter, because everything it receives is `(prev) => next`.
    expect(onWindowsChange).toHaveBeenCalledOnce()
    expect(onWindowsChange.mock.calls[0][0]).toBeTypeOf('function')
    expect(onWindowsChange.mock.calls[0][0]([])).toHaveLength(1)
  })

  it('adopts a new arrangement handed down from above', () => {
    const { rerender } = render(
      <BinPackingLayout
        windows={[win('a')]}
        onWindowsChange={() => {}}
        useContainerSize={size800x400}
      />,
    )

    rerender(
      <BinPackingLayout
        windows={[win('z', { x: 10 })]}
        onWindowsChange={() => {}}
        useContainerSize={size800x400}
      />,
    )

    expect(screen.getByTestId('desktop-window-z')).toBeInTheDocument()
    expect(screen.queryByTestId('desktop-window-a')).not.toBeInTheDocument()
  })

  it('works without an onWindowsChange at all, holding the arrangement itself', async () => {
    const user = userEvent.setup()
    render(<BinPackingLayout windows={[win('a')]} useContainerSize={size800x400} />)

    await user.click(screen.getByRole('button', { name: 'Close window' }))

    expect(screen.queryByTestId('desktop-window-a')).not.toBeInTheDocument()
  })
})

describe('addFromTemplate — adding a window with no pointer (ANV-35)', () => {
  /**
   * The imperative half of ANV-35's click-to-add palette. The grid is measured here and
   * nowhere else, so a parent holding the window list cannot work out where a new window
   * fits; this method is how it asks.
   *
   * WIRING, apart from the two REAL cases marked below: the placement is real geometry
   * (`../geometry/rects.test.js`), but the grid it places into is invented.
   */
  const TEMPLATE = {
    title: 'From the palette',
    color: '#8b5cf6',
    width: 6,
    height: 4,
    minWidth: 2,
    minHeight: 2,
    content: <span>widget</span>,
  }

  /** Gives a test the ref without re-rendering the tree on every read. */
  function withApi(props = {}) {
    const api = { current: null }

    function Harness() {
      const ref = useRef(null)
      api.current = ref
      return <Host apiRef={ref} {...props} />
    }

    render(<Harness />)
    return api
  }

  it('creates the window, returns its id, and mounts the template s content', () => {
    const api = withApi({ initial: [win('a')] })

    let id
    act(() => {
      id = api.current.current.addFromTemplate(TEMPLATE)
    })

    expect(id).toBe('win_1')
    const added = screen.getByTestId('desktop-window-win_1')
    expect(within(added).getByText('widget')).toBeInTheDocument()
    expect(within(added).getByText('From the palette')).toBeInTheDocument()
  })

  it('places it inside the grid at the size the template asked for', () => {
    const api = withApi({ initial: [] })

    act(() => api.current.current.addFromTemplate(TEMPLATE))

    // 40 × 20 cells at 20px, no offset. Centred on the grid's middle and 6 × 4 in size, so
    // the top-left lands at (17, 8) — this is the geometry's answer, not a rounded guess.
    expect(screen.getByTestId('desktop-window-win_1')).toHaveStyle({
      width: '120px',
      height: '80px',
      left: '340px',
      top: '160px',
    })
  })

  it('does not overlap what is already there', () => {
    // A window across the whole top of the grid: the new one must go below it, not onto it.
    const api = withApi({ initial: [win('band', { x: 0, y: 0, width: 40, height: 6 })] })

    act(() => api.current.current.addFromTemplate(TEMPLATE))

    const top = Number(
      screen.getByTestId('desktop-window-win_1').style.top.replace('px', ''),
    )
    expect(top).toBeGreaterThanOrEqual(6 * 20)
  })

  it('refuses, and says so with null, when the grid is full', () => {
    // One window covering all 40 × 20 cells, with a minimum that keeps it there. The
    // template's own minimum is 2 × 2, so there is genuinely nowhere legal for it.
    const api = withApi({
      initial: [win('full', { width: 40, height: 20, minWidth: 40, minHeight: 20 })],
    })

    let id
    act(() => {
      id = api.current.current.addFromTemplate(TEMPLATE)
    })

    expect(id).toBeNull()
    expect(screen.queryByTestId('desktop-window-win_1')).not.toBeInTheDocument()
  })

  it('refuses while the desktop is unmeasured (REAL — this is what jsdom always reports)', () => {
    const api = { current: null }

    function Harness() {
      const ref = useRef(null)
      api.current = ref
      return (
        <BinPackingLayout
          ref={ref}
          windows={[]}
          onWindowsChange={() => {}}
          useContainerSize={fixedSize(0, 0)}
        />
      )
    }

    render(<Harness />)

    let id
    act(() => {
      id = api.current.current.addFromTemplate(TEMPLATE)
    })

    expect(id).toBeNull()
  })

  it('refuses while a window is fullscreen, exactly as a drop does', async () => {
    // The arrangement underneath a fullscreen window is frozen — the reflow effect declines
    // to run while `fullscreenId` is set — so a window added now would be packed against a
    // grid nobody is maintaining, behind an overlay that swallows its controls.
    const user = userEvent.setup()
    const api = withApi({ initial: [win('a')] })

    await user.click(screen.getByRole('button', { name: 'Fill the desktop' }))

    let id
    act(() => {
      id = api.current.current.addFromTemplate(TEMPLATE)
    })

    expect(id).toBeNull()
    expect(screen.queryByTestId('desktop-window-win_1')).not.toBeInTheDocument()
  })

  it('refuses a missing template rather than creating an untitled window (REAL)', () => {
    const api = withApi({ initial: [] })

    let id
    act(() => {
      id = api.current.current.addFromTemplate(null)
    })

    expect(id).toBeNull()
    expect(screen.queryByTestId('desktop-window-win_1')).not.toBeInTheDocument()
  })

  it('gives each added window its own id, so two of one widget are two windows', () => {
    const api = withApi({ initial: [] })

    act(() => api.current.current.addFromTemplate(TEMPLATE))
    act(() => api.current.current.addFromTemplate(TEMPLATE))

    expect(screen.getByTestId('desktop-window-win_1')).toBeInTheDocument()
    expect(screen.getByTestId('desktop-window-win_2')).toBeInTheDocument()
  })
})
