import { setupServer } from 'msw/node'

import { handlers } from './handlers'

/**
 * The one MSW server for the whole suite. `src/test/setup.js` starts it, resets it between
 * tests and closes it at the end — do not call `setupServer` anywhere else, or two
 * interceptors fight over the same `fetch`.
 *
 * A test that needs different behaviour overrides for its own duration:
 *
 *   import { server } from '@test/msw/server'
 *   server.use(http.get(apiUrl('/v1/stocks'), () => pageResponse([...])))
 *
 * `resetHandlers()` in the afterEach hook puts the defaults back, so an override never
 * leaks into the next test.
 */
export const server = setupServer(...handlers)
