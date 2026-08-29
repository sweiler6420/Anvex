import { useCallback } from 'react'

/**
 * One window on the desktop (ANV-33).
 *
 * Ported from `AverageInvestorWeb/src/components/shared/binpacking/Window.jsx` (155 lines).
 * Renamed from `Window` because a component called `Window` shadows the global inside its
 * own module, which is how the original ended up writing `typeof window !== 'undefined'`
 * guards in the file next door.
 *
 * **This component measures nothing.** Every dimension arrives as a pixel number that
 * `BinPackingLayout` computed from the grid, and every interaction leaves as a callback.
 * That is what makes it genuinely testable under jsdom: there is no layout to be missing.
 *
 * ## Kept as found
 *
 * The inline styles, verbatim. They are the visual contract — the glass panel, the blur, the
 * four coloured dots — and moving them to Tailwind classes would be a redesign wearing a
 * port's clothes. The one substantive rule they encode is `height: calc(100% - 40px)` on the
 * content wrapper, which is the header's height hard-coded a second time; it is preserved
 * and named as {@link HEADER_HEIGHT_PX} so a change to the padding cannot silently leave the
 * content overflowing.
 *
 * ## Changed, and why
 *
 *  - **The four control dots are `<button type="button">` with an `aria-label`.** They were
 *    already `<button>`s, but with no accessible name other than a `title` attribute — the
 *    *last* fallback in the accessible-name algorithm, supported unevenly, and invisible to
 *    a touch user. They also carried no `type`, so inside a form every one of them would
 *    submit it. Same rule ANV-29 applied to the password toggle: an icon that is a control
 *    says what pressing it does.
 *  - **The dead `useEffect` is gone.** It reset `style.transform` to `translate(0px, 0px)`
 *    whenever `left`/`top` changed, matched by a `document.querySelectorAll` sweep in
 *    `BinPackingLayout`'s mouse-up that did the same thing to every window at once. Nothing
 *    in the shipped code ever *set* a transform — the drag preview is a separate ghost
 *    element — so both were leftovers from an earlier implementation, and both wrote to the
 *    DOM behind React's back for no effect.
 */

/** The header's height in pixels; the content box is sized against it. */
const HEADER_HEIGHT_PX = 40

/** The four controls, in the order they appear. `label` is the accessible name. */
const CONTROLS = [
  { key: 'collapse', color: '#facc15', label: 'Collapse to minimum size', handler: 'onCollapse' },
  { key: 'grow', color: '#22c55e', label: 'Grow to fill free space', handler: 'onGrow' },
  { key: 'fullscreen', color: '#60a5fa', label: null, handler: 'onFullscreen' },
  { key: 'close', color: '#f87171', label: 'Close window', handler: 'onClose' },
]

const outerStyleFor = ({ left, top, width, height, zIndex, hidden }) => ({
  position: 'absolute',
  left: `${left}px`,
  top: `${top}px`,
  width: `${width}px`,
  height: `${height}px`,
  zIndex: zIndex || 1,
  cursor: 'default',
  display: hidden ? 'none' : undefined,
})

const INNER_STYLE = {
  width: 'calc(100% - 8px)',
  height: 'calc(100% - 8px)',
  margin: '4px',
  outline: '2px solid rgba(255, 255, 255, 0.29)',
  boxShadow: '0 8px 32px rgba(0, 0, 0, 0.1), inset 0 1px 0 rgba(255, 255, 255, 0.1)',
  backdropFilter: 'blur(5px)',
  background: 'rgba(59, 130, 246, 0.1)',
  borderRadius: '0.5rem',
}

const HEADER_STYLE = {
  background: 'rgba(0, 0, 0, 0.2)',
  color: 'white',
  fontSize: '0.875rem',
  padding: '0.5rem 0.75rem',
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  borderTopLeftRadius: '0.5rem',
  borderTopRightRadius: '0.5rem',
  borderBottom: '1px solid rgba(255, 255, 255, 0.1)',
}

const TITLE_STYLE = {
  fontFamily: 'AllRoundGothic, ui-sans-serif, system-ui',
  fontWeight: 500,
  whiteSpace: 'nowrap',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
}

const CONTENT_WRAPPER_STYLE = {
  padding: '0.5rem',
  color: 'white',
  fontSize: '0.75rem',
  display: 'flex',
  flexDirection: 'column',
  overflow: 'hidden',
  height: `calc(100% - ${HEADER_HEIGHT_PX}px)`,
}

const CONTENT_INNER_STYLE = {
  background: 'rgba(255, 255, 255, 0.1)',
  borderRadius: '0.25rem',
  padding: '0.25rem',
  flex: 1,
  minHeight: 0,
  width: '100%',
  height: '100%',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  overflow: 'auto',
}

const RESIZE_HANDLE_STYLE = {
  position: 'absolute',
  bottom: 0,
  right: 0,
  width: '1rem',
  height: '1rem',
  cursor: 'se-resize',
  opacity: 0.6,
}

const controlDotStyle = (background) => ({
  width: '12px',
  height: '12px',
  background,
  borderRadius: '9999px',
  transition: 'background-color 150ms ease-in-out',
  padding: 0,
  border: 'none',
})

/**
 * @param {object} props
 * @param {string} props.id
 * @param {string} props.title
 * @param {string} [props.color] the header's background; the window's identity colour
 * @param {number} props.left pixels, relative to the desktop container
 * @param {number} props.top
 * @param {number} props.width
 * @param {number} props.height
 * @param {number} [props.zIndex]
 * @param {boolean} [props.isFullscreen]
 * @param {boolean} [props.hidden] hidden while its own drag ghost is on screen
 * @param {(id: string) => void} [props.onCollapse]
 * @param {(id: string) => void} [props.onGrow]
 * @param {(id: string) => void} [props.onFullscreen]
 * @param {(id: string) => void} [props.onClose]
 * @param {(event: React.MouseEvent, id: string) => void} [props.onDragStart]
 * @param {(event: React.MouseEvent, id: string) => void} [props.onResizeStart]
 * @param {React.ReactNode} [props.children] the window's content — ANV-34's widgets
 */
export default function DesktopWindow({
  id,
  title,
  color,
  left,
  top,
  width,
  height,
  zIndex,
  isFullscreen = false,
  hidden = false,
  onCollapse,
  onGrow,
  onFullscreen,
  onClose,
  onDragStart,
  onResizeStart,
  children,
}) {
  const handlers = { onCollapse, onGrow, onFullscreen, onClose }

  /**
   * A window is dragged **by its header, and only by its header**.
   *
   * The listener is on the outer element rather than the header so that a press anywhere in
   * the window can raise it later without a second listener, but the three refusals below
   * are what keep a press on a control from also starting a drag: the mouse-down would
   * otherwise `preventDefault()` and the click never arrive.
   */
  const handleMouseDown = useCallback(
    (event) => {
      if (isFullscreen) return
      if (event.target.closest('[data-role="control"]')) return
      if (event.target.closest('[data-role="resize-handle"]')) return
      if (!event.target.closest('[data-role="header"]')) return
      onDragStart?.(event, id)
    },
    [id, isFullscreen, onDragStart],
  )

  return (
    <div
      style={outerStyleFor({ left, top, width, height, zIndex, hidden })}
      onMouseDown={handleMouseDown}
      data-window-id={id}
      data-testid={`desktop-window-${id}`}
    >
      <div style={INNER_STYLE}>
        <div
          data-role="header"
          style={{
            ...HEADER_STYLE,
            background: color || HEADER_STYLE.background,
            cursor: isFullscreen ? 'default' : 'move',
            userSelect: 'none',
          }}
        >
          <span style={TITLE_STYLE}>{title}</span>
          <div style={{ display: 'flex', gap: '0.25rem' }}>
            {CONTROLS.map(({ key, color: dot, label, handler }) => {
              // The fullscreen control is the only one whose name depends on the state,
              // because it is the only one that toggles (ANV-28's rule for the theme
              // switcher: the name says what pressing it *does*).
              const name =
                label ?? (isFullscreen ? 'Exit fullscreen' : 'Fill the desktop')
              return (
                <button
                  key={key}
                  type="button"
                  data-role="control"
                  data-testid={`window-${key}-${id}`}
                  aria-label={name}
                  title={name}
                  style={controlDotStyle(dot)}
                  onClick={(event) => {
                    event.stopPropagation()
                    handlers[handler]?.(id)
                  }}
                />
              )
            })}
          </div>
        </div>

        <div style={CONTENT_WRAPPER_STYLE}>
          <div style={CONTENT_INNER_STYLE}>{children}</div>
        </div>

        {!isFullscreen && (
          <div
            data-role="resize-handle"
            data-testid={`window-resize-${id}`}
            style={RESIZE_HANDLE_STYLE}
            onMouseDown={(event) => {
              event.stopPropagation()
              onResizeStart?.(event, id)
            }}
          >
            <svg
              width="100%"
              height="100%"
              aria-hidden="true"
              focusable="false"
              style={{ transform: 'rotate(45deg)' }}
            >
              <path d="M2 8 L8 2" stroke="white" strokeWidth="2" />
            </svg>
          </div>
        )}
      </div>
    </div>
  )
}
