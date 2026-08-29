import { useCallback, useEffect, useMemo, useState } from 'react'

import { ThemeContext } from './ThemeContext'
import {
  isTheme,
  resolveInitialTheme,
  THEME_CLASSES,
  THEMES,
  writeStoredTheme,
} from './themeStorage'

/**
 * Class-based dark mode (ANV-25), ported from `AverageInvestorWeb/src/ThemeProvider.js`.
 *
 * Tailwind is configured `darkMode: 'class'` (CLAUDE.md §5), so the *only* thing that makes
 * a `dark:` utility apply is the `dark` class on `<html>`. This provider owns that class
 * once React is running, and nothing else in the app touches it.
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
 *      an empty body — two different opinions about the same browser. `themeStorage.js`
 *      now owns both, and wraps the `window.localStorage` property read as well as the
 *      `getItem`/`setItem` calls, because a browser can refuse any one of the three.
 *   3. **The class is set by removing every known theme class and adding the current one.**
 *      The old `getPrevious()` derived the class to remove from the current state
 *      (`theme === 'dark' ? 'light' : 'dark'`), which is correct only while there are
 *      exactly two themes and fails silently the day a third arrives.
 *
 * **ANV-28 moved the key, the class names and `resolveInitialTheme` into
 * `providers/themeStorage.js`** and left this file the React half. That is not tidying: the
 * pre-paint script injected into `index.html` has to resolve the *same* theme this provider
 * settles on, and it cannot import a module (see `themeStorage.js` for why). Sharing one
 * rule is what stops the page flipping colour a moment after it appears.
 */

export function ThemeProvider({ children }) {
  // Lazy initialiser: this runs on first render, not on import. The pre-paint script has
  // already put the same class on <html> using the same rule, so this agrees with it
  // rather than correcting it.
  const [theme, setThemeState] = useState(() => resolveInitialTheme())

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
