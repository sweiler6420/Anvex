import { useLayoutEffect, useState } from 'react'

/**
 * Measure an element with a `ResizeObserver`, coalesced to one update per frame (ANV-33).
 *
 * Replaces `GridManager.createResizeObserver`, which was the one part of the old
 * `GridManager` that touched the DOM — a "debounced" observer sitting in a module beside two
 * pure geometry functions. It is a hook here because that is what it always was: a
 * subscription with a lifetime tied to a component.
 *
 * ## What was wrong with the original
 *
 *  - **The debounce was not a debounce.** A burst of resize events took the leading edge
 *    immediately and then scheduled the trailing one on `requestAnimationFrame` — the next
 *    frame, ~16 ms, not the `debounceMs` the caller passed. The 60 that
 *    `BinPackingLayout` passed was doing nothing.
 *  - **`disconnect()` did not cancel the pending frame.** The function returned the raw
 *    `ResizeObserver`, so the caller's cleanup stopped the observer and left a scheduled
 *    callback holding a `setState` for a component that was about to unmount. React 18 does
 *    not warn about that, so it was silent.
 *
 * Both are fixed. The behaviour that is *kept* is the important one: coalescing. A resize
 * produces a flood of entries, each of which would otherwise re-derive the grid, re-run the
 * packer over every window and re-render the whole desktop. One update per frame is the
 * correct granularity for something whose output is pixels, and it is simpler than a timed
 * debounce as well as more honest about what it does.
 *
 * ## Under jsdom
 *
 * There is no layout and no `ResizeObserver`; `src/test/setup.js` installs an inert stub so
 * the import does not throw. This hook therefore reports `{width: 0, height: 0}` for the
 * whole of a test run, and `BinPackingLayout` renders an unmeasured, empty container —
 * which is the truth, not a bug. See the ANV-33 report on which tests prove behaviour and
 * which prove wiring.
 *
 * @param {React.RefObject<Element>} ref the element to measure
 * @returns {{width: number, height: number}} the content box, floored to whole pixels
 */
export default function useContainerSize(ref) {
  const [size, setSize] = useState({ width: 0, height: 0 })

  useLayoutEffect(() => {
    const element = ref.current
    if (!element || typeof ResizeObserver === 'undefined') return undefined

    let frame = null
    let latest = null

    const observer = new ResizeObserver((entries) => {
      const entry = entries[entries.length - 1]
      if (!entry) return
      latest = entry.contentRect
      if (frame !== null) return
      frame = requestAnimationFrame(() => {
        frame = null
        // `latest` is read inside the frame, not captured when it was scheduled, so a
        // burst of entries collapses to the *last* measurement rather than the first.
        setSize((previous) => {
          const next = {
            width: Math.floor(latest.width),
            height: Math.floor(latest.height),
          }
          // Bail out of the render when nothing moved. A `ResizeObserver` fires on the
          // initial observe and on any scroll-driven reflow, and an equal-but-new object
          // would re-run the packer for a size that did not change.
          return previous.width === next.width && previous.height === next.height
            ? previous
            : next
        })
      })
    })

    observer.observe(element)

    return () => {
      observer.disconnect()
      if (frame !== null) cancelAnimationFrame(frame)
    }
  }, [ref])

  return size
}
