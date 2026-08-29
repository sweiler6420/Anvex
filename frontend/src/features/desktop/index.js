/**
 * The bin-packing window system (ANV-33) — the public surface of `features/desktop/`.
 *
 * ANV-34 (the widgets) and ANV-35 (`InteractiveDesktop`, which composes this with them and
 * goes into `Workflow`'s `demo` prop) import from here and not from the individual modules,
 * so the internal layout can change without a sweep through their files.
 *
 * ## The seam for a window's content
 *
 * A window's `content` is a **React node held on the window object**:
 *
 * ```jsx
 * { id: 'w1', title: 'Counter', color: '#3b82f6',
 *   x: 0, y: 0, width: 10, height: 8, minWidth: 5, minHeight: 4,
 *   content: <CounterWidget /> }
 * ```
 *
 * Nothing in this feature inspects it, imports a widget, or knows one exists — it is
 * rendered into the window's content box and clipped there. The same field on a
 * `WindowMenu` item's `window` template is what a dropped window is created from.
 */

export { default as BinPackingLayout } from './components/BinPackingLayout'
export { default as DesktopWindow } from './components/DesktopWindow'
export { default as WindowMenu } from './components/WindowMenu'
export { default as useContainerSize } from './hooks/useContainerSize'
export { clearPendingWindow, peekPendingWindow, setPendingWindow } from './dragPayload'
export { nextWindowId, resetWindowIdCounter } from './windowIds'
