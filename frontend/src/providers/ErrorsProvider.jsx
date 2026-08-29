import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { CLIENT_ERROR_CODES, isNetworkError, toApiError } from '@lib/api'

import { ErrorsContext } from './ErrorsContext'

/**
 * The transient error surface (ANV-25), ported from
 * `AverageInvestorWeb/src/ErrorsProvider.js`.
 *
 * One error at a time, auto-cleared after {@link ERROR_TIMEOUT_MS}. It is deliberately not
 * a toast stack and deliberately not an error boundary — ANV-28 owns what renders this, and
 * a boundary catches render failures, which is a different problem.
 *
 * Three things are fixed relative to the old provider:
 *
 *   1. **The timer is owned, not fired and forgotten.** The old `handleSetError` called
 *      `setTimeout` and dropped the handle, which is two real bugs rather than one bit of
 *      untidiness. A second error arriving before the first expired left the *first*
 *      timer running, so it fired and wiped the second error early — a login failure that
 *      vanishes after two seconds because a stock request failed eight seconds ago. And an
 *      error raised just before a navigation left a timer that fired after unmount. Here
 *      the handle lives in a ref: setting an error clears the pending timer before starting
 *      a new one, and unmounting clears it.
 *   2. **It stores an `ApiError`, not whatever it was handed.** The old provider took a
 *      string, which every call site in `useApi.js` wrote by hand and differently, so the
 *      UI could only branch on the sentence. Everything through here is normalised with
 *      `toApiError`, so a consumer reads `error.code` — the same vocabulary the backend's
 *      envelope uses (CLAUDE.md §4) — and `error.details` is always an object, never
 *      `null`. A value that is not an `ApiError` at all (a thrown `TypeError`, a rejected
 *      string) becomes `unknown_error` rather than crashing the provider.
 *   3. **`request_cancelled` is swallowed.** An aborted request is a component unmounting,
 *      not a failure, and showing "The request was cancelled." for it trains users to
 *      ignore the banner. It is the one code that reaches `setError` and changes nothing.
 */

/** How long an error stays on screen. The old app's ten seconds, kept. */
export const ERROR_TIMEOUT_MS = 10_000

export function ErrorsProvider({ children }) {
  const [error, setErrorState] = useState(null)
  const timerRef = useRef(null)

  /** Cancel any pending auto-clear. Safe to call when there is none. */
  const cancelTimer = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }, [])

  const clearError = useCallback(() => {
    cancelTimer()
    setErrorState(null)
  }, [cancelTimer])

  /**
   * Accepts anything throwable. `null`/`undefined` clears, which is what makes
   * `setError(null)` on a successful retry read naturally.
   */
  const setError = useCallback(
    (value) => {
      if (value === null || value === undefined) {
        clearError()
        return
      }

      const apiError = toApiError(value)

      // Not a failure: the caller went away. Leave any error already on screen alone —
      // clearing it here would let an unmounting sibling erase a real message.
      if (apiError.code === CLIENT_ERROR_CODES.CANCELLED) return

      cancelTimer()
      setErrorState(apiError)
      timerRef.current = setTimeout(() => {
        timerRef.current = null
        setErrorState(null)
      }, ERROR_TIMEOUT_MS)
    },
    [cancelTimer, clearError],
  )

  // The unmount half of the same fix. Without it the timer above fires into a component
  // that is gone.
  useEffect(() => cancelTimer, [cancelTimer])

  const value = useMemo(
    () => ({
      error,
      // Derived here so a banner does not have to import the transport's helpers to ask
      // the one question it actually asks: "is the API reachable, or is this our fault?"
      isNetworkFailure: isNetworkError(error),
      setError,
      clearError,
    }),
    [error, setError, clearError],
  )

  return <ErrorsContext.Provider value={value}>{children}</ErrorsContext.Provider>
}

export default ErrorsProvider
