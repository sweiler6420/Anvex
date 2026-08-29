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

/**
 * Shown while the password is hidden — press it to reveal. Heroicons `eye`, 24/outline.
 *
 * Added by ANV-29 (`@components/ui/icons` is where an icon goes, never inline). The old
 * `Login.jsx` imported these two from `@heroicons/react/24/outline`; the same argument as
 * above applies — two more `d` attributes, no behaviour.
 */
export function EyeIcon({ className }) {
  return (
    <svg {...outline} className={className}>
      <path d="M2.036 12.322a1.012 1.012 0 0 1 0-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178Z" />
      <path d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />
    </svg>
  )
}

/** Shown while the password is visible — press it to hide. Heroicons `eye-slash`. */
export function EyeSlashIcon({ className }) {
  return (
    <svg {...outline} className={className}>
      <path d="M3.98 8.223A10.477 10.477 0 0 0 1.934 12C3.226 16.338 7.244 19.5 12 19.5c.993 0 1.953-.138 2.863-.395M6.228 6.228A10.45 10.45 0 0 1 12 4.5c4.756 0 8.773 3.162 10.065 7.498a10.523 10.523 0 0 1-4.293 5.774M6.228 6.228 3 3m3.228 3.228 3.65 3.65m7.894 7.894L21 21m-3.228-3.228-3.65-3.65m0 0a3 3 0 1 0-4.243-4.243m4.242 4.242L9.88 9.88" />
    </svg>
  )
}
