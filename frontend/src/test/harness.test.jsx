import { render, screen, waitFor } from '@testing-library/react'
import { http } from 'msw'
import { useEffect, useState } from 'react'
import { describe, expect, it } from 'vitest'

import { apiUrl } from '../lib/env'
import { errorResponse, pageResponse } from './msw/handlers'
import { server } from './msw/server'

/**
 * Proves the harness itself, which is the whole point of ANV-23: jsdom + React 18 +
 * @testing-library/react + MSW intercepting a real `fetch`, with the setup/teardown hooks
 * in setup.js doing the starting, resetting and closing.
 *
 * `Probe` lives here rather than in `src/` on purpose. ANV-24 owns the API client layer
 * (CLAUDE.md §5: "all network calls go through `lib/api`"), so shipping a component that
 * calls `fetch` directly would be a half-written seam for it to unpick. A test fixture is
 * not that.
 */
function Probe({ path }) {
  const [state, setState] = useState({ status: 'loading' })

  useEffect(() => {
    let live = true
    fetch(apiUrl(path))
      .then(async (response) => ({ ok: response.ok, body: await response.json() }))
      .then((result) => {
        if (live) setState({ status: 'done', ...result })
      })
    return () => {
      live = false
    }
  }, [path])

  if (state.status === 'loading') return <p>loading…</p>
  return (
    <pre data-testid="body" data-ok={String(state.ok)}>
      {JSON.stringify(state.body)}
    </pre>
  )
}

describe('the frontend test harness', () => {
  it('serves a default handler to a component that fetches', async () => {
    render(<Probe path="/health" />)

    expect(screen.getByText('loading…')).toBeInTheDocument()

    await waitFor(() => expect(screen.getByTestId('body')).toBeInTheDocument())
    expect(JSON.parse(screen.getByTestId('body').textContent)).toEqual({ status: 'ok' })
    expect(screen.getByTestId('body')).toHaveAttribute('data-ok', 'true')
  })

  it('lets a test override a handler with server.use', async () => {
    server.use(http.get(apiUrl('/health'), () => errorResponse('not_found', 'gone', { status: 404 })))

    render(<Probe path="/health" />)

    await waitFor(() => expect(screen.getByTestId('body')).toBeInTheDocument())
    const body = JSON.parse(screen.getByTestId('body').textContent)
    // The fixed error envelope: all four keys, `details` an object and never null.
    expect(Object.keys(body.error).sort()).toEqual(['code', 'details', 'message', 'request_id'])
    expect(body.error.code).toBe('not_found')
    expect(body.error.details).toEqual({})
    expect(screen.getByTestId('body')).toHaveAttribute('data-ok', 'false')
  })

  it('resets overrides between tests', async () => {
    // The previous test's 404 override must be gone — resetHandlers in setup.js.
    render(<Probe path="/health" />)

    await waitFor(() => expect(screen.getByTestId('body')).toBeInTheDocument())
    expect(screen.getByTestId('body')).toHaveAttribute('data-ok', 'true')
  })

  it('builds a Page<T> envelope the backend would recognise', async () => {
    server.use(
      http.get(apiUrl('/v1/stocks'), () =>
        pageResponse([{ ticker: 'AAPL' }], { total: 3, limit: 1, offset: 0 }),
      ),
    )

    const response = await fetch(apiUrl('/v1/stocks'))
    await expect(response.json()).resolves.toEqual({
      items: [{ ticker: 'AAPL' }],
      total: 3,
      limit: 1,
      offset: 0,
      has_more: true,
    })
  })

  it('fails a request nobody mocked instead of letting it reach the network', async () => {
    // onUnhandledRequest: 'error' in setup.js. Without it this would hang or, worse, hit a
    // real API the developer happens to be running.
    await expect(fetch(apiUrl('/v1/not-mocked'))).rejects.toThrow()
  })
})
