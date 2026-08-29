import { useContext } from 'react'

import { ThemeContext } from '@providers/ThemeContext'

/**
 * Read and toggle the theme (ANV-25).
 *
 * Named `useDarkMode` because that is what the components ANV-28 ports already call
 * (`Header.jsx`, `DarkModeSwitcher.jsx`), and the shape they destructure — `{ theme,
 * toggleTheme }` — is preserved. `isDark` and `setTheme` are additions; nothing is removed.
 *
 * Unlike the old hook, this throws outside the provider instead of handing back a context
 * default that half-works. A switcher that silently does nothing is harder to diagnose than
 * a component that says which provider is missing.
 *
 * @returns {{theme: 'light'|'dark', isDark: boolean, setTheme: (t: 'light'|'dark') => void,
 *            toggleTheme: () => void}}
 */
export function useDarkMode() {
  const context = useContext(ThemeContext)
  if (context === null) {
    throw new Error('useDarkMode must be used inside <ThemeProvider>.')
  }
  return context
}

export default useDarkMode
