import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { AuthSnapshot } from '../../auth/AuthSnapshot'
import { clearToken, writeToken } from '../../auth/storage'
import { DisUiServerHttpError } from './client'
import { getStoresOnboarded } from './stores'

const tenant: AuthSnapshot = {
  userId: 'u_acmeuser0001',
  tenantId: 't_acme9k2l1mn4',
  storeId: 's_acme0001a4b7',
  roles: ['dis:read'],
}

// T2: OnboardedStore fixtures shaped to the real /api/v1/stores-onboarded contract.
describe('stores-onboarded fixtures (shaped to the real contract)', () => {
  it('returns the tenant stores with the contract fields', async () => {
    const stores = await getStoresOnboarded(tenant)
    expect(stores.length).toBeGreaterThan(0)
    for (const store of stores) {
      for (const key of [
        'store_id',
        'name',
        'store_code',
        'status',
        'country',
        'timezone',
        'currency',
        'tax_treatment',
      ]) {
        expect(store).toHaveProperty(key)
      }
      expect(['opening', 'active', 'inactive', 'closed']).toContain(store.status)
      expect(['inclusive', 'exclusive']).toContain(store.tax_treatment)
    }
  })

  it('serves store_code as null when absent (nullable at source, D55)', async () => {
    const stores = await getStoresOnboarded(tenant)
    expect(stores.some((s) => s.store_code === null)).toBe(true)
    expect(stores.some((s) => typeof s.store_code === 'string')).toBe(true)
  })
})

// T10: real-mode wiring (mocked fetch; dis-ui-server is not run locally).
describe('stores-onboarded real mode (T10)', () => {
  beforeEach(() => {
    vi.stubEnv('VITE_DIS_UI_SERVER_MODE', 'real')
    vi.stubEnv('VITE_DIS_UI_SERVER_BASE_URL', 'http://test.local')
    writeToken('tok-123')
  })
  afterEach(() => {
    vi.unstubAllEnvs()
    vi.unstubAllGlobals()
    clearToken()
  })

  const REAL_STORES = [
    {
      store_id: '0190ac20-6b00-7000-8b00-0000000000c1',
      name: 'Acme Downtown #1',
      store_code: 'TX-102',
      status: 'active',
      country: 'US',
      timezone: 'America/New_York',
      currency: 'USD',
      tax_treatment: 'exclusive',
    },
  ]

  it('GETs /api/v1/stores-onboarded with a Bearer and parses OnboardedStore[]', async () => {
    const fetchMock = vi.fn<(url: string, init: RequestInit) => Promise<Response>>(async () => ({ ok: true, status: 200, json: async () => REAL_STORES }) as unknown as Response)
    vi.stubGlobal('fetch', fetchMock)
    const stores = await getStoresOnboarded(tenant)
    expect(stores).toEqual(REAL_STORES)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('http://test.local/api/v1/stores-onboarded')
    expect((init as RequestInit).headers).toMatchObject({ authorization: 'Bearer tok-123' })
  })

  it('maps a non-2xx to DisUiServerHttpError', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: false, status: 403, json: async () => ({ error: { code: 'tenant_scope' } }) }) as unknown as Response),
    )
    await expect(getStoresOnboarded(tenant)).rejects.toBeInstanceOf(DisUiServerHttpError)
  })
})
