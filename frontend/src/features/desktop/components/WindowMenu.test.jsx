import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { clearPendingWindow, peekPendingWindow } from '../dragPayload'
import WindowMenu from './WindowMenu'

/**
 * ANV-33 — the window palette.
 *
 * **These tests prove real behaviour.** Nothing here is measured: what a `dragstart` puts in
 * the drag payload, and what a `dragend` leaves behind, are the whole of this component's
 * job.
 *
 * The `dataTransfer` object is hand-made, because jsdom's `DataTransfer` is not constructible
 * and `fireEvent.dragStart` will not invent one. That is a genuine fixture — but the thing
 * being asserted is what the component wrote into `../dragPayload`, which is not fabricated.
 */

const ITEMS = [
  { name: 'Blue', color: '#3b82f6', window: { title: 'Blue window', width: 10, height: 8 } },
  { name: 'Green', color: '#22c55e', window: { title: 'Green window', width: 10, height: 8 } },
]

/** Enough of `DataTransfer` for the component: `setData` and a settable `effectAllowed`. */
const makeDataTransfer = () => {
  const store = new Map()
  return {
    effectAllowed: 'uninitialized',
    setData: (type, value) => store.set(type, value),
    getData: (type) => store.get(type) ?? '',
  }
}

afterEach(clearPendingWindow)

describe('what it renders', () => {
  it('shows the prompt and one chip per item', () => {
    render(<WindowMenu items={ITEMS} />)

    expect(screen.getByText('Drag into grid:')).toBeInTheDocument()
    expect(within(screen.getByRole('list')).getAllByRole('listitem')).toHaveLength(2)
  })

  it('marks the chips up as a list, and labels the list with the prompt', () => {
    render(<WindowMenu items={ITEMS} />)

    expect(screen.getByRole('list', { name: 'Drag into grid:' })).toBeInTheDocument()
  })

  it('paints each chip in its own colour', () => {
    render(<WindowMenu items={ITEMS} />)

    expect(screen.getByTestId('window-menu-item-Blue')).toHaveStyle({
      backgroundColor: '#3b82f6',
    })
  })

  it('renders an empty list rather than failing when there is nothing to offer', () => {
    render(<WindowMenu items={[]} />)

    expect(within(screen.getByRole('list')).queryAllByRole('listitem')).toHaveLength(0)
  })

  it('takes a caller-supplied prompt', () => {
    render(<WindowMenu items={ITEMS} label="Add a widget:" />)

    expect(screen.getByRole('list', { name: 'Add a widget:' })).toBeInTheDocument()
  })

  it('makes every chip draggable', () => {
    render(<WindowMenu items={ITEMS} />)

    for (const chip of screen.getAllByRole('listitem')) {
      expect(chip).toHaveAttribute('draggable', 'true')
    }
  })
})

describe('the drag hand-off', () => {
  it('arms the pending window with the full template, not the label', () => {
    render(<WindowMenu items={ITEMS} />)

    fireEvent.dragStart(screen.getByTestId('window-menu-item-Green'), {
      dataTransfer: makeDataTransfer(),
    })

    // The template carries the React content a `DataTransfer` could never hold. That is the
    // entire reason `dragPayload` exists.
    expect(peekPendingWindow()).toBe(ITEMS[1].window)
  })

  it('also puts a serialisable label on the DataTransfer, for anything else listening', () => {
    const dataTransfer = makeDataTransfer()
    render(<WindowMenu items={ITEMS} />)

    fireEvent.dragStart(screen.getByTestId('window-menu-item-Blue'), { dataTransfer })

    expect(JSON.parse(dataTransfer.getData('application/json'))).toEqual({ name: 'Blue' })
  })

  it('declares the drag a copy, so the chip stays in the palette', () => {
    const dataTransfer = makeDataTransfer()
    render(<WindowMenu items={ITEMS} />)

    fireEvent.dragStart(screen.getByTestId('window-menu-item-Blue'), { dataTransfer })

    expect(dataTransfer.effectAllowed).toBe('copy')
  })

  it('falls back to text/plain when the browser refuses the JSON type', () => {
    const dataTransfer = makeDataTransfer()
    dataTransfer.setData = (type, value) => {
      if (type === 'application/json') throw new Error('unsupported type')
      dataTransfer.plain = value
    }
    render(<WindowMenu items={ITEMS} />)

    fireEvent.dragStart(screen.getByTestId('window-menu-item-Blue'), { dataTransfer })

    expect(JSON.parse(dataTransfer.plain)).toEqual({ name: 'Blue' })
    // And the payload is armed either way — the label is the part that may fail.
    expect(peekPendingWindow()).toBe(ITEMS[0].window)
  })

  it('disarms the payload when the drag ends, dropped or cancelled', () => {
    render(<WindowMenu items={ITEMS} />)
    const chip = screen.getByTestId('window-menu-item-Blue')

    fireEvent.dragStart(chip, { dataTransfer: makeDataTransfer() })
    expect(peekPendingWindow()).not.toBeNull()

    fireEvent.dragEnd(chip)

    expect(peekPendingWindow()).toBeNull()
  })

  it('replaces the payload when a second chip is picked up', () => {
    render(<WindowMenu items={ITEMS} />)

    fireEvent.dragStart(screen.getByTestId('window-menu-item-Blue'), {
      dataTransfer: makeDataTransfer(),
    })
    fireEvent.dragStart(screen.getByTestId('window-menu-item-Green'), {
      dataTransfer: makeDataTransfer(),
    })

    expect(peekPendingWindow()).toBe(ITEMS[1].window)
  })
})

describe('click-to-add (ANV-35) — the affordance the drag never had', () => {
  it('leaves the chip a plain list item when no handler is given', () => {
    // The default is byte-for-byte ANV-33's behaviour: no button, nothing focusable, no new
    // name in the accessibility tree.
    render(<WindowMenu items={ITEMS} />)

    expect(screen.queryAllByRole('button')).toHaveLength(0)
  })

  it('turns each chip into a button that names what pressing it does', () => {
    render(<WindowMenu items={ITEMS} onAdd={() => {}} />)

    // "Add Blue", not "Blue": a control has to say what it does, and the visible label is a
    // substring of the accessible name (WCAG 2.5.3).
    expect(screen.getByRole('button', { name: 'Add Blue' })).toHaveTextContent('Blue')
    expect(screen.getByRole('button', { name: 'Add Green' })).toBeInTheDocument()
  })

  it('hands the whole item back, not just its name', async () => {
    const user = userEvent.setup()
    const onAdd = vi.fn()
    render(<WindowMenu items={ITEMS} onAdd={onAdd} />)

    await user.click(screen.getByRole('button', { name: 'Add Green' }))

    // The window template is the useful half; a name would make the caller look it up again.
    expect(onAdd).toHaveBeenCalledWith(ITEMS[1])
  })

  it('is operable from the keyboard, which is the entire point', async () => {
    const user = userEvent.setup()
    const onAdd = vi.fn()
    render(<WindowMenu items={ITEMS} onAdd={onAdd} />)

    await user.tab()
    await user.keyboard('{Enter}')

    expect(onAdd).toHaveBeenCalledWith(ITEMS[0])
  })

  it('keeps the list markup and the draggable chip exactly as they were', () => {
    render(<WindowMenu items={ITEMS} onAdd={() => {}} />)

    for (const chip of screen.getAllByRole('listitem')) {
      expect(chip).toHaveAttribute('draggable', 'true')
    }
    expect(within(screen.getByRole('list')).getAllByRole('listitem')).toHaveLength(2)
  })

  it('still arms the payload when the drag starts on the button itself', () => {
    // A form control inside a `draggable` ancestor swallows the gesture in several browsers,
    // so the button carries `draggable` and the handlers too. Remove either and the mouse
    // users the palette was built for lose it silently to make room for the keyboard.
    const dataTransfer = makeDataTransfer()
    render(<WindowMenu items={ITEMS} onAdd={() => {}} />)

    const button = screen.getByRole('button', { name: 'Add Blue' })
    expect(button).toHaveAttribute('draggable', 'true')

    fireEvent.dragStart(button, { dataTransfer })
    expect(peekPendingWindow()).toBe(ITEMS[0].window)

    fireEvent.dragEnd(button)
    expect(peekPendingWindow()).toBeNull()
  })
})
