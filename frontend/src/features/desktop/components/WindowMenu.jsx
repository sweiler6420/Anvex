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
 * ## The mouse-only path, closed by ANV-35
 *
 * A `draggable` `<li>` is mouse-only: there is no keyboard equivalent of an HTML5 drag, so
 * as ANV-33 shipped it this palette could not be operated from a keyboard at all. **`onAdd`
 * closes that** — the same answer ANV-34 gave inside the watchlist, where a drag became a
 * pair of buttons expressing the same destination with no box model to interpret.
 *
 * `onAdd` is **optional, and its absence is the old behaviour byte for byte**: with no
 * handler the chip is the bare `<li>` it always was. With one, the chip's label becomes a
 * `<button>` *inside* that same `<li>` — the list markup, the `draggable` attribute and the
 * drag hand-off are all untouched, so the two affordances sit on top of each other rather
 * than beside each other and the mouse gesture is not traded away for the keyboard one.
 *
 * **The `<button>` carries `draggable` and the drag handlers too**, which looks redundant
 * and is not: a form control inside a draggable ancestor swallows the gesture in several
 * browsers — the ancestor's `dragstart` never fires — so introducing the button without
 * arming the payload from the button as well would have quietly removed drag for exactly the
 * users it exists for. The inner handler wins and the outer one is what a drag started from
 * the chip's padding still uses.
 *
 * The accessible name is `Add <name>`, not `<name>`: a button has to say what pressing it
 * does, and the visible label is a substring of it (WCAG 2.5.3).
 */

const ITEM_CLASS = 'cursor-move select-none rounded font-demi text-sm text-white shadow-sm'

/**
 * The chip's padding sits on whichever element is the hit target, so a click lands anywhere
 * on the chip rather than only on the word.
 */
const PAD_CLASS = 'block px-3 py-2'

const BUTTON_CLASS = `${PAD_CLASS} rounded focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-400 focus-visible:ring-offset-1`

/**
 * @param {object} props
 * @param {Array<{name: string, color: string, window: object}>} [props.items]
 * @param {string} [props.label] the prompt shown before the chips
 * @param {(item: object) => void} [props.onAdd] when supplied, each chip is *also* a button
 *   that calls this with the whole item — the keyboard- and touch-operable half.
 */
export default function WindowMenu({ items = [], label = 'Drag into grid:', onAdd }) {
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
            className={`${ITEM_CLASS}${onAdd ? '' : ` ${PAD_CLASS}`}`}
            style={{ backgroundColor: item.color }}
            data-testid={`window-menu-item-${item.name}`}
          >
            {onAdd ? (
              <button
                type="button"
                draggable
                onDragStart={handleDragStart(item)}
                onDragEnd={clearPendingWindow}
                onClick={() => onAdd(item)}
                className={BUTTON_CLASS}
                aria-label={`Add ${item.name}`}
                data-testid={`window-menu-add-${item.name}`}
              >
                {item.name}
              </button>
            ) : (
              item.name
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}
