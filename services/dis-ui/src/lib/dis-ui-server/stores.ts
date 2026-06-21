import { useQuery } from '@tanstack/react-query'

import type { AuthSnapshot } from '../../auth/AuthSnapshot'
import { getJson } from './client'
import { isRealMode } from './mode'

// Onboarded-stores endpoint (slice 14b, D70). Shaped EXACTLY to the real contract
// (services/dis-ui-server/.../schemas/stores.py:OnboardedStore), served as a bare array at
// GET /api/v1/stores-onboarded, tenant-scoped (in-query predicate, identity_mirror RLS-off).
// Mode-aware (T10): real mode calls the live endpoint; fixture mode (default) returns the
// inlined fixtures, so local dev + tests work with no backend. Surfaces the locale-relevant
// store attributes (currency, timezone, tax_treatment) the UI shows as read-only context.

export type StoreStatus = 'opening' | 'active' | 'inactive' | 'closed'
export type StoreTaxTreatment = 'inclusive' | 'exclusive'

export type OnboardedStore = {
  store_id: string // internal UUID, lowercase string (opaque to the UI)
  name: string
  store_code: string | null // nullable at source (D55), served as-is
  status: StoreStatus
  country: string
  timezone: string
  currency: string // ISO 4217
  tax_treatment: StoreTaxTreatment
}

const STORE_FIXTURES: Record<string, OnboardedStore[]> = {
  t_acme9k2l1mn4: [
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
    {
      store_id: '0190ac20-6b00-7000-8b00-0000000000c2',
      name: 'Acme Uptown #2',
      store_code: null,
      status: 'opening',
      country: 'US',
      timezone: 'America/Chicago',
      currency: 'USD',
      tax_treatment: 'exclusive',
    },
  ],
}

// GET /api/v1/stores-onboarded -> the tenant's onboarded stores (own-tenant only). Real
// mode calls the live endpoint (tenant scoped server-side from the token); fixture mode
// returns the inlined tenant fixtures.
export async function getStoresOnboarded(snapshot: AuthSnapshot): Promise<OnboardedStore[]> {
  if (isRealMode()) {
    return getJson<OnboardedStore[]>('/api/v1/stores-onboarded')
  }
  return [...(STORE_FIXTURES[snapshot.tenantId ?? ''] ?? [])]
}

export function useStoresOnboarded(snapshot: AuthSnapshot | null) {
  return useQuery({
    queryKey: ['dis-ui-server', 'stores-onboarded', snapshot?.tenantId ?? 'none'],
    queryFn: () => getStoresOnboarded(snapshot as AuthSnapshot),
    enabled: snapshot !== null,
    staleTime: Infinity,
    retry: false,
  })
}
