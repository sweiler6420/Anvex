import { useContext } from 'react'

import { ErrorsContext } from '@providers/ErrorsContext'

/**
 * Read and raise the transient error (ANV-25).
 *
 * `error` is an `ApiError` or `null`. **Branch on `error.code`, never on `error.message`**
 * (CLAUDE.md §4) — `details` is always an object and `requestId` is always present or
 * `null`, so both can be indexed without a guard. `isNetworkFailure` is the one derived
 * question a banner asks, so it does not have to import the transport to ask it.
 *
 * `setError` takes anything throwable and normalises it, so a `catch (err)` block hands the
 * value straight in. `request_cancelled` is swallowed.
 *
 * @returns {{error: import('@lib/api').ApiError | null, isNetworkFailure: boolean,
 *            setError: (value: unknown) => void, clearError: () => void}}
 */
export function useErrors() {
  const context = useContext(ErrorsContext)
  if (context === null) {
    throw new Error('useErrors must be used inside <ErrorsProvider>.')
  }
  return context
}

export default useErrors
