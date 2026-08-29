import { http, HttpResponse } from 'msw'

import { apiUrl } from '../../lib/env'

/**
 * The default MSW handler set (ANV-23).
 *
 * Every later frontend ticket mocks the API here or, for one test's worth of behaviour,
 * with `server.use(...)` — never by stubbing `fetch`/axios. Two rules keep the mocks
 * honest, and they come straight from the backend's own contract (build-log carry-overs):
 *
 *  - **A non-2xx body is the fixed error envelope**, `{"error": {code, message, details,
 *    request_id}}` with `details` always `{}` and never `null`. Use `errorResponse(...)`
 *    below so a handler cannot invent a different shape; the UI branches on `code`.
 *  - **A list is a `Page<T>` envelope**, `{items, total, limit, offset, has_more}`. Use
 *    `pageResponse(...)`.
 *
 * Handlers are matched against `apiUrl(path)`, so they follow `VITE_API_BASE_URL` — the
 * same-origin (dev proxy) and cross-origin configurations are both covered without a
 * second handler.
 */

let requestCounter = 0

/** The backend's `app.schemas.errors.ErrorResponse`, exactly. */
export function errorResponse(code, message, { status = 400, details = {} } = {}) {
  requestCounter += 1
  return HttpResponse.json(
    {
      error: {
        code,
        message,
        details,
        request_id: `msw-${String(requestCounter).padStart(8, '0')}`,
      },
    },
    { status },
  )
}

/** The backend's `app.schemas.pagination.Page[T]`, exactly. */
export function pageResponse(items, { total = items.length, limit = 50, offset = 0 } = {}) {
  return HttpResponse.json({
    items,
    total,
    limit,
    offset,
    has_more: offset + items.length < total,
  })
}

export const handlers = [
  // Liveness. Unversioned on purpose — CLAUDE.md §4.
  http.get(apiUrl('/health'), () => HttpResponse.json({ status: 'ok' })),

  // Readiness, likewise unversioned.
  http.get(apiUrl('/health/ready'), () => HttpResponse.json({ status: 'ready' })),
]
