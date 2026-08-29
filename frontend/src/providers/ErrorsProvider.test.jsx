import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useErrors } from '@hooks/useErrors'
import { ApiError, CLIENT_ERROR_CODES } from '@lib/api'

import { ErrorsProvider, ERROR_TIMEOUT_MS } from './ErrorsProvider'

/** The live context, captured so a test can call `setError` with an arbitrary value. */
let api = null

function Probe() {
  api = useErrors()
  return (
    <div>
      <span data-testid="code">{api.error ? api.error.code : 'none'}</span>
      <span data-testid="message">{api.error ? api.error.message : ''}</span>
      <span data-testid="request-id">{api.error ? String(api.error.requestId) : ''}</span>
      <span data-testid="details">{api.error ? JSON.stringify(api.error.details) : ''}</span>
      <span data-testid="network">{String(api.isNetworkFailure)}</span>
    </div>
  )
}

const mount = () =>
  render(
    <ErrorsProvider>
      <Probe />
    </ErrorsProvider>,
  )

const raise = (value) => act(() => api.setError(value))
const advance = (ms) => act(() => vi.advanceTimersByTime(ms))
const code = () => screen.getByTestId('code').textContent

beforeEach(() => {
  api = null
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('ErrorsProvider', () => {
  it('stores an ApiError and exposes its code, details and request id', () => {
    mount()

    raise(
      new ApiError({
        code: 'not_found',
        message: "stock 'AAPL' was not found.",
        details: { resource: 'stock' },
        requestId: '8f1c',
        status: 404,
      }),
    )

    // The whole reason for normalising: a consumer branches on this, never on the sentence.
    expect(code()).toBe('not_found')
    expect(screen.getByTestId('request-id')).toHaveTextContent('8f1c')
    expect(screen.getByTestId('details')).toHaveTextContent('{"resource":"stock"}')
    expect(screen.getByTestId('network')).toHaveTextContent('false')
  })

  it('flags a transport failure so a banner can say "cannot reach the server"', () => {
    mount()

    raise(new ApiError({ code: CLIENT_ERROR_CODES.NETWORK }))

    expect(screen.getByTestId('network')).toHaveTextContent('true')
    // `details` is an object even when nothing supplied one, so a consumer indexes it
    // unconditionally — the frontend half of CLAUDE.md §4's "never null".
    expect(screen.getByTestId('details')).toHaveTextContent('{}')
  })

  it('clears the error when its timer expires', () => {
    mount()

    raise(new ApiError({ code: 'conflict' }))
    expect(code()).toBe('conflict')

    advance(ERROR_TIMEOUT_MS - 1)
    expect(code()).toBe('conflict')

    advance(1)
    expect(code()).toBe('none')
  })

  it('does not let a second error inherit the first error\'s timer', () => {
    // The old provider's bug, and the reason it was invisible: the second error is
    // displayed correctly and then vanishes early, at a moment governed by when the *first*
    // one was raised. With the handle dropped there are two live timers here; with it
    // owned there is one, and the assertions below fail on the buggy version twice over.
    mount()

    raise(new ApiError({ code: 'first_error' }))
    advance(ERROR_TIMEOUT_MS - 1000)

    raise(new ApiError({ code: 'second_error' }))
    expect(vi.getTimerCount()).toBe(1)

    // The first error's timer would fire 1000ms from here. The second must survive it.
    advance(2000)
    expect(code()).toBe('second_error')

    // And the second error's own timer must still be the one that clears it.
    advance(ERROR_TIMEOUT_MS - 2000 - 1)
    expect(code()).toBe('second_error')
    advance(1)
    expect(code()).toBe('none')
  })

  it('cancels the pending timer on unmount', () => {
    // "No state update after unmount" is asserted at the source: with the cleanup removed
    // the count below is 1, and the callback runs against a component that is gone.
    const view = mount()

    raise(new ApiError({ code: 'not_found' }))
    expect(vi.getTimerCount()).toBe(1)

    view.unmount()

    expect(vi.getTimerCount()).toBe(0)
    expect(() => vi.advanceTimersByTime(ERROR_TIMEOUT_MS * 2)).not.toThrow()
  })

  it('clears on demand, and drops the timer with it', () => {
    mount()

    raise(new ApiError({ code: 'validation_error' }))
    act(() => api.clearError())

    expect(code()).toBe('none')
    expect(vi.getTimerCount()).toBe(0)
  })

  it('treats a null value as a clear', () => {
    mount()

    raise(new ApiError({ code: 'validation_error' }))
    raise(null)

    expect(code()).toBe('none')
    expect(vi.getTimerCount()).toBe(0)
  })
})

describe('a cancelled request', () => {
  it('is swallowed silently', () => {
    mount()

    raise(new ApiError({ code: CLIENT_ERROR_CODES.CANCELLED }))

    // A component unmounting mid-flight is not a failure and must never reach the user.
    expect(code()).toBe('none')
    expect(vi.getTimerCount()).toBe(0)
  })

  it('does not wipe an error already on screen', () => {
    mount()

    raise(new ApiError({ code: 'not_found' }))
    raise({ code: 'ERR_CANCELED', name: 'CanceledError' })

    expect(code()).toBe('not_found')
    expect(vi.getTimerCount()).toBe(1)
  })
})

describe('a value that is not an ApiError', () => {
  it('becomes unknown_error rather than crashing the provider', () => {
    mount()

    expect(() => raise(new TypeError('cannot read properties of undefined'))).not.toThrow()

    expect(code()).toBe(CLIENT_ERROR_CODES.UNKNOWN)
    expect(screen.getByTestId('message')).toHaveTextContent(
      'cannot read properties of undefined',
    )
    expect(screen.getByTestId('details')).toHaveTextContent('{}')
    expect(screen.getByTestId('request-id')).toHaveTextContent('null')
  })

  it('survives a thrown string, which has no fields at all', () => {
    mount()

    expect(() => raise('something went wrong')).not.toThrow()

    expect(code()).toBe(CLIENT_ERROR_CODES.UNKNOWN)
  })
})

describe('useErrors', () => {
  it('names the missing provider instead of half-working', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    expect(() => render(<Probe />)).toThrow(/ErrorsProvider/)
    spy.mockRestore()
  })
})
