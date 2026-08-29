import { useCallback, useEffect, useMemo, useState } from 'react'

import { ThemeContext } from './ThemeContext'

/**
 * Class-based dark mode (ANV-25), ported from `AverageInvestorWeb/src/ThemeProvider.js`.
 *
 * Tailwind is configured `darkMode: 'class'` (CLAUDE.md §5), so the *only* thing that makes
 * a `dark:` utility apply is the `dark` class on `<html>`. This provider owns that class,
 * and nothing else in the app touches it.
 *
 * Three things are fixed relative to the old provider:
 *
 *   1. **Nothing reads storage at module scope.** The old file computed its initial value
 *      in a top-level `const`, so importing it touched `localStorage` — before any error
 *      boundary existed, and in any environment without a `window`. Here the read happens
 *      in a lazy `useState` initialiser, i.e. during the provider's first render, where a
 *      failure is a React failure and not an import-time crash.
 *   2. **Storage being unavailable is one decision, applied to both halves.** The old file
 *      guarded the read with `typeof window` and left the write in a `try {} catch {}` with
 *      an empty body — two different opinions about the same browser. A private window, or
 *      a browser with site data blocked, can throw on *reading* `window.localStorage`
 *      itself, on `getItem`, and on `setItem` independently (Safari's private mode throws a
 *      quota error only on write). So both helpers below wrap the whole access, and both
 *      degrade to the same behaviour: **the theme still works, it just does not persist.**
 *   3. **The class is set by removing every known theme class and adding the current one.**
 *      The old `getPrevious()` derived the class to remove from the current state
 *      (`theme === 'dark' ? 'light' : 'dark'`), which is correct only while there are
 *      exactly two themes and fails silently the day a third arrives — it would leave the
 *      third class on `<html>` forever. Removing all of `THEMES` cannot drift.
 */

/**
 * The themes this app knows about. The class names on `<html>` *are* these strings.
 *
 * Module-private on purpose: a consumer reads `theme` / `isDark` off the context, so this
 * file exports nothing but the component and one string literal — which is also what keeps
 * React Fast Refresh working for it.
 */
const THEMES = Object.freeze({ LIGHT: 'light', DARK: 'dark' })

/** Every class this provider is allowed to put on, and therefore must take off. */
const THEME_CLASSES = Object.freeze(Object.values(THEMES))

/** The `localStorage` key. Kept exported so a test never hardcodes it. */
export const THEME_STORAGE_KEY = 'theme'

const isTheme = (value) => THEME_CLASSES.includes(value)

/**
 * The stored theme, or `null` when there is none, it is unrecognised, or storage is
 * unreachable. An unrecognised value is treated as absent rather than applied: a stale
 * `"midnight"` from a future build must not put an unknown class on `<html>`.
 */
function readStoredTheme() {
  if (typeof window === 'undefined') return null
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY)
    return isTheme(stored) ? stored : null
  } catch {
    return null
  }
}

/** Persist, or don't. A browser that refuses to store is not an error worth surfacing. */
function writeStoredTheme(theme) {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme)
  } catch {
    // Site data blocked, private mode, quota exceeded. The class is already on <html>, so
    // this session is correct; only the *next* one loses the preference.
  }
}

/** `true` when the OS asks for dark. `false` when it doesn't, or won't say. */
function prefersDarkScheme() {
  if (typeof window === 'undefined') return false
  try {
    return window.matchMedia?.('(prefers-color-scheme: dark)')?.matches === true
  } catch {
    return false
  }
}

/**
 * A stored choice wins; otherwise follow the OS.
 *
 * The old provider defaulted to light unconditionally, which meant a user whose machine is
 * set to dark got a bright screen on every first visit and on every new device, and had to
 * find the toggle again each time. `prefers-color-scheme` is the answer the platform
 * already has, and reading it costs one guarded line. It is consulted **only** when nothing
 * is stored, so an explicit choice — including an explicit choice of *light* on a dark
 * machine — is never overridden by the OS.
 */
function resolveInitialTheme() {
  return readStoredTheme() ?? (prefersDarkScheme() ? THEMES.DARK : THEMES.LIGHT)
}

export function ThemeProvider({ children }) {
  // Lazy initialiser: this runs on first render, not on import.
  const [theme, setThemeState] = useState(resolveInitialTheme)

  useEffect(() => {
    const root = document.documentElement
    root.classList.remove(...THEME_CLASSES)
    root.classList.add(theme)
    writeStoredTheme(theme)
  }, [theme])

  const setTheme = useCallback((next) => {
    // Ignore anything that is not a theme rather than writing a class nobody styles.
    if (isTheme(next)) setThemeState(next)
  }, [])

  const toggleTheme = useCallback(() => {
    setThemeState((current) => (current === THEMES.DARK ? THEMES.LIGHT : THEMES.DARK))
  }, [])

  const value = useMemo(
    () => ({ theme, isDark: theme === THEMES.DARK, setTheme, toggleTheme }),
    [theme, setTheme, toggleTheme],
  )

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

export default ThemeProvider
