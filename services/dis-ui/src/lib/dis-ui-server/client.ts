import { readToken } from '../../auth/storage'
import { getBaseUrl } from './mode'

// The real-mode dis-ui-server HTTP seam. Every real JSON endpoint goes through the
// getJson/postJson/patchJson helpers below; they share one set of conventions with
// postMultipart (getBaseUrl base, Bearer from the session token, DisUiServerHttpError on
// non-2xx via parseErrorEnvelope), so status-code handling is consistent across the UI.
// Unused in fixture mode. (T10 replaced an earlier single GET wrapper with this set.)

// The session bearer the UI holds (Customer Master token, auth/storage). All authed real
// calls read it here; behind AuthBoundary it is always present, so a missing token is a
// loud error rather than a silent unauthenticated call.
export function sessionToken(): string {
  const token = readToken()
  if (token === null || token.length === 0) {
    throw new Error('no session token: cannot call dis-ui-server (sign in first)')
  }
  return token
}

// A non-2xx response from dis-ui-server, carrying the HTTP status plus the parsed
// error envelope (services/dis-ui-server/errors_http.py: {"error": {code, message,
// trace_id, details}}). Callers map (status, code) to a user-facing message; the
// `details` bag carries load-bearing context (e.g. tier-0 `reason` on a 422).
export class DisUiServerHttpError extends Error {
  readonly status: number
  readonly code: string
  readonly details: Record<string, unknown>

  constructor(status: number, code: string, message: string, details: Record<string, unknown>) {
    super(message)
    this.name = 'DisUiServerHttpError'
    this.status = status
    this.code = code
    this.details = details
  }
}

// Best-effort parse of the standard error envelope. A non-JSON or off-shape body
// degrades to an empty code so the caller still gets the status to map on.
async function parseErrorEnvelope(
  response: Response,
): Promise<{ code: string; message: string; details: Record<string, unknown> }> {
  try {
    const body = (await response.json()) as { error?: { code?: unknown; message?: unknown; details?: unknown } }
    const err = body.error ?? {}
    return {
      code: typeof err.code === 'string' ? err.code : '',
      message: typeof err.message === 'string' ? err.message : `request failed (${response.status})`,
      details:
        typeof err.details === 'object' && err.details !== null
          ? (err.details as Record<string, unknown>)
          : {},
    }
  } catch {
    return { code: '', message: `request failed (${response.status})`, details: {} }
  }
}

// POST a multipart/form-data body to dis-ui-server. The Content-Type header is
// deliberately NOT set: the browser sets it with the correct multipart boundary
// from the FormData. On a non-2xx, throws DisUiServerHttpError with the parsed
// envelope so the caller can map the status and code.
export async function postMultipart<T>(path: string, opts: { token: string; form: FormData }): Promise<T> {
  const response = await fetch(`${getBaseUrl()}${path}`, {
    method: 'POST',
    headers: { authorization: `Bearer ${opts.token}` },
    body: opts.form,
  })
  if (!response.ok) {
    const { code, message, details } = await parseErrorEnvelope(response)
    throw new DisUiServerHttpError(response.status, code, message, details)
  }
  return (await response.json()) as T
}

// Throw DisUiServerHttpError on a non-2xx, parsing the standard error envelope; otherwise
// parse the JSON body as T. The shared tail for the JSON helpers below.
async function readJsonOrThrow<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const { code, message, details } = await parseErrorEnvelope(response)
    throw new DisUiServerHttpError(response.status, code, message, details)
  }
  return (await response.json()) as T
}

// GET a JSON resource. Bearer from the session token. Non-2xx (incl. throw-style 404s)
// raise DisUiServerHttpError so callers map (status, code) consistently.
export async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${getBaseUrl()}${path}`, {
    headers: { authorization: `Bearer ${sessionToken()}` },
  })
  return readJsonOrThrow<T>(response)
}

// POST a JSON body (application/json). Same auth + error conventions as getJson.
export async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${getBaseUrl()}${path}`, {
    method: 'POST',
    headers: {
      authorization: `Bearer ${sessionToken()}`,
      'content-type': 'application/json',
    },
    body: JSON.stringify(body),
  })
  return readJsonOrThrow<T>(response)
}

// PATCH a JSON body (application/json). Same auth + error conventions as getJson.
export async function patchJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${getBaseUrl()}${path}`, {
    method: 'PATCH',
    headers: {
      authorization: `Bearer ${sessionToken()}`,
      'content-type': 'application/json',
    },
    body: JSON.stringify(body),
  })
  return readJsonOrThrow<T>(response)
}
