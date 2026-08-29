import '@testing-library/jest-dom/vitest'

import { cleanup } from '@testing-library/react'
import { afterAll, afterEach, beforeAll } from 'vitest'

import { server } from './msw/server'

/**
 * The frontend test harness (ANV-23). Registered as `test.setupFiles` in vite.config.js,
 * so every `*.test.jsx` gets it — there is no second setup file and no per-test
 * `setupServer`.
 *
 * `onUnhandledRequest: 'error'` is the load-bearing setting. The default ('warn') lets a
 * request nobody mocked fall through to the real network, where it either hangs or hits a
 * developer's actually-running API — which is how a test starts passing for the wrong
 * reason. Erroring means "you added a call; add a handler" shows up as a failure with the
 * URL in it.
 */
beforeAll(() => {
  server.listen({ onUnhandledRequest: 'error' })
})

/**
 * jsdom has no layout, so `window.scrollTo` is one of its "not implemented" stubs: calling
 * it prints a full stack trace to stderr and returns. TanStack Router (ANV-27) calls it on
 * every completed navigation to put a new page at the top, so without this every routing
 * test buries its real output under jsdom traces for a no-op.
 *
 * A no-op assignment, not a `vi.fn()` — nothing should assert on scrolling, and a shared
 * spy in the setup file would be state leaking between tests. A test that genuinely cares
 * can install its own.
 */
window.scrollTo = () => {}

afterEach(() => {
  cleanup()
  server.resetHandlers()
})

afterAll(() => {
  server.close()
})
