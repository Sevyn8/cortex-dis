import { useQuery } from '@tanstack/react-query'

import { getJson } from './client'
import { isRealMode } from './mode'

// Template types catalog (Chunk 2, WIRED). The packet axis the user picks BEFORE mapping:
// GET /api/v1/template-types returns a bare array, identical for every tenant. Shaped EXACTLY
// to the real dis-ui-server contract
// (services/dis-ui-server/src/dis_ui_server/schemas/template_types.py:TemplateType). The KEYS
// are the single in-code vocabulary (dis_validation.TEMPLATE_TYPES); display_name/description
// are operator-facing copy authored in the BFF. Mode-aware (T10): real mode calls the live
// endpoint; fixture mode (default + tests) returns the inlined list, so local dev needs no
// backend. The chosen key parameterises GET /template-mapping-fields?template_type=<key>.
export type TemplateType = {
  key: string // a member of dis_validation.TEMPLATE_TYPES (sales | inventory_change | snapshot)
  display_name: string
  description: string
}

// Mirrors the BFF's authored presentation copy as of this commit; the live endpoint is the
// source of truth at real mode. Order is the offering order on the Template type step.
export const TEMPLATE_TYPES_FIXTURE: TemplateType[] = [
  {
    key: 'sales',
    display_name: 'Sales',
    description: 'Point-of-sale transactions: each row is a sold (or returned) line item.',
  },
  {
    key: 'inventory_change',
    display_name: 'Inventory change',
    description: 'Stock movements: each row is a change in on-hand quantity for a SKU.',
  },
  {
    key: 'snapshot',
    display_name: 'Catalogue snapshot',
    description: 'Current-position rows: the latest price, cost, or stock state per SKU.',
  },
]

// GET /api/v1/template-types. Tenant-independent. Real mode calls the live endpoint; fixture
// mode returns the inlined list.
export async function getTemplateTypes(): Promise<TemplateType[]> {
  if (isRealMode()) {
    return getJson<TemplateType[]>('/api/v1/template-types')
  }
  return [...TEMPLATE_TYPES_FIXTURE]
}

export function useTemplateTypes() {
  return useQuery({
    queryKey: ['dis-ui-server', 'template-types'],
    queryFn: getTemplateTypes,
    staleTime: Infinity,
    retry: false,
  })
}
