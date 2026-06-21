import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { DisUiServerHttpError } from './client'
import { uploadCsv, uploadCsvWithSessionToken } from './csv-uploads'
import type { CsvUploadResult } from './csv-uploads'

// T4-real: the FIRST real-mode HTTP call. These tests exercise it against a MOCKED fetch
// (dis-ui-server is not run locally), asserting the request matches API_CONTRACT 8.1 exactly
// and that CsvUploadResult parses / errors map by status + envelope code.

const RESULT: CsvUploadResult = {
  trace_id: '0190ac30-7c00-7000-8c00-0000000000d1',
  upload_id: 'us_abc123def456',
  tenant_id: 't_acme9k2l1mn4',
  store_id: '0190ac20-6b00-7000-8b00-0000000000c1',
  store_code: 'TX-102',
  source_id: 'manual_csv_upload',
  template_id: '0190ac10-5a00-7000-8a00-0000000000a1',
  gcs_uri: 'gs://dis-bronze/tenant/x/source/manual_csv_upload/yyyy=2026/mm=06/dd=05/x.csv',
  row_count: 42,
  received_ts: '2026-06-05T10:00:00Z',
  status: 'received',
}

function okResponse(body: unknown): Response {
  return { ok: true, status: 201, json: async () => body } as unknown as Response
}

function errResponse(status: number, code: string, details: Record<string, unknown> = {}): Response {
  return {
    ok: false,
    status,
    json: async () => ({ error: { code, message: `failed: ${code}`, trace_id: null, details } }),
  } as unknown as Response
}

const sampleFile = () => new File(['a,b\n1,2\n'], 'batch.csv', { type: 'text/csv' })

const baseArgs = {
  token: 'tok-123',
  templateId: '0190ac10-5a00-7000-8a00-0000000000a1',
  storeCode: 'TX-102',
}

beforeEach(() => {
  vi.stubEnv('VITE_DIS_UI_SERVER_BASE_URL', 'http://test.local')
})

afterEach(() => {
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
  localStorage.clear()
})

describe('uploadCsv (real POST /api/v1/csv-uploads)', () => {
  it('sends a multipart POST with file + template_id + store_code and no source_id/intent', async () => {
    const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<Response>>(
      async () => okResponse(RESULT),
    )
    vi.stubGlobal('fetch', fetchMock)

    await uploadCsv({ ...baseArgs, file: sampleFile() })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('http://test.local/api/v1/csv-uploads')
    expect(init.method).toBe('POST')
    expect((init.headers as Record<string, string>).authorization).toBe('Bearer tok-123')

    // The body is a real FormData with exactly the contract parts.
    const form = init.body as FormData
    expect(form).toBeInstanceOf(FormData)
    expect(form.get('template_id')).toBe(baseArgs.templateId)
    expect(form.get('store_code')).toBe('TX-102')
    expect(form.get('file')).toBeInstanceOf(File)
    // The server derives source_id from the template lineage; there is no intent field.
    expect(form.get('source_id')).toBeNull()
    expect(form.get('intent')).toBeNull()
    // Content-Type is NOT set by us (the browser sets the multipart boundary).
    expect((init.headers as Record<string, string>)['content-type']).toBeUndefined()
  })

  it('parses CsvUploadResult on 201', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => okResponse(RESULT)))
    const result = await uploadCsv({ ...baseArgs, file: sampleFile() })
    expect(result.status).toBe('received')
    expect(result.upload_id).toMatch(/^us_[a-z0-9]{12}$/)
    expect(result.row_count).toBe(42)
    expect(result.source_id).toBe('manual_csv_upload')
  })

  it('maps each error status to a DisUiServerHttpError carrying status + code', async () => {
    const cases: Array<[number, string, Record<string, unknown>]> = [
      [404, 'resource_not_found', {}],
      [409, 'mapping_state_conflict', {}],
      [409, 'store_state_conflict', {}],
      [413, 'payload_too_large', {}],
      [422, 'upload_structure', { reason: 'not_utf8' }],
      [503, 'storage', {}],
    ]
    for (const [status, code, details] of cases) {
      vi.stubGlobal('fetch', vi.fn(async () => errResponse(status, code, details)))
      const err = await uploadCsv({ ...baseArgs, file: sampleFile() }).catch((e: unknown) => e)
      expect(err).toBeInstanceOf(DisUiServerHttpError)
      const e = err as DisUiServerHttpError
      expect(e.status).toBe(status)
      expect(e.code).toBe(code)
      if (status === 422) {
        expect(e.details.reason).toBe('not_utf8')
      }
    }
  })
})

describe('uploadCsvWithSessionToken', () => {
  it('reads the session token from storage and uploads', async () => {
    localStorage.setItem('dis-ui.dev.authToken', 'stored-tok')
    const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<Response>>(
      async () => okResponse(RESULT),
    )
    vi.stubGlobal('fetch', fetchMock)

    await uploadCsvWithSessionToken({ file: sampleFile(), templateId: baseArgs.templateId, storeCode: 'TX-102' })

    const [, init] = fetchMock.mock.calls[0]
    expect((init.headers as Record<string, string>).authorization).toBe('Bearer stored-tok')
  })

  it('throws when no session token is held', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => okResponse(RESULT)))
    await expect(
      uploadCsvWithSessionToken({ file: sampleFile(), templateId: baseArgs.templateId, storeCode: 'TX-102' }),
    ).rejects.toThrow(/no session token/)
  })
})
