import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { clearToken, writeToken } from '../../auth/storage'
import { DisUiServerHttpError } from './client'
import { getMyRoles } from './roles'

// DIS step 2: getMyRoles() calls GET /api/v1/me/roles through the shared JSON client.
// Mocked fetch (dis-ui-server is not run locally); asserts the path, the Bearer, and
// that the {roles, resolved} envelope is parsed. Mirrors client.test.ts conventions.

function okResponse(body: unknown, status = 200): Response {
  return { ok: true, status, json: async () => body } as unknown as Response
}
function errResponse(status: number, code: string): Response {
  return {
    ok: false,
    status,
    json: async () => ({ error: { code, message: `failed: ${code}`, trace_id: null, details: {} } }),
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

describe('getMyRoles', () => {
  it('GETs /api/v1/me/roles with a Bearer and parses {roles, resolved}', async () => {
    const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<Response>>(async () =>
      okResponse({ roles: ['atlas:schema:publish'], resolved: true }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const out = await getMyRoles()
    expect(out).toEqual({ roles: ['atlas:schema:publish'], resolved: true })
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('http://test.local/api/v1/me/roles')
    expect((init as RequestInit | undefined)?.method ?? 'GET').toBe('GET')
    expect((init as RequestInit).headers).toMatchObject({ authorization: 'Bearer tok-123' })
  })

  it('parses the fail-safe {roles: [], resolved: false} body', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => okResponse({ roles: [], resolved: false })),
    )
    await expect(getMyRoles()).resolves.toEqual({ roles: [], resolved: false })
  })

  it('throws DisUiServerHttpError on a non-2xx (caller treats it as fail-safe)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => errResponse(500, 'internal')),
    )
    await expect(getMyRoles()).rejects.toBeInstanceOf(DisUiServerHttpError)
  })
})
