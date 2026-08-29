import { act, render, screen } from '@testing-library/react'
import { useRef } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import useContainerSize from './useContainerSize'

/**
 * ANV-33 — the container measurement.
 *
 * ## What is real here and what is not
 *
 * The **sizes are fabricated.** jsdom has no box model, so no `contentRect` in this file was
 * measured; every number below was typed by hand. Nothing here is evidence that
 * `BinPackingLayout` measures its panel correctly in a browser.
 *
 * What *is* real is everything between the observer and the state: that exactly one observer
 * is created and connected, that a burst of entries collapses into a single update carrying
 * the **last** measurement rather than the first, that an unchanged size does not re-render,
 * and that unmounting disconnects **and cancels the frame that was already scheduled** — the
 * leak the original had, where a callback survived `disconnect()` and set state on a
 * component that was gone.
 *
 * Those are claims about this hook's own logic, and each of them fails if the corresponding
 * line is removed. They are worth having; they are not worth mistaking for a layout test.
 */

/** A `ResizeObserver` a test can drive, replacing `setup.js`'s inert one for this file. */
class DriveableResizeObserver {
  static instances = []

  constructor(callback) {
    this.callback = callback
    this.targets = []
    this.disconnected = false
    DriveableResizeObserver.instances.push(this)
  }

  observe(target) {
    this.targets.push(target)
  }

  unobserve(target) {
    this.targets = this.targets.filter((t) => t !== target)
  }

  disconnect() {
    this.disconnected = true
    this.targets = []
  }

  /** Deliver one or more entries, as a real observer would in a single batch. */
  emit(...rects) {
    this.callback(
      rects.map((contentRect) => ({ contentRect, target: this.targets[0] })),
      this,
    )
  }
}

/** Run every scheduled animation frame. jsdom implements rAF on a timer, so this drives it. */
const flushFrames = async () => {
  await act(async () => {
    await new Promise((resolve) => requestAnimationFrame(resolve))
  })
}

function Probe() {
  const ref = useRef(null)
  const size = useContainerSize(ref)
  return (
    <div ref={ref} data-testid="probe">
      {size.width}×{size.height}
    </div>
  )
}

let originalResizeObserver

beforeEach(() => {
  originalResizeObserver = globalThis.ResizeObserver
  DriveableResizeObserver.instances = []
  globalThis.ResizeObserver = DriveableResizeObserver
})

afterEach(() => {
  globalThis.ResizeObserver = originalResizeObserver
})

const observer = () => DriveableResizeObserver.instances.at(-1)

describe('useContainerSize', () => {
  it('reports nothing before anything has been observed — the jsdom default', () => {
    render(<Probe />)

    expect(screen.getByTestId('probe')).toHaveTextContent('0×0')
  })

  it('observes the element the ref points at, exactly once', () => {
    render(<Probe />)

    expect(DriveableResizeObserver.instances).toHaveLength(1)
    expect(observer().targets).toEqual([screen.getByTestId('probe')])
  })

  it('reports a size on the next frame, not synchronously', async () => {
    render(<Probe />)

    act(() => observer().emit({ width: 800, height: 400 }))
    expect(screen.getByTestId('probe')).toHaveTextContent('0×0')

    await flushFrames()

    expect(screen.getByTestId('probe')).toHaveTextContent('800×400')
  })

  it('floors a fractional measurement, so no window is placed on half a pixel', async () => {
    render(<Probe />)

    act(() => observer().emit({ width: 800.9, height: 400.4 }))
    await flushFrames()

    expect(screen.getByTestId('probe')).toHaveTextContent('800×400')
  })

  it('takes the LAST entry of a batch, not the first', async () => {
    render(<Probe />)

    act(() => observer().emit({ width: 100, height: 100 }, { width: 640, height: 480 }))
    await flushFrames()

    expect(screen.getByTestId('probe')).toHaveTextContent('640×480')
  })

  it('collapses a burst of callbacks into one update carrying the latest size', async () => {
    render(<Probe />)

    act(() => {
      observer().emit({ width: 100, height: 100 })
      observer().emit({ width: 200, height: 200 })
      observer().emit({ width: 300, height: 300 })
    })
    await flushFrames()

    // The intermediate sizes never reached the component: only one frame was scheduled, and
    // it read the newest value rather than the one captured when it was booked.
    expect(screen.getByTestId('probe')).toHaveTextContent('300×300')
  })

  it('reports a later change after the frame has run', async () => {
    render(<Probe />)

    act(() => observer().emit({ width: 100, height: 100 }))
    await flushFrames()
    act(() => observer().emit({ width: 500, height: 250 }))
    await flushFrames()

    expect(screen.getByTestId('probe')).toHaveTextContent('500×250')
  })

  it('ignores an empty batch rather than throwing', async () => {
    render(<Probe />)

    act(() => observer().emit())
    await flushFrames()

    expect(screen.getByTestId('probe')).toHaveTextContent('0×0')
  })

  it('hands back the same object when the size has not changed', async () => {
    const sizes = []

    function Recorder() {
      const ref = useRef(null)
      sizes.push(useContainerSize(ref))
      return <div ref={ref} />
    }

    render(<Recorder />)
    act(() => observer().emit({ width: 800, height: 400 }))
    await flushFrames()
    const measured = sizes.at(-1)
    act(() => observer().emit({ width: 800, height: 400 }))
    await flushFrames()

    // A new-but-equal object every time an observer fires would invalidate any consumer
    // memoising on it — and a `ResizeObserver` fires on the initial observe and on reflows
    // that changed nothing. Returning the identical object is what stops a re-pack for a
    // size that did not move.
    expect(sizes.at(-1)).toBe(measured)
    expect(measured).toEqual({ width: 800, height: 400 })
  })

  it('disconnects on unmount', () => {
    const { unmount } = render(<Probe />)
    const ro = observer()

    unmount()

    expect(ro.disconnected).toBe(true)
  })

  it('cancels a frame that was already scheduled, so nothing fires after unmount', () => {
    // The original returned the raw observer from its factory, so the caller's cleanup
    // stopped the *observer* and left the booked callback alone — a `setState` on a component
    // that no longer exists.
    //
    // This one asserts on the cancellation itself rather than on a symptom, deliberately:
    // React 18 removed the post-unmount `setState` warning, and a `setState` on an unmounted
    // component is otherwise a no-op, so **the leak has no observable effect to assert on**.
    // A test that unmounted, flushed frames and checked the component was gone passes with
    // the cancel deleted — verified by mutation — which makes it worse than no test at all.
    const raf = vi.spyOn(window, 'requestAnimationFrame')
    const cancel = vi.spyOn(window, 'cancelAnimationFrame')
    const { unmount } = render(<Probe />)

    act(() => observer().emit({ width: 800, height: 400 }))
    const scheduled = raf.mock.results.at(-1).value
    unmount()

    expect(cancel).toHaveBeenCalledWith(scheduled)
  })

  it('does nothing at all when the environment has no ResizeObserver', () => {
    delete globalThis.ResizeObserver

    expect(() => render(<Probe />)).not.toThrow()
    expect(screen.getByTestId('probe')).toHaveTextContent('0×0')
  })
})
