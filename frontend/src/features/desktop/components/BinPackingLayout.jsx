import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react'

import { clearPendingWindow, peekPendingWindow } from '../dragPayload'
import {
  cellsToPixels,
  computeGridSpecForWindows,
  isGridReady as gridIsReady,
  pixelToCell,
} from '../geometry/grid'
import { reflowScaleByOverlap } from '../geometry/reflow'
import { computeAllowedSize, findNearestFreePosition, fitCenteredRect } from '../geometry/rects'
import useContainerSizeHook from '../hooks/useContainerSize'
import {
  closeWindow,
  collapseToMinimum,
  growToFill,
  moveWindow,
  resizeWindow,
  restoreWindow,
} from '../geometry/windowOps'
import { nextWindowId } from '../windowIds'
import DesktopWindow from './DesktopWindow'

/**
 * The bin-packing desktop: a grid of cells, a set of non-overlapping windows on it, and the
 * gestures that move and resize them (ANV-33).
 *
 * Ported from `AverageInvestorWeb/src/components/shared/binpacking/BinPackingLayout.jsx`
 * (671 lines). **Everything geometric has moved out** — into `../geometry/`, where it is a
 * function of numbers with no React and no DOM, and where it is tested exhaustively. What is
 * left here is what genuinely is stateful: a measurement, a grid derived from it, the
 * arrangement, three transient gestures, and the fullscreen slot.
 *
 * ## The model
 *
 * A **window** is `{id, title, color, x, y, width, height, minWidth, minHeight, content}`,
 * with `x`/`y`/`width`/`height` in **grid cells**, never pixels. Pixels appear in exactly
 * three places: the container's measured size, `cellsToPixels` on the way to a style, and
 * `pixelToCell` on the way in from a pointer event.
 *
 * The invariant the whole system exists to keep is that **no two windows overlap and none
 * escapes the grid**. Every gesture proposes a rectangle and asks `../geometry/rects` whether
 * it is legal before committing it; a resize that would collide shrinks instead, and a drag
 * that would collide either finds the nearest free cell or refuses.
 *
 * ## Controlled, with a local copy
 *
 * `windows` is the source of truth and `onWindowsChange` is how it moves. The component
 * keeps its own `packed` copy because a reflow happens *during* layout, before the parent
 * has had a chance to re-render, and rendering the stale prop for a frame is a visible jump.
 * `onWindowsChange` is always called with an **updater function**, so the prop is a React
 * state setter: `<BinPackingLayout windows={w} onWindowsChange={setW} />`. That was the old
 * contract, undocumented, and passing a plain `(next) => …` callback instead silently loses
 * every drag.
 *
 * ## The one thing to know before writing a test
 *
 * Under jsdom there is no layout and no `ResizeObserver`, so `useContainerSize` reports 0×0,
 * the grid is never ready, and this component renders an **empty container**. That is
 * correct, not broken: a window system that measures its container measures nothing when
 * nothing has a size. Tests that need a grid install a `ResizeObserver` fixture and feed it
 * fabricated dimensions, which proves the wiring between the measurement and the geometry
 * and proves nothing about the geometry itself — that is what `../geometry/*.test.js` is for.
 */

const GRID_LINE_COLOR = '#3b82f6'

const BinPackingLayout = forwardRef(function BinPackingLayout(
  {
    cellSize = 20,
    minGridWidth = 4,
    minGridHeight = 3,
    centerGrid = true,
    allowOverflowScroll = true,
    showGridLines = true,
    windows = [],
    onWindowsChange,
    useContainerSize = useContainerSizeHook,
  },
  ref,
) {
  const containerRef = useRef(null)
  /**
   * The measurement seam, and the only place a test is allowed to lie.
   *
   * jsdom has no layout: `getBoundingClientRect()` is 0×0, `offsetWidth` is 0, and there is
   * no `ResizeObserver` at all. A desktop that measures its container therefore measures
   * nothing, and every test of anything downstream of the measurement has to fabricate one.
   *
   * The fabrication is a **prop with the real hook as its default** rather than a global
   * `ResizeObserver` mock, for one reason: it is visible. A test that passes
   * `useContainerSize={() => ({width: 800, height: 400})}` is unmistakably asserting against
   * a number it invented, and nothing it proves can be mistaken for a claim about layout.
   * The geometry those numbers feed is tested for real, with no DOM at all, in
   * `../geometry/*.test.js`.
   */
  const containerSize = useContainerSize(containerRef)

  // The arrangement as rendered. `packedRef` shadows it so a gesture handler bound to
  // `window` reads the current arrangement rather than the one captured when it was bound.
  const [packed, setPackedState] = useState(windows)
  const packedRef = useRef(windows)

  const [gesture, setGesture] = useState(null)

  // The two transient previews are mirrored into refs. They change on every `mousemove`, and
  // the `mouseup` handler that consumes them is bound once for the whole gesture — listing
  // them as effect dependencies would tear the listeners down and rebuild them sixty times a
  // second, and reading them from the closure instead would consume the value they had when
  // the gesture started.
  const [dragGhost, setDragGhostState] = useState(null)
  const dragGhostRef = useRef(null)
  const setDragGhost = useCallback((next) => {
    dragGhostRef.current = next
    setDragGhostState(next)
  }, [])

  const [resizePreview, setResizePreviewState] = useState(null)
  const resizePreviewRef = useRef(null)
  const setResizePreview = useCallback((next) => {
    resizePreviewRef.current = next
    setResizePreviewState(next)
  }, [])

  const [activeId, setActiveId] = useState(null)
  const [fullscreenId, setFullscreenId] = useState(null)
  const [fullscreenPrev, setFullscreenPrev] = useState(null)

  // Held in a ref so the gesture effects do not re-subscribe every time the parent
  // re-renders with a new inline callback.
  const onWindowsChangeRef = useRef(onWindowsChange)
  onWindowsChangeRef.current = onWindowsChange

  /** Publish a new arrangement: render it, remember it, and tell the owner. */
  const commit = useCallback((next) => {
    packedRef.current = next
    setPackedState(next)
    onWindowsChangeRef.current?.(() => next)
  }, [])

  // The prop is the source of truth; adopt it whenever it changes identity. The parent is
  // normally echoing back what `commit` just sent, in which case this is a no-op render.
  useEffect(() => {
    packedRef.current = windows
    setPackedState(windows)
  }, [windows])

  /**
   * The grid, derived rather than stored.
   *
   * The original computed this in a `useEffect` into state, with a dependency array that
   * listed the container size and the *aggregate* minimum footprint but **not** `windows` —
   * so rearranging windows without changing the sum of their minimum widths left the grid at
   * its old size. Deriving it removes the effect, the state and the stale dependency
   * together.
   *
   * The feedback loop this creates is bounded, and the argument matters because an unbounded
   * one would be an infinite render: the grid grows to fit the arrangement, and reflowing
   * into a grid leaves every band's minimum widths summing to no more than that grid's
   * columns. So each pass makes the required columns and rows no larger than the last, and
   * both are bounded below by what the container can show. The sequence is non-increasing
   * and bounded, so it settles.
   */
  const gridSpec = useMemo(
    () =>
      computeGridSpecForWindows({
        containerWidth: containerSize.width,
        containerHeight: containerSize.height,
        cellSize,
        minCols: minGridWidth,
        minRows: minGridHeight,
        windows: packed,
      }),
    [containerSize.width, containerSize.height, cellSize, minGridWidth, minGridHeight, packed],
  )

  const isReady = gridIsReady(gridSpec, cellSize)
  const { cols, rows } = gridSpec

  /**
   * Reflow when — and only when — the grid changes shape.
   *
   * The guard is a ref rather than a dependency array because the effect *writes* the
   * arrangement it reads: listing `packed` would make every reflow trigger another one.
   * Comparing against the last grid this actually ran for is the honest version of the
   * original's under-specified `[isGridReady, cols, rows, fullscreenId]`.
   *
   * Reflow is suspended while a window is fullscreen: the arrangement underneath is frozen,
   * not rearranged behind the fullscreen window's back.
   */
  const lastReflowGrid = useRef({ cols: 0, rows: 0 })
  useLayoutEffect(() => {
    if (!isReady || fullscreenId) return
    if (lastReflowGrid.current.cols === cols && lastReflowGrid.current.rows === rows) return
    lastReflowGrid.current = { cols, rows }
    commit(reflowScaleByOverlap({ windows: packedRef.current, cols, rows }).next)
  }, [isReady, cols, rows, fullscreenId, commit])

  /**
   * A grid that changes size while a window is fullscreen ends the fullscreen.
   *
   * The fullscreen window is drawn at the grid's full extent, so a grid that resized under it
   * would leave it the wrong size with no gesture to correct it — and the arrangement beneath
   * was never reflowed for the new grid, because the effect above declines to while
   * `fullscreenId` is set. Dropping out is what lets both catch up.
   */
  const prevGridRef = useRef({ cols: 0, rows: 0 })
  useEffect(() => {
    const changed = cols !== prevGridRef.current.cols || rows !== prevGridRef.current.rows
    prevGridRef.current = { cols, rows }
    if (!changed || !fullscreenId) return
    if (fullscreenPrev) commit(restoreWindow(packedRef.current, fullscreenId, fullscreenPrev))
    setFullscreenId(null)
    setFullscreenPrev(null)
  }, [cols, rows, fullscreenId, fullscreenPrev, commit])

  useImperativeHandle(
    ref,
    () => ({
      addWindow: (win) => onWindowsChangeRef.current?.((prev) => [...prev, win]),
      removeWindow: (id) =>
        onWindowsChangeRef.current?.((prev) => prev.filter((w) => w.id !== id)),
      replaceWindows: (next) => onWindowsChangeRef.current?.(() => next),
    }),
    [],
  )

  /** Where the pointer is, in fractional grid cells. */
  const pointerCell = useCallback(
    (event) => {
      const element = containerRef.current
      if (!element) return null
      return pixelToCell({
        clientX: event.clientX,
        clientY: event.clientY,
        containerRect: element.getBoundingClientRect(),
        scrollLeft: element.scrollLeft || 0,
        scrollTop: element.scrollTop || 0,
        gridSpec,
        cellSize,
      })
    },
    [gridSpec, cellSize],
  )

  // ---------------------------------------------------------------------------------------
  // Dragging and resizing an existing window
  // ---------------------------------------------------------------------------------------

  const onDragStart = useCallback(
    (event, id) => {
      if (!isReady) return
      setActiveId(id)
      setGesture({ kind: 'drag', id })
      event.preventDefault()
    },
    [isReady],
  )

  const onResizeStart = useCallback(
    (event, id) => {
      if (!isReady) return
      const target = packedRef.current.find((w) => w.id === id)
      if (!target) return
      setActiveId(id)
      setGesture({
        kind: 'resize',
        id,
        startX: event.clientX,
        startY: event.clientY,
        originWidth: target.width * cellSize,
        originHeight: target.height * cellSize,
      })
      event.preventDefault()
    },
    [isReady, cellSize],
  )

  /**
   * The live half of a drag or a resize.
   *
   * Both gestures render their preview **from state**, not by writing to the element's
   * `style`. The original resized by assigning `el.style.width` on every `mousemove` and then
   * read the size back out of `getBoundingClientRect()` on `mouseup` to decide what to
   * commit — a round trip through the DOM whose only purpose was to remember a number the
   * code had just calculated, and the reason the commit path could not be tested without a
   * browser that lays out. The pixels are identical either way.
   */
  useEffect(() => {
    if (!gesture) return undefined

    const onMove = (event) => {
      const moving = packedRef.current.find((w) => w.id === gesture.id)
      if (!moving) return
      const bounds = { cols, rows, placed: packedRef.current, excludeId: gesture.id }

      if (gesture.kind === 'drag') {
        const cell = pointerCell(event)
        if (!cell) return
        // The requested size *is* the minimum: a window being dragged never shrinks to fit
        // a gap. If it does not fit under the cursor there is no ghost and no drop.
        const fit = fitCenteredRect(
          {
            centerX: cell.cellX,
            centerY: cell.cellY,
            width: moving.width,
            height: moving.height,
            minWidth: moving.width,
            minHeight: moving.height,
          },
          bounds,
        )
        setDragGhost(fit ? { ...fit, color: moving.color } : null)
        return
      }

      const requestedWidth = Math.max(
        1,
        Math.round(Math.max(1, gesture.originWidth + (event.clientX - gesture.startX)) / cellSize),
      )
      const requestedHeight = Math.max(
        1,
        Math.round(Math.max(1, gesture.originHeight + (event.clientY - gesture.startY)) / cellSize),
      )
      const allowed = computeAllowedSize(
        {
          x: moving.x,
          y: moving.y,
          width: requestedWidth,
          height: requestedHeight,
          minWidth: Math.max(1, moving.minWidth || 1),
          minHeight: Math.max(1, moving.minHeight || 1),
        },
        bounds,
      )
      setResizePreview({ id: gesture.id, ...allowed })
    }

    const onUp = (event) => {
      const current = packedRef.current
      const target = current.find((w) => w.id === gesture.id)
      const ghost = dragGhostRef.current
      const preview = resizePreviewRef.current

      if (target && gesture.kind === 'drag') {
        if (ghost) {
          commit(moveWindow(current, target.id, { x: ghost.x, y: ghost.y }))
        } else {
          // No ghost: the cursor is somewhere the window does not fit. Fall back to the
          // nearest free cell to where it was pointing, and leave the window where it is if
          // there is not one.
          const cell = pointerCell(event)
          if (cell) {
            const spot = findNearestFreePosition(
              {
                x: Math.round(cell.cellX - target.width / 2),
                y: Math.round(cell.cellY - target.height / 2),
                width: target.width,
                height: target.height,
              },
              { cols, rows, placed: current, excludeId: target.id },
            )
            if (spot.found) commit(moveWindow(current, target.id, spot))
          }
        }
      } else if (target && gesture.kind === 'resize' && preview) {
        const size = { width: preview.width, height: preview.height }
        const spot = findNearestFreePosition(
          { x: target.x, y: target.y, ...size },
          { cols, rows, placed: current, excludeId: target.id },
        )
        commit(resizeWindow(current, target.id, { x: spot.x, y: spot.y, ...size }))
      }

      setActiveId(null)
      setGesture(null)
      setDragGhost(null)
      setResizePreview(null)
    }

    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [gesture, cols, rows, cellSize, pointerCell, commit, setDragGhost, setResizePreview])

  // ---------------------------------------------------------------------------------------
  // Dragging a new window in from the menu
  // ---------------------------------------------------------------------------------------

  /**
   * Where a template dropped at the cursor would land, or `null`.
   *
   * The template comes from `../dragPayload` rather than `dataTransfer`, because the drag
   * data store is in protected mode during `dragover` and a window template contains React
   * nodes that could not be serialised into it anyway.
   */
  const previewForPointer = useCallback(
    (event, template) => {
      const cell = pointerCell(event)
      if (!cell) return null
      return fitCenteredRect(
        {
          centerX: cell.cellX,
          centerY: cell.cellY,
          width: template.width,
          height: template.height,
          minWidth: Math.max(1, template.minWidth || 1),
          minHeight: Math.max(1, template.minHeight || 1),
        },
        { cols, rows, placed: packedRef.current },
      )
    },
    [pointerCell, cols, rows],
  )

  const handleDragOver = useCallback(
    (event) => {
      if (!isReady || fullscreenId) return
      const template = peekPendingWindow()
      if (!template) {
        // Somebody else's drag. Say so, so the browser shows a "no drop" cursor and the
        // event goes on to whatever else wants it.
        setDragGhost(null)
        event.dataTransfer.dropEffect = 'none'
        return
      }
      event.preventDefault()
      event.dataTransfer.dropEffect = 'copy'
      const fit = previewForPointer(event, template)
      setDragGhost(fit ? { ...fit, color: template.color } : null)
    },
    [isReady, fullscreenId, previewForPointer, setDragGhost],
  )

  const handleDragLeave = useCallback(() => setDragGhost(null), [setDragGhost])

  const handleDrop = useCallback(
    (event) => {
      if (!isReady || fullscreenId) return
      const template = peekPendingWindow()
      if (!template) return
      event.preventDefault()

      const fit = dragGhost ?? previewForPointer(event, template)
      setDragGhost(null)
      clearPendingWindow()
      if (!fit) return

      const spot = findNearestFreePosition(fit, { cols, rows, placed: packedRef.current })
      commit([
        ...packedRef.current,
        {
          ...template,
          id: nextWindowId(),
          x: Math.max(0, Math.min(cols - fit.width, spot.x)),
          y: Math.max(0, Math.min(rows - fit.height, spot.y)),
          width: fit.width,
          height: fit.height,
        },
      ])
    },
    [isReady, fullscreenId, dragGhost, previewForPointer, cols, rows, commit, setDragGhost],
  )

  // ---------------------------------------------------------------------------------------
  // The window controls
  // ---------------------------------------------------------------------------------------

  const handleCollapse = useCallback(
    (id) => commit(collapseToMinimum(packedRef.current, id)),
    [commit],
  )

  const handleGrow = useCallback(
    (id) => commit(growToFill(packedRef.current, id, { cols, rows })),
    [commit, cols, rows],
  )

  const handleFullscreen = useCallback(
    (id) => {
      if (fullscreenId === id) {
        if (fullscreenPrev) commit(restoreWindow(packedRef.current, id, fullscreenPrev))
        setFullscreenId(null)
        setFullscreenPrev(null)
        return
      }
      // A second window cannot take over the screen from the first; the overlay makes its
      // controls unreachable anyway, and swapping would strand the first one's saved rect.
      if (fullscreenId) return
      const target = packedRef.current.find((w) => w.id === id)
      if (!target) return
      setFullscreenPrev({
        x: target.x,
        y: target.y,
        width: target.width,
        height: target.height,
      })
      setFullscreenId(id)
    },
    [fullscreenId, fullscreenPrev, commit],
  )

  const handleClose = useCallback(
    (id) => {
      if (fullscreenId === id) {
        // No restore: the geometry is about to stop existing.
        setFullscreenId(null)
        setFullscreenPrev(null)
      }
      commit(closeWindow(packedRef.current, id))
    },
    [fullscreenId, commit],
  )

  // ---------------------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------------------

  const containerStyle = useMemo(
    () => ({
      position: 'relative',
      width: '100%',
      height: '100%',
      overflowX: gridSpec.overflowX && allowOverflowScroll ? 'auto' : 'hidden',
      overflowY: gridSpec.overflowY && allowOverflowScroll ? 'auto' : 'hidden',
      scrollbarGutter: 'stable both-edges',
    }),
    [gridSpec.overflowX, gridSpec.overflowY, allowOverflowScroll],
  )

  const gridStyle = useMemo(
    () => ({
      position: 'absolute',
      left: `${centerGrid ? gridSpec.offsetLeft : 0}px`,
      top: `${centerGrid ? gridSpec.offsetTop : 0}px`,
      width: `${gridSpec.innerW}px`,
      height: `${gridSpec.innerH}px`,
      opacity: 0.2,
      border: `2px solid ${GRID_LINE_COLOR}`,
      pointerEvents: 'none',
    }),
    [centerGrid, gridSpec.offsetLeft, gridSpec.offsetTop, gridSpec.innerW, gridSpec.innerH],
  )

  return (
    <div
      ref={containerRef}
      data-testid="binpacking-desktop"
      data-grid-ready={isReady ? 'true' : 'false'}
      style={containerStyle}
      onDragOver={handleDragOver}
      onDragEnter={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {isReady && (
        <>
          {showGridLines && (
            <div style={gridStyle} data-testid="binpacking-grid" aria-hidden="true">
              <svg width="100%" height="100%">
                {Array.from({ length: cols + 1 }, (_, i) => (
                  <line
                    key={`v-${i}`}
                    x1={i * cellSize}
                    y1={0}
                    x2={i * cellSize}
                    y2={rows * cellSize}
                    stroke={GRID_LINE_COLOR}
                    strokeWidth="1"
                  />
                ))}
                {Array.from({ length: rows + 1 }, (_, i) => (
                  <line
                    key={`h-${i}`}
                    x1={0}
                    y1={i * cellSize}
                    x2={cols * cellSize}
                    y2={i * cellSize}
                    stroke={GRID_LINE_COLOR}
                    strokeWidth="1"
                  />
                ))}
              </svg>
            </div>
          )}

          {dragGhost && (
            <div
              data-testid="binpacking-ghost"
              aria-hidden="true"
              style={{
                position: 'absolute',
                ...cellsToPixels(dragGhost, gridSpec, cellSize),
                backgroundColor: dragGhost.color || GRID_LINE_COLOR,
                opacity: 0.18,
                border: `2px dashed ${dragGhost.color || GRID_LINE_COLOR}`,
                borderRadius: 6,
                pointerEvents: 'none',
                zIndex: 5,
              }}
            />
          )}

          {/* Swallows every pointer event aimed at the windows underneath a fullscreen one. */}
          {fullscreenId && (
            <div
              data-testid="binpacking-fullscreen-overlay"
              style={{
                position: 'absolute',
                left: gridSpec.offsetLeft,
                top: gridSpec.offsetTop,
                width: gridSpec.innerW,
                height: gridSpec.innerH,
                zIndex: 50,
                background: 'transparent',
              }}
            />
          )}

          {packed.map((w) => {
            const isFullscreen = fullscreenId === w.id
            const preview = resizePreview?.id === w.id ? resizePreview : null
            const pixels = isFullscreen
              ? {
                  left: gridSpec.offsetLeft,
                  top: gridSpec.offsetTop,
                  width: gridSpec.innerW,
                  height: gridSpec.innerH,
                }
              : cellsToPixels(
                  {
                    x: w.x,
                    y: w.y,
                    width: preview ? preview.width : w.width,
                    height: preview ? preview.height : w.height,
                  },
                  gridSpec,
                  cellSize,
                )

            return (
              <DesktopWindow
                key={w.id}
                id={w.id}
                title={w.title}
                color={w.color}
                {...pixels}
                isFullscreen={isFullscreen}
                // While a drag ghost is on screen the window itself is hidden, so the ghost
                // is the only thing following the cursor.
                hidden={gesture?.kind === 'drag' && gesture.id === w.id && !!dragGhost}
                zIndex={isFullscreen ? 100 : activeId === w.id ? 10 : 1}
                onCollapse={handleCollapse}
                onGrow={handleGrow}
                onFullscreen={handleFullscreen}
                onClose={handleClose}
                onDragStart={onDragStart}
                onResizeStart={onResizeStart}
              >
                {w.content}
              </DesktopWindow>
            )
          })}
        </>
      )}
    </div>
  )
})

export default BinPackingLayout
