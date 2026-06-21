import { Boxes, FileSpreadsheet, ShoppingBag, Square } from 'lucide-react'

import { SOURCE_IDENTITIES, sourceIdentity } from './source-identity'

describe('source identity (single source of truth)', () => {
  it('maps csv to the live CSV identity with its icon and literal classes', () => {
    const csv = sourceIdentity('csv')
    expect(csv.label).toBe('CSV upload')
    expect(csv.icon).toBe(FileSpreadsheet)
    expect(csv.live).toBe(true)
    expect(csv.textClass).toBe('text-source-csv')
    expect(csv.bgSoftClass).toBe('bg-source-csv/10')
    expect(csv.borderClass).toBe('border-source-csv')
    expect(csv.dotClass).toBe('bg-source-csv')
  })

  it('maps the POS connectors as coming-soon (not live) with their icons', () => {
    expect(sourceIdentity('shopify_pos').icon).toBe(ShoppingBag)
    expect(sourceIdentity('shopify_pos').live).toBe(false)
    expect(sourceIdentity('square').icon).toBe(Square)
    expect(sourceIdentity('square').live).toBe(false)
    expect(sourceIdentity('other').icon).toBe(Boxes)
    expect(sourceIdentity('other').live).toBe(false)
  })

  it('uses source-scoped literal classes for every identity (Tailwind v4 can generate them)', () => {
    for (const key of ['csv', 'shopify_pos', 'square', 'other'] as const) {
      const id = SOURCE_IDENTITIES[key]
      expect(id.textClass).toMatch(/^text-source-/)
      expect(id.bgSoftClass).toMatch(/^bg-source-.+\/10$/)
      expect(id.borderClass).toMatch(/^border-source-/)
      expect(id.dotClass).toMatch(/^bg-source-/)
    }
  })

  it('falls back to the Other identity for an unknown key', () => {
    const unknown = sourceIdentity('manual_csv_upload')
    expect(unknown.key).toBe('other')
    expect(unknown).toBe(SOURCE_IDENTITIES.other)
  })
})
