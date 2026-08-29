/**
 * `ApiError` — the one failure shape every call through `lib/api` produces (ANV-24).
 *
 * The backend's error body is fixed (CLAUDE.md §4) and every non-2xx response uses it:
 *
 *     {"error": {"code", "message", "details", "request_id"}}
 *
 * with `details` always `{}` rather than `null`. **A consumer branches on `code`, never on
 * `message`** — a message is a sentence somebody will reword.
 *
 * The problem this module solves is that *not every failure has a body*. A DNS failure, a
 * dropped connection, a timeout and an aborted request all produce an axios error with no
 * `response` at all, and the old app dealt with that by making each caller ask
 * `if (!err?.response)` — five call sites in `useApi.js`, each guessing differently, each
 * returning its own ad-hoc object (`{status, message, error, detail}`) so that a caller
 * could not tell success from failure without probing for fields that might not exist.
 *
 * So: **a transport failure is given a `code` of its own** and `status: null`. A caller
 * writes `err.code === 'network_error'`, exactly as it writes `err.code === 'not_found'`,
 * and never touches `err.response`. The client-originated codes below are deliberately
 * disjoint from every code the backend can emit (`app/domain/errors.py` plus the token
 * codes), so one namespace is safe to switch on.
 */

/**
 * Codes this module invents, for failures the server never got to describe.
 *
 * Disjoint from the backend's own vocabulary by construction — none of `internal_error`,
 * `not_found`, `conflict`, `validation_error`, `unauthorized`, `forbidden`,
 * `external_service_error`, `invalid_token`, `token_expired`, `wrong_token_type` or
 * `http_error` appears here.
 */
export const CLIENT_ERROR_CODES = Object.freeze({
  /** The request never reached the API: DNS, refused connection, CORS, offline. */
  NETWORK: 'network_error',
  /** The request was sent but the API did not answer inside the client timeout. */
  TIMEOUT: 'timeout',
  /** The caller aborted it (an `AbortSignal`, a component unmounting). */
  CANCELLED: 'request_cancelled',
  /** Something answered, but not with the envelope — a proxy's HTML 502, say. */
  MALFORMED: 'malformed_response',
  /** A failure before the request existed. Should be a bug, not traffic. */
  UNKNOWN: 'unknown_error',
})

/** The four 401-shaped codes the backend emits. Kept here so nothing hardcodes a string. */
export const AUTH_ERROR_CODES = Object.freeze({
  /** No credentials presented, or they no longer map to an account. */
  UNAUTHORIZED: 'unauthorized',
  /** Malformed, tampered, or signed with the wrong key. Refreshing cannot help. */
  INVALID_TOKEN: 'invalid_token',
  /** The access token is past its `exp`. **This is the refresh signal.** */
  TOKEN_EXPIRED: 'token_expired',
  /** An access token was presented where a refresh token was required, or vice versa. */
  WRONG_TOKEN_TYPE: 'wrong_token_type',
})

/** Fallback text, used only when there is nothing better to say. */
const DEFAULT_MESSAGES = {
  [CLIENT_ERROR_CODES.NETWORK]: 'Could not reach the Anvex API.',
  [CLIENT_ERROR_CODES.TIMEOUT]: 'The Anvex API did not respond in time.',
  [CLIENT_ERROR_CODES.CANCELLED]: 'The request was cancelled.',
  [CLIENT_ERROR_CODES.MALFORMED]: 'The Anvex API returned an unexpected response.',
  [CLIENT_ERROR_CODES.UNKNOWN]: 'The request could not be sent.',
}

/**
 * A single, always-complete failure shape.
 *
 * Every field is always present, which is the whole point — `details` is `{}` and never
 * `null` (mirroring the backend), `status` is `null` and never `undefined` when no
 * response arrived, and `requestId` is `null` rather than missing. A consumer indexes
 * unconditionally.
 */
export class ApiError extends Error {
  /**
   * @param {object} init
   * @param {string} init.code             machine-readable slug; the only thing to branch on
   * @param {string} [init.message]        human-readable sentence, safe to display
   * @param {object} [init.details]        flat structured context; `{}` when there is none
   * @param {string|null} [init.requestId] the API's `request_id`, for a support ticket
   * @param {number|null} [init.status]    the HTTP status, or `null` if nothing answered
   * @param {unknown} [init.cause]         the original error, for logging only
   */
  constructor({ code, message, details, requestId = null, status = null, cause } = {}) {
    super(message || DEFAULT_MESSAGES[code] || 'The request failed.')
    this.name = 'ApiError'
    this.code = code || CLIENT_ERROR_CODES.UNKNOWN
    this.details = details && typeof details === 'object' ? details : {}
    this.requestId = requestId ?? null
    this.status = typeof status === 'number' ? status : null
    if (cause !== undefined) this.cause = cause
  }

  /** True when nothing answered — no status line, no envelope, no `request_id`. */
  get isTransportFailure() {
    return this.status === null
  }
}

/** Type guard. */
export function isApiError(value) {
  return value instanceof ApiError
}

/** "The request never reached the API." Prefer this to comparing `code` by hand. */
export function isNetworkError(value) {
  return (
    isApiError(value) &&
    (value.code === CLIENT_ERROR_CODES.NETWORK || value.code === CLIENT_ERROR_CODES.TIMEOUT)
  )
}

/** "The session is not usable" — either half of the pair was rejected. */
export function isAuthError(value) {
  return isApiError(value) && Object.values(AUTH_ERROR_CODES).includes(value.code)
}

/**
 * Pull the envelope out of a response body, or return `null` if it is not one.
 *
 * Deliberately strict: a body is only an envelope if `error.code` is a non-empty string.
 * Anything else — an HTML error page from a proxy, a bare `{"detail": ...}` from a
 * misconfigured route, an empty body — becomes `malformed_response`, which is a *true*
 * statement, where guessing a code would be a false one.
 */
function readEnvelope(data) {
  const envelope = data?.error
  if (!envelope || typeof envelope !== 'object') return null
  if (typeof envelope.code !== 'string' || envelope.code.length === 0) return null
  return {
    code: envelope.code,
    message: typeof envelope.message === 'string' ? envelope.message : undefined,
    details: envelope.details && typeof envelope.details === 'object' ? envelope.details : {},
    requestId: typeof envelope.request_id === 'string' ? envelope.request_id : null,
  }
}

/** axios' own codes for "it never got there", mapped onto ours. */
function transportCodeFor(error) {
  if (error?.code === 'ECONNABORTED' || error?.code === 'ETIMEDOUT') {
    return CLIENT_ERROR_CODES.TIMEOUT
  }
  return CLIENT_ERROR_CODES.NETWORK
}

/**
 * Normalise anything thrown by axios (or by us) into an {@link ApiError}.
 *
 * Idempotent: an `ApiError` passed in comes straight back out, so an interceptor that has
 * already normalised a failure can hand it on without wrapping it twice.
 */
export function toApiError(error) {
  if (isApiError(error)) return error

  // An aborted request is not a failure of the API and must not read like one — a
  // component unmounting mid-flight would otherwise surface as an error toast.
  if (error?.code === 'ERR_CANCELED' || error?.name === 'CanceledError' || error?.__CANCEL__) {
    return new ApiError({ code: CLIENT_ERROR_CODES.CANCELLED, status: null, cause: error })
  }

  const response = error?.response
  if (response) {
    const envelope = readEnvelope(response.data)
    if (envelope) {
      return new ApiError({ ...envelope, status: response.status, cause: error })
    }
    return new ApiError({
      code: CLIENT_ERROR_CODES.MALFORMED,
      message: `${DEFAULT_MESSAGES[CLIENT_ERROR_CODES.MALFORMED]} (HTTP ${response.status})`,
      status: response.status,
      cause: error,
    })
  }

  // A request object with no response is the textbook transport failure. No request
  // object at all means we broke before sending — a bad config, a throwing interceptor.
  if (error?.request || error?.code) {
    return new ApiError({ code: transportCodeFor(error), status: null, cause: error })
  }

  return new ApiError({
    code: CLIENT_ERROR_CODES.UNKNOWN,
    message: error?.message,
    status: null,
    cause: error,
  })
}
