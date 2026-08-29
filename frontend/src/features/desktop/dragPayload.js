/**
 * The hand-off from the window menu to the desktop during an HTML5 drag (ANV-33).
 *
 * ## Why a module and not `dataTransfer`
 *
 * A window definition contains a **React node** (its content), and `DataTransfer` carries
 * strings. So the drag has to move a serialisable *label* through the browser and keep the
 * real object somewhere both ends can reach. The old repo's somewhere was a property on the
 * global object, `window.__BINPACKING_DRAG`, written inside a bare `try {} catch {}`.
 *
 * A module-level variable does the same job with the same lifetime — one document, one
 * drag — while being importable, mockable, resettable between tests, and invisible to
 * anything that did not ask for it. It also removes the failure mode two desktops on one
 * page had: a second `BinPackingLayout` shared the global and could consume a drag that was
 * aimed at the first.
 *
 * ## The part that is a browser rule, not a choice
 *
 * `dataTransfer.getData()` returns `''` during `dragover` — the spec puts the drag data
 * store in *protected mode* until the drop, precisely so a page cannot read what is being
 * dragged over it before the user commits. So the label is only legible at `drop` time, and
 * `dragover` has nothing but this module to tell it what is coming. The old code called
 * `getData` on `dragover` anyway and fell back to the global; the fallback was doing all
 * the work and the parse was reading an empty string every time.
 */

/** @type {object | null} */
let pending = null

/**
 * Announce what is being dragged. Called from the menu's `dragstart`.
 *
 * @param {object} windowDefinition the full window template, React content included
 */
export function setPendingWindow(windowDefinition) {
  pending = windowDefinition ?? null
}

/**
 * What is currently being dragged, if anything. Does **not** clear it — `dragover` fires
 * continuously and needs to keep answering the same question.
 *
 * @returns {object | null}
 */
export function peekPendingWindow() {
  return pending
}

/** Forget the drag. Called on `drop` and on `dragend`, so a cancelled drag leaves nothing. */
export function clearPendingWindow() {
  pending = null
}
