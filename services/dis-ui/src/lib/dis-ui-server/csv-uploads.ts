import { readToken } from '../../auth/storage'
import { postMultipart } from './client'

// ===========================================================================================
// REAL caller - the FIRST real-mode HTTP call in the UI (Slice 8, D72).
//
// THE SEAM: every other module in this folder is fixture-backed - its getters call
// `ensureFixtureMode` and THROW in real mode (slice 13 deferred). This module is the
// exception: `uploadCsv` ALWAYS performs a live multipart POST to dis-ui-server's
// `POST /api/v1/csv-uploads`, in both modes, because the file genuinely must reach GCS.
// Nothing else in the UI is flipped to real; the fixture seam is unchanged everywhere else.
//
// It requires a configured backend: `getBaseUrl()` (VITE_DIS_UI_SERVER_BASE_URL) and a
// session token (auth/storage.readToken()). A missing base URL throws the existing config
// error - we fail loud rather than silently fake an upload.
//
// HONESTY (D71 resolved by slice-8a): the upload validates the template is ACTIVE and carries
// `template_id` end to end, and the streaming consumer is now template-keyed, so it maps the
// batch through the template's ACTIVE mapping version. Callers may say a batch is "ingested
// through the template's active mapping version", with two caveats: the version applied is the
// one active at consume time (not pinned at upload), and mapping is asynchronous (the 201 means
// received, not mapped). The result returns no mapping_version_id, so never claim a specific
// applied version.
// ===========================================================================================

// The 201 body, mirroring services/dis-ui-server/schemas/csv_uploads.py:CsvUploadResult.
// Identity values are the RESOLVED internal UUIDs (served as lowercase strings to the UI).
export type CsvUploadResult = {
  trace_id: string
  upload_id: string // ^us_[a-z0-9]{12}$ - deterministic per logical upload (D58 dedup)
  tenant_id: string
  store_id: string
  store_code: string
  source_id: string // derived from the template lineage, never sent by the client
  template_id: string
  gcs_uri: string
  row_count: number // tier-0 observed data rows (excluding the header)
  received_ts: string
  status: 'received'
}

export type CsvUploadArgs = {
  token: string
  file: File
  templateId: string
  storeCode: string
}

// POST /api/v1/csv-uploads - multipart: file + template_id + store_code. The server derives
// source_id from the template lineage (we never send it), and there is no `intent` field.
// Throws DisUiServerHttpError on a non-2xx (the caller maps status/code to a message).
export async function uploadCsv(args: CsvUploadArgs): Promise<CsvUploadResult> {
  const form = new FormData()
  form.append('file', args.file)
  form.append('template_id', args.templateId)
  form.append('store_code', args.storeCode)
  return postMultipart<CsvUploadResult>('/api/v1/csv-uploads', { token: args.token, form })
}

// Convenience: read the session token from storage (the token home, auth/storage.ts) and
// upload. Throws a clear error when no token is held (should not happen behind AuthBoundary).
export async function uploadCsvWithSessionToken(args: Omit<CsvUploadArgs, 'token'>): Promise<CsvUploadResult> {
  const token = readToken()
  if (token === null || token.length === 0) {
    throw new Error('no session token: cannot upload (sign in first)')
  }
  return uploadCsv({ ...args, token })
}
