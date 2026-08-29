import { createContext } from 'react'

/**
 * The theme context (ANV-25).
 *
 * It lives in its own module rather than beside `ThemeProvider` for two reasons: a file
 * that exports both a component and a non-component loses React Fast Refresh, and the
 * `useDarkMode` hook in `@hooks` needs the context without importing the provider.
 *
 * The default is `null` — deliberately not a working stub. The old app defaulted the
 * context to a bare string, so a component rendered outside the provider destructured
 * `undefined` and failed somewhere else entirely; `useDarkMode` turns the same mistake
 * into one sentence naming the missing provider.
 *
 * @type {import('react').Context<{
 *   theme: 'light' | 'dark',
 *   isDark: boolean,
 *   setTheme: (theme: 'light' | 'dark') => void,
 *   toggleTheme: () => void,
 * } | null>}
 */
export const ThemeContext = createContext(null)

export default ThemeContext
