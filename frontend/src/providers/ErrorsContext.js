import { createContext } from 'react'

/**
 * The transient-error context (ANV-25). Separate from `ErrorsProvider` for the same two
 * reasons as `ThemeContext`: Fast Refresh, and a hook that must not import a component.
 *
 * `null` by default so `useErrors` can say "you are outside `<ErrorsProvider>`" instead of
 * letting a `setError` call reach the old app's default, which was a `console.error` stub
 * that silently swallowed every error raised before the provider mounted.
 *
 * @type {import('react').Context<{
 *   error: import('@lib/api').ApiError | null,
 *   isNetworkFailure: boolean,
 *   setError: (value: unknown) => void,
 *   clearError: () => void,
 * } | null>}
 */
export const ErrorsContext = createContext(null)

export default ErrorsContext
