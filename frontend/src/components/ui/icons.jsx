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

/* ------------------------------------------------------------------------------------ *
 * The home page's glyphs (ANV-32).
 *
 * `Features`, `Workflow` and `Pricing` imported seven more icons from
 * `@heroicons/react/24/outline`. Same argument as above — seven more `d` attributes, no
 * behaviour — and the same licence (Heroicons, MIT, © Tailwind Labs).
 *
 * All seven are decorative: each sits beside text that already says what it means. The
 * pricing ticks and crosses are the case that matters, because there the *colour* was
 * carrying the meaning: `Pricing.jsx` pairs each one with a `sr-only` "Included:" /
 * "Not included:" so the answer survives both `aria-hidden` and colour blindness.
 * ------------------------------------------------------------------------------------ */

/** Heroicons `chart-bar`, 24/outline. */
export function ChartBarIcon({ className }) {
  return (
    <svg {...outline} className={className}>
      <path d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 0 1 3 19.875v-6.75ZM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V8.625ZM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V4.125Z" />
    </svg>
  )
}

/** Heroicons `arrow-trending-up`, 24/outline. */
export function ArrowTrendingUpIcon({ className }) {
  return (
    <svg {...outline} className={className}>
      <path d="M2.25 18 9 11.25l4.306 4.306a11.95 11.95 0 0 1 5.814-5.518l2.74-1.22m0 0-5.94-2.281m5.94 2.28-2.28 5.941" />
    </svg>
  )
}

/** Heroicons `squares-2x2`, 24/outline. */
export function Squares2X2Icon({ className }) {
  return (
    <svg {...outline} className={className}>
      <path d="M3.75 6A2.25 2.25 0 0 1 6 3.75h2.25A2.25 2.25 0 0 1 10.5 6v2.25a2.25 2.25 0 0 1-2.25 2.25H6a2.25 2.25 0 0 1-2.25-2.25V6ZM3.75 15.75A2.25 2.25 0 0 1 6 13.5h2.25a2.25 2.25 0 0 1 2.25 2.25V18a2.25 2.25 0 0 1-2.25 2.25H6A2.25 2.25 0 0 1 3.75 18v-2.25ZM13.5 6a2.25 2.25 0 0 1 2.25-2.25H18A2.25 2.25 0 0 1 20.25 6v2.25A2.25 2.25 0 0 1 18 10.5h-2.25a2.25 2.25 0 0 1-2.25-2.25V6ZM13.5 15.75a2.25 2.25 0 0 1 2.25-2.25H18a2.25 2.25 0 0 1 2.25 2.25V18A2.25 2.25 0 0 1 18 20.25h-2.25A2.25 2.25 0 0 1 13.5 18v-2.25Z" />
    </svg>
  )
}

/** Heroicons `user-group`, 24/outline. */
export function UserGroupIcon({ className }) {
  return (
    <svg {...outline} className={className}>
      <path d="M18 18.72a9.094 9.094 0 0 0 3.741-.479 3 3 0 0 0-4.682-2.72m.94 3.198.001.031c0 .225-.012.447-.037.666A11.944 11.944 0 0 1 12 21c-2.17 0-4.207-.576-5.963-1.584A6.062 6.062 0 0 1 6 18.719m12 0a5.971 5.971 0 0 0-.941-3.197m0 0A5.995 5.995 0 0 0 12 12.75a5.995 5.995 0 0 0-5.058 2.772m0 0a3 3 0 0 0-4.681 2.72 8.986 8.986 0 0 0 3.74.477m.94-3.197a5.971 5.971 0 0 0-.94 3.197M15 6.75a3 3 0 1 1-6 0 3 3 0 0 1 6 0Zm6 3a2.25 2.25 0 1 1-4.5 0 2.25 2.25 0 0 1 4.5 0Zm-13.5 0a2.25 2.25 0 1 1-4.5 0 2.25 2.25 0 0 1 4.5 0Z" />
    </svg>
  )
}

/** Heroicons `currency-dollar`, 24/outline. */
export function CurrencyDollarIcon({ className }) {
  return (
    <svg {...outline} className={className}>
      <path d="M12 6v12m-3-2.818.879.659c1.171.879 3.07.879 4.242 0 1.172-.879 1.172-2.303 0-3.182C13.536 12.219 12.768 12 12 12c-.725 0-1.45-.22-2.003-.659-1.106-.879-1.106-2.303 0-3.182s2.9-.879 4.006 0l.415.33M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
    </svg>
  )
}

/** Heroicons `check-circle`, 24/outline. */
export function CheckCircleIcon({ className }) {
  return (
    <svg {...outline} className={className}>
      <path d="M9 12.75 11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
    </svg>
  )
}

/** Heroicons `x-circle`, 24/outline. */
export function XCircleIcon({ className }) {
  return (
    <svg {...outline} className={className}>
      <path d="m9.75 9.75 4.5 4.5m0-4.5-4.5 4.5M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
    </svg>
  )
}
