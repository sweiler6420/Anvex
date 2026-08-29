import { useId, useState } from 'react'

import WidgetFrame from './WidgetFrame'

/**
 * An input that echoes what is typed into it (ANV-34). Ported from
 * `AverageInvestorWeb/src/components/shared/widgets/TextInputWidget.jsx` (21 lines).
 *
 * Its job on the desktop is to prove that a **focused, partially-typed control survives a
 * window being dragged, resized and re-packed** — the one thing a remount destroys that a
 * counter's zero does not make obvious.
 *
 * ## Changed from the original
 *
 *  - **The input has a label.** It had a `placeholder` and nothing else, so its accessible
 *    name was the placeholder — which disappears the moment a character is typed, leaving a
 *    control announced as "edit text, blank". ANV-29's rule: every control is named. The
 *    label is `sr-only` so the layout is unchanged, and the placeholder stays as a hint.
 *  - **`outline-none` is gone.** It removed the focus ring and replaced it with nothing,
 *    which makes the widget unusable from a keyboard: there is no way to see where focus
 *    is. A visible `focus-visible` ring replaces it.
 *  - **The echo is a live region.** It is the widget's only output and it changes on every
 *    keystroke, so `aria-live="polite"` is what makes it reach a screen reader at all —
 *    `role="status"` on the element, which is what `<output>` already means.
 *  - The em-dash placeholder for "nothing typed yet" is kept, but paired with a `sr-only`
 *    word: a dash is a punctuation mark, not a statement, and it is announced as one.
 */

export default function TextInputWidget({ label = 'Echo input' }) {
  const [text, setText] = useState('')
  const inputId = useId()

  return (
    <WidgetFrame label={label} testId="text-input-widget">
      <label htmlFor={inputId} className="sr-only">
        Text to echo
      </label>
      <input
        id={inputId}
        value={text}
        onChange={(event) => setText(event.target.value)}
        placeholder="Type here"
        className="w-full min-w-0 rounded bg-neutral-100 px-2 py-1 text-neutral-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500 dark:bg-neutral-200"
      />
      <output aria-live="polite" data-testid="text-input-echo" className="min-w-0 truncate">
        <span className="sr-only">Echo: </span>
        {text || <span aria-hidden="true">—</span>}
        {text ? null : <span className="sr-only">nothing typed yet</span>}
      </output>
    </WidgetFrame>
  )
}
