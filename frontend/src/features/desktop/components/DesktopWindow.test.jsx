import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import DesktopWindow from './DesktopWindow'

/**
 * ANV-33 — a single window.
 *
 * **These tests prove real behaviour**, and they are the only component tests in the feature
 * that do. `DesktopWindow` takes every dimension as a prop and measures nothing, so jsdom's
 * missing layout costs it nothing: which control fires which callback, whether a press on a
 * control also starts a drag, and what the fullscreen button is called are all claims about
 * markup and event handling, not about pixels.
 *
 * The one thing they cannot check is that the window *looks* right. Inline styles are
 * asserted only where a style carries behaviour (`display: none` for a hidden window).
 */

const renderWindow = (props = {}) =>
  render(
    <DesktopWindow
      id="w1"
      title="Research"
      color="#3b82f6"
      left={40}
      top={20}
      width={200}
      height={160}
      {...props}
    />,
  )

describe('what it renders', () => {
  it('shows the title and the content it was given', () => {
    renderWindow({ children: <p>widget goes here</p> })

    expect(screen.getByText('Research')).toBeInTheDocument()
    expect(screen.getByText('widget goes here')).toBeInTheDocument()
  })

  it('positions and sizes itself from the pixels it was handed', () => {
    renderWindow()

    expect(screen.getByTestId('desktop-window-w1')).toHaveStyle({
      left: '40px',
      top: '20px',
      width: '200px',
      height: '160px',
    })
  })

  it('is hidden with display:none while its drag ghost is on screen', () => {
    renderWindow({ hidden: true })

    expect(screen.getByTestId('desktop-window-w1')).toHaveStyle({ display: 'none' })
  })

  it('is visible when it is not being dragged', () => {
    renderWindow()

    expect(screen.getByTestId('desktop-window-w1')).not.toHaveStyle({ display: 'none' })
  })

  it('offers a resize handle normally', () => {
    renderWindow()

    expect(screen.getByTestId('window-resize-w1')).toBeInTheDocument()
  })

  it('withdraws the resize handle in fullscreen, where there is nothing to resize into', () => {
    renderWindow({ isFullscreen: true })

    expect(screen.queryByTestId('window-resize-w1')).not.toBeInTheDocument()
  })
})

describe('the controls', () => {
  it.each([
    ['Collapse to minimum size', 'onCollapse'],
    ['Grow to fill free space', 'onGrow'],
    ['Fill the desktop', 'onFullscreen'],
    ['Close window', 'onClose'],
  ])('the %s button calls %s with the window id', async (name, handler) => {
    const user = userEvent.setup()
    const spy = vi.fn()
    renderWindow({ [handler]: spy })

    await user.click(screen.getByRole('button', { name }))

    expect(spy).toHaveBeenCalledExactlyOnceWith('w1')
  })

  it('names the fullscreen control for what pressing it does, in both states', () => {
    const { rerender } = renderWindow()

    expect(screen.getByRole('button', { name: 'Fill the desktop' })).toBeInTheDocument()

    rerender(
      <DesktopWindow id="w1" title="Research" left={0} top={0} width={10} height={10} isFullscreen />,
    )

    expect(screen.getByRole('button', { name: 'Exit fullscreen' })).toBeInTheDocument()
  })

  it('gives every control an accessible name — four buttons, four names', () => {
    renderWindow()

    const names = screen
      .getAllByRole('button')
      .map((button) => button.getAttribute('aria-label'))

    expect(names).toEqual([
      'Collapse to minimum size',
      'Grow to fill free space',
      'Fill the desktop',
      'Close window',
    ])
  })

  it('makes every control type="button", so a window inside a form cannot submit it', () => {
    renderWindow()

    for (const button of screen.getAllByRole('button')) {
      expect(button).toHaveAttribute('type', 'button')
    }
  })

  it('does not throw when a control has no handler', async () => {
    const user = userEvent.setup()
    renderWindow()

    await expect(user.click(screen.getByRole('button', { name: 'Close window' }))).resolves.toBeUndefined()
  })
})

describe('starting a drag', () => {
  it('starts one from the header, and says which window', () => {
    const onDragStart = vi.fn()
    renderWindow({ onDragStart })

    fireEvent.mouseDown(screen.getByText('Research'))

    expect(onDragStart).toHaveBeenCalledOnce()
    expect(onDragStart.mock.calls[0][1]).toBe('w1')
  })

  it('does NOT start one from the window body', () => {
    const onDragStart = vi.fn()
    renderWindow({ onDragStart, children: <p>widget goes here</p> })

    fireEvent.mouseDown(screen.getByText('widget goes here'))

    expect(onDragStart).not.toHaveBeenCalled()
  })

  it('does NOT start one from a control — otherwise the click never arrives', () => {
    const onDragStart = vi.fn()
    renderWindow({ onDragStart })

    fireEvent.mouseDown(screen.getByRole('button', { name: 'Close window' }))

    expect(onDragStart).not.toHaveBeenCalled()
  })

  it('does NOT start one from the resize handle', () => {
    const onDragStart = vi.fn()
    renderWindow({ onDragStart })

    fireEvent.mouseDown(screen.getByTestId('window-resize-w1'))

    expect(onDragStart).not.toHaveBeenCalled()
  })

  it('does NOT start one at all while fullscreen', () => {
    const onDragStart = vi.fn()
    renderWindow({ onDragStart, isFullscreen: true })

    fireEvent.mouseDown(screen.getByText('Research'))

    expect(onDragStart).not.toHaveBeenCalled()
  })
})

describe('starting a resize', () => {
  it('starts one from the handle, and says which window', () => {
    const onResizeStart = vi.fn()
    renderWindow({ onResizeStart })

    fireEvent.mouseDown(screen.getByTestId('window-resize-w1'))

    expect(onResizeStart).toHaveBeenCalledOnce()
    expect(onResizeStart.mock.calls[0][1]).toBe('w1')
  })

  it('stops the press reaching the drag listener above it', () => {
    const onDragStart = vi.fn()
    const onResizeStart = vi.fn()
    renderWindow({ onDragStart, onResizeStart })

    fireEvent.mouseDown(screen.getByTestId('window-resize-w1'))

    expect(onResizeStart).toHaveBeenCalledOnce()
    expect(onDragStart).not.toHaveBeenCalled()
  })
})
