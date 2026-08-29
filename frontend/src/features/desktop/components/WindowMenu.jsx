import { clearPendingWindow, setPendingWindow } from '../dragPayload'

/**
 * The palette a new window is dragged out of (ANV-33).
 *
 * Ported from `AverageInvestorWeb/src/components/shared/binpacking/WindowMenu.jsx` (43 lines).
 *
 * Each item is `{name, color, window}` where `window` is the template
 * `BinPackingLayout` instantiates on drop — title, colour, size, minimum size, and the React
 * node that becomes its content. ANV-34's widgets go in that last field and this component
 * needs no change to carry them.
 *
 * ## Changed from the original
 *
 *  - **The dragged definition goes through `../dragPayload` rather than
 *    `window.__BINPACKING_DRAG`.** Same lifetime, same single-drag assumption, no global.
 *  - **The two `try {} catch {}` wrappers are gone from the payload write.** Assigning a
 *    module variable cannot throw. The one around `dataTransfer.setData` stays and is real:
 *    a browser that refuses `application/json` needs the `text/plain` fallback.
 *  - **`onDragEnd` clears unconditionally.** The original only cleared when the global was
 *    truthy, which is the same thing written as a branch, but it also never ran when a drag
 *    was cancelled with Escape in some browsers — so `onDrop` clears too, and clearing twice
 *    is free.
 *  - **`key` is the item's `name`, not its array index.** An index key makes React reuse the
 *    wrong DOM node when the list is reordered, and a palette's whole job is to be a list.
 *  - The wrapper is a `<ul>` of `<li>`s with the classes unchanged (ANV-32's rule: repeated
 *    cards are a list), and the label is associated with it by `aria-labelledby` instead of
 *    being a floating `<div>` of text.
 *
 * ## What is *not* fixed here
 *
 * A `draggable` `<li>` is mouse-only: there is no keyboard equivalent of an HTML5 drag, so
 * this palette cannot be operated from a keyboard at all. That is true of the original and
 * true of this port. The honest fix is a click-to-add affordance beside the drag, which is a
 * behaviour change and therefore ANV-34/35's call, not this ticket's.
 */

const ITEM_CLASS =
  'cursor-move select-none rounded px-3 py-2 font-demi text-sm text-white shadow-sm'

/**
 * @param {object} props
 * @param {Array<{name: string, color: string, window: object}>} [props.items]
 * @param {string} [props.label] the prompt shown before the draggable chips
 */
export default function WindowMenu({ items = [], label = 'Drag into grid:' }) {
  const handleDragStart = (item) => (event) => {
    setPendingWindow(item.window)
    try {
      event.dataTransfer.setData('application/json', JSON.stringify({ name: item.name }))
    } catch {
      event.dataTransfer.setData('text/plain', JSON.stringify({ name: item.name }))
    }
    event.dataTransfer.effectAllowed = 'copy'
  }

  return (
    <div className="flex items-center gap-2" data-testid="window-menu">
      <span
        id="window-menu-label"
        className="mr-2 font-gothic text-md font-medium text-neutral-500"
      >
        {label}
      </span>
      <ul className="flex items-center gap-2" aria-labelledby="window-menu-label">
        {items.map((item) => (
          <li
            key={item.name}
            draggable
            onDragStart={handleDragStart(item)}
            onDragEnd={clearPendingWindow}
            className={ITEM_CLASS}
            style={{ backgroundColor: item.color }}
            data-testid={`window-menu-item-${item.name}`}
          >
            {item.name}
          </li>
        ))}
      </ul>
    </div>
  )
}
