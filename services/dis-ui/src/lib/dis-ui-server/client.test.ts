import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { clearToken, writeToken } from '../../auth/storage'
import { DisUiServerHttpError, getJson, patchJson, postJson } from './client'

// T10: the shared JSON client methods. Mocked fetch (dis-ui-server is not run locally),
// asserting the real-call conventions: base URL, Bearer from the session token, JSON
// content-type on writes, and DisUiServerHttpError carrying the parsed envelope on non-2xx.

function okResponse(body: unknown, status = 200): Response {
  return { ok: true, status, json: async () => body } as unknown as Response
}
function errResponse(status: number, code: string, details: Record<string, unknown> = {}): Response {
  return {
    ok: false,
    status,
    json: async () => ({ error: { code, message: `failed: ${code}`, trace_id: null, details } }),
  } as unknown as Response
}

beforeEach(() => {
  vi.stubEnv('VITE_DIS_UI_SERVER_BASE_URL', 'http://test.local')
  writeToken('tok-123')
})
afterEach(() => {
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
  clearToken()
})

describe('client JSON helpers (T10)', () => {
  it('getJson GETs base+path with a Bearer header and parses the body', async () => {
    const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<Response>>(async () => okResponse([{ a: 1 }]))
    vi.stubGlobal('fetch', fetchMock)
    const out = await getJson<{ a: number }[]>('/api/v1/thing')
    expect(out).toEqual([{ a: 1 }])
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('http://test.local/api/v1/thing')
    expect((init as RequestInit | undefined)?.method ?? 'GET').toBe('GET')
    expect((init as RequestInit).headers).toMatchObject({ authorization: 'Bearer tok-123' })
  })

  it('postJson POSTs a JSON body with content-type + Bearer', async () => {
    const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<Response>>(async () => okResponse({ ok: true }, 201))
    vi.stubGlobal('fetch', fetchMock)
    await postJson('/api/v1/thing', { name: 'x' })
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('http://test.local/api/v1/thing')
    expect((init as RequestInit).method).toBe('POST')
    expect((init as RequestInit).headers).toMatchObject({
      authorization: 'Bearer tok-123',
      'content-type': 'application/json',
    })
    expect((init as RequestInit).body).toBe(JSON.stringify({ name: 'x' }))
  })

  it('patchJson PATCHes a JSON body', async () => {
    const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<Response>>(async () => okResponse({ ok: true }))
    vi.stubGlobal('fetch', fetchMock)
    await patchJson('/api/v1/thing/1', { name: 'y' })
    const [, init] = fetchMock.mock.calls[0]
    expect((init as RequestInit).method).toBe('PATCH')
    expect((init as RequestInit).body).toBe(JSON.stringify({ name: 'y' }))
  })

  it('maps a non-2xx to DisUiServerHttpError carrying status + code + details', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => errResponse(409, 'mapping_state_conflict', { source_id: 's1' })))
    await expect(getJson('/api/v1/thing')).rejects.toMatchObject({
      name: 'DisUiServerHttpError',
      status: 409,
      code: 'mapping_state_conflict',
      details: { source_id: 's1' },
    })
    await expect(getJson('/api/v1/thing')).rejects.toBeInstanceOf(DisUiServerHttpError)
  })

  it('throws a loud error when no session token is held', async () => {
    clearToken()
    vi.stubGlobal('fetch', vi.fn(async () => okResponse([])))
    await expect(getJson('/api/v1/thing')).rejects.toThrow(/no session token/i)
  })
})
