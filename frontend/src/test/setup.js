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

afterEach(() => {
  cleanup()
  server.resetHandlers()
})

afterAll(() => {
  server.close()
})
