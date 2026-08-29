/**
 * The four icons the shell needs (ANV-28).
 *
 * ---------------------------------------------------------------------------------------
 * ## Why not `@heroicons/react` and `@headlessui/react`
 *
 * The old header imported both. Neither is re-adopted, and the two decisions have
 * *different* reasons — the ticket was right to ask for a justification rather than a
 * blanket answer.
 *
 * **`@heroicons/react`** ships one React component per SVG and nothing else: no behaviour,
 * no state, no accessibility logic. The old app used exactly four of the ~1,300. Copying
 * four `d` attributes (Heroicons is MIT-licensed, © Tailwind Labs) costs the forty lines
 * below and removes a dependency, a version to keep current, and a tree-shaking assumption
 * from the bundle. There is nothing here a library could get *right* that this gets wrong —
 * that is what distinguishes it from the next case.
 *
 * **`@headlessui/react`** is the opposite kind of dependency: it exists precisely because
 * focus management, `aria-*` wiring and Escape handling are easy to reimplement badly, and
 * "I'll hand-roll a modal" is a well-known way to ship an inaccessible one. It is **not**
 * declined because it is big. It is declined because `Header`'s drawer is not a **modal**:
 * it is a [disclosure navigation
 * menu](https://www.w3.org/WAI/ARIA/apg/patterns/disclosure/) — a panel that follows its
 * own toggle button in the DOM, does not overlay the page's own focusable content on the
 * breakpoints where it renders, and therefore must **not** trap focus. Tab out of the last
 * link and you should reach the page. Headless UI's `Dialog` would add the trap and the
 * inert background the pattern explicitly does not want, and its `Disclosure` supplies
 * `aria-expanded`/`aria-controls` — six attributes, not a focus manager. See `Header.jsx`
 * for the three behaviours that *are* implemented by hand, and `Header.test.jsx` for the
 * keyboard test that would fail if any of them were dropped.
 *
 * Reconsider `@headlessui` the day the shell grows something genuinely modal — a command
 * palette, a confirm dialog, a combobox. Do not reconsider it for this.
 *
 * ---------------------------------------------------------------------------------------
 * Every icon here is decorative: it sits inside a control whose accessible name comes from
 * the control's own `aria-label` or text. So each is `aria-hidden`, `focusable="false"`
 * (IE/Edge legacy still tab-stops SVGs otherwise) and takes its size and colour from the
 * `className` the caller passes.
 */

/** The attributes every Heroicons 24/outline glyph shares. */
const outline = {
  xmlns: 'http://www.w3.org/2000/svg',
  fill: 'none',
  viewBox: '0 0 24 24',
  strokeWidth: 1.5,
  stroke: 'currentColor',
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  'aria-hidden': 'true',
  focusable: 'false',
}

/** The hamburger. Heroicons `bars-3`, 24/outline. */
export function Bars3Icon({ className }) {
  return (
    <svg {...outline} className={className}>
      <path d="M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25h16.5" />
    </svg>
  )
}

/** The close glyph. Heroicons `x-mark`, 24/outline. */
export function XMarkIcon({ className }) {
  return (
    <svg {...outline} className={className}>
      <path d="M6 18 18 6M6 6l12 12" />
    </svg>
  )
}

/** Shown by the switcher while the theme is dark — press it for light. Heroicons `sun`. */
export function SunIcon({ className }) {
  return (
    <svg {...outline} className={className}>
      <path d="M12 3v2.25m6.364.386-1.591 1.591M21 12h-2.25m-.386 6.364-1.591-1.591M12 18.75V21m-4.773-4.227-1.591 1.591M5.25 12H3m4.227-4.773L5.636 5.636M15.75 12a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0Z" />
    </svg>
  )
}

/** Shown by the switcher while the theme is light — press it for dark. Heroicons `moon`. */
export function MoonIcon({ className }) {
  return (
    <svg {...outline} className={className}>
      <path d="M21.752 15.002A9.72 9.72 0 0 1 18 15.75c-5.385 0-9.75-4.365-9.75-9.75 0-1.33.266-2.597.748-3.752A9.753 9.753 0 0 0 3 11.25C3 16.635 7.365 21 12.75 21a9.753 9.753 0 0 0 9.002-5.998Z" />
    </svg>
  )
}
