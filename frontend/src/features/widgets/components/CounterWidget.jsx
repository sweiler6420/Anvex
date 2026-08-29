import { useState } from 'react'

import WidgetFrame from './WidgetFrame'

/**
 * A counter (ANV-34). Ported from
 * `AverageInvestorWeb/src/components/shared/widgets/CounterWidget.jsx` (19 lines).
 *
 * It exists to prove the desktop's content seam carries a *stateful* node — a window that
 * is dragged, resized and re-packed must not remount its contents, and a counter that
 * resets to zero is the visible symptom if it does.
 *
 * ## Changed from the original
 *
 *  - **The buttons have `type="button"` and a real accessible name.** They had neither. No
 *    `type` means `submit` inside a form, so dropping this widget onto a page with one
 *    would have made "+" submit it; and the accessible name was the glyph, so a screen
 *    reader announced the pair as "hyphen-minus, button" and "plus sign, button".
 *  - **The value is a live region.** A count that changes with no visible pointer movement
 *    is exactly the case `aria-live="polite"` exists for; without it the only feedback for
 *    pressing the button is a number a screen-reader user has to go and look for.
 *  - Colours are theme-aware (see `WidgetFrame`), and `min-w-[3rem]` becomes `min-w-0` +
 *    `tabular-nums` so the value column does not force a 2×2 window to scroll horizontally
 *    before the count has even reached two digits.
 */

const BUTTON_CLASS =
  'rounded bg-neutral-200 px-2 py-1 font-demi text-neutral-900 hover:bg-neutral-300 ' +
  'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 ' +
  'focus-visible:outline-brand-500 dark:bg-neutral-700 dark:text-white dark:hover:bg-neutral-600'

export default function CounterWidget({ label = 'Counter', initialCount = 0 }) {
  const [count, setCount] = useState(initialCount)

  return (
    <WidgetFrame label={label} testId="counter-widget">
      <div className="flex min-w-0 items-center gap-2">
        <button
          type="button"
          className={BUTTON_CLASS}
          aria-label="Decrease count"
          onClick={() => setCount((c) => c - 1)}
        >
          −
        </button>
        <output
          aria-live="polite"
          data-testid="counter-value"
          className="min-w-0 rounded bg-neutral-100 px-3 py-1 text-center tabular-nums dark:bg-neutral-800"
        >
          {count}
        </output>
        <button
          type="button"
          className={BUTTON_CLASS}
          aria-label="Increase count"
          onClick={() => setCount((c) => c + 1)}
        >
          +
        </button>
      </div>
    </WidgetFrame>
  )
}
