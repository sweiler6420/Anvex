/**
 * Ids for windows the desktop creates itself, i.e. windows dropped in from the menu (ANV-33).
 *
 * A counter, not a clock. The original built `win_${Date.now()}`, so two windows created
 * inside the same millisecond — which a quick second drop, or a test loop, does easily — got
 * the **same id**. That is one React key for two elements, and a close that removes both.
 *
 * It lives in its own module rather than beside `BinPackingLayout` because a `.jsx` file that
 * exports a component *and* a function loses React Fast Refresh (`react-refresh/
 * only-export-components`, the rule ANV-25 and ANV-27 both split files over), and the reset
 * has to be exported for tests that want ids they can name.
 */

let counter = 0

/** The next window id: `win_1`, `win_2`, … */
export function nextWindowId() {
  counter += 1
  return `win_${counter}`
}

/** Start again from `win_1`. For tests; nothing in the application calls it. */
export function resetWindowIdCounter() {
  counter = 0
}
