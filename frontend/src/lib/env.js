/**
 * The frontend's read of the root `.env`, and the only module that touches
 * `import.meta.env`.
 *
 * CLAUDE.md §2: one env file for every stack. Vite is pointed at the repo root by
 * `envDir` in vite.config.js and exposes only the `VITE_`-prefixed keys to the bundle.
 * The old app kept `src/app-config.json` for the same job; that file is deliberately
 * **not** ported — a second config file is a second place for the API URL to be wrong.
 *
 * ANV-24 builds `lib/api` on top of this and should import `API_BASE_URL` rather than
 * reading `import.meta.env` again.
 */

/**
 * Base URL for the Anvex API, with no trailing slash.
 *
 * An empty value is meaningful, not a mistake: it means "same origin", which routes
 * requests through the Vite dev proxy (`/v1` → the `api` service) instead of making a
 * cross-origin call the API's CORS list has to allow. `.env.example` ships the explicit
 * `http://localhost:8000` because that is what a browser on the host talks to.
 */
export const API_BASE_URL = String(import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, '')

/** Join the base URL with an API path. `apiUrl('/v1/stocks')` — the leading slash is required. */
export function apiUrl(path) {
  if (!path.startsWith('/')) {
    throw new Error(`apiUrl expects an absolute path, got ${JSON.stringify(path)}`)
  }
  return `${API_BASE_URL}${path}`
}

/** Vite's own mode flag, re-exported so nothing else has to reach into `import.meta.env`. */
export const IS_PRODUCTION = import.meta.env.PROD
