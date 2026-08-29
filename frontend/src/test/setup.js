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

/**
 * The same stub, for the same reason, one element down (ANV-32).
 *
 * jsdom does not implement `Element.prototype.scrollIntoView` **at all** — it is missing
 * rather than a warning stub — and TanStack Router's scroll restoration calls it on any
 * navigation carrying a hash whose target is actually in the document. That was
 * unreachable until ANV-32: the header linked to `#features`/`#workflow`/`#pricing`/
 * `#contact` and nothing had those ids, so the router's own `getElementById(...)?.` short
 * circuit swallowed it. With the home page's sections in place, every fragment navigation
 * throws a `TypeError` inside the router's emit and prints eight lines of stack for a
 * no-op, which is precisely the noise `window.scrollTo` was stubbed to stop.
 *
 * Assigned on the prototype, because the elements are the router's to find, not ours to
 * hand it.
 */
Element.prototype.scrollIntoView = () => {}

afterEach(() => {
  cleanup()
  server.resetHandlers()
})

afterAll(() => {
  server.close()
})
