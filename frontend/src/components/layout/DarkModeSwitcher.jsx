import { MoonIcon, SunIcon } from '@components/ui/icons'
import { useDarkMode } from '@hooks/useDarkMode'

/**
 * The theme toggle (ANV-28), ported from
 * `AverageInvestorWeb/src/components/header/DarkModeSwitcher.jsx`.
 *
 * Close to a straight copy, as ANV-25 predicted: that component destructured
 * `{ theme, toggleTheme }` and `useDarkMode()` returns exactly those (plus `isDark` and
 * `setTheme`). The icons come from `@components/ui/icons` instead of `@heroicons/react` —
 * see that module for why the dependency is not re-adopted.
 *
 * Two things changed, both about the button rather than the theme:
 *
 *  - **`type="button"`.** The old one had no `type`, so the default `"submit"` applied. It
 *    happened to live outside a form; the day this switcher is reused inside one, an
 *    un-typed button submits it. A control that is not a submit button says so.
 *  - **The accessible name says what pressing it *does*, and changes with the theme.**
 *    `aria-label='Toggle theme'` was static, so a screen-reader user got no feedback that
 *    anything happened: same name before and after, and the only thing that changed was a
 *    decorative glyph. "Switch to dark theme" → press → "Switch to light theme" is
 *    self-announcing. (`aria-pressed` is the alternative idiom, but "pressed" describes a
 *    state this control does not really have — there is no unpressed dark mode.)
 *
 * The icon is the *destination*, not the current state — a sun while dark, a moon while
 * light — which is what the old one did and what the new label now spells out.
 *
 * `className` is a prop because the header renders this twice, at two sizes: once in the
 * desktop action group and once inside the mobile drawer. It has no internal state, so two
 * instances cannot disagree; both read the one context.
 */
export default function DarkModeSwitcher({ className = '' }) {
  const { theme, toggleTheme } = useDarkMode()
  const isDark = theme === 'dark'

  return (
    <button
      type="button"
      aria-label={isDark ? 'Switch to light theme' : 'Switch to dark theme'}
      onClick={toggleTheme}
      className={`rounded-md border border-neutral-300 p-2 hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-800 ${className}`}
    >
      {isDark ? (
        <SunIcon className="h-5 w-5 text-brand-400" />
      ) : (
        <MoonIcon className="h-5 w-5 text-brand-600" />
      )}
    </button>
  )
}
