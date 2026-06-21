import { Boxes, FileSpreadsheet, ShoppingBag, Square } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

// Source-type identity (redesign R1): the SINGLE source of truth mapping each source
// type to its color, icon, and label. Consumed by the connector picker (R2), the CSV
// journey (R3), the thin POS connect step (R5), and the Dashboard breakdown (R6) so the
// whole product reads as one. Do not duplicate this mapping anywhere (FM3).
//
// The Tailwind classes are LITERAL strings, not interpolated from the token name:
// Tailwind v4 only generates a utility for a class string it sees verbatim in source, so
// `bg-source-csv` must appear as a literal here (a `bg-${var}` form would never compile).
// The color tokens themselves live in index.css (--source-*, surfaced as --color-source-*).
export type SourceTypeKey = 'csv' | 'shopify_pos' | 'square' | 'other'

export type SourceIdentity = {
  key: SourceTypeKey
  label: string
  // Visual only: the lucide icon for this source type.
  icon: LucideIcon
  // CSV is the live source (built deep); the POS connectors are coming-soon (built thin).
  live: boolean
  // Literal Tailwind classes for this identity's color (see note above).
  textClass: string
  bgSoftClass: string
  borderClass: string
  dotClass: string
}

export const SOURCE_IDENTITIES: Record<SourceTypeKey, SourceIdentity> = {
  csv: {
    key: 'csv',
    label: 'CSV upload',
    icon: FileSpreadsheet,
    live: true,
    textClass: 'text-source-csv',
    bgSoftClass: 'bg-source-csv/10',
    borderClass: 'border-source-csv',
    dotClass: 'bg-source-csv',
  },
  shopify_pos: {
    key: 'shopify_pos',
    label: 'Shopify POS',
    icon: ShoppingBag,
    live: false,
    textClass: 'text-source-shopify-pos',
    bgSoftClass: 'bg-source-shopify-pos/10',
    borderClass: 'border-source-shopify-pos',
    dotClass: 'bg-source-shopify-pos',
  },
  square: {
    key: 'square',
    label: 'Square',
    icon: Square,
    live: false,
    textClass: 'text-source-square',
    bgSoftClass: 'bg-source-square/10',
    borderClass: 'border-source-square',
    dotClass: 'bg-source-square',
  },
  other: {
    key: 'other',
    label: 'Other POS/ERP',
    icon: Boxes,
    live: false,
    textClass: 'text-source-other',
    bgSoftClass: 'bg-source-other/10',
    borderClass: 'border-source-other',
    dotClass: 'bg-source-other',
  },
}

// Resolve a source-type key to its identity. An unknown key degrades to `other` (the
// generic POS/ERP identity) rather than throwing, so a new or unmapped source type still
// renders with a sensible neutral identity.
export function sourceIdentity(key: string): SourceIdentity {
  return SOURCE_IDENTITIES[key as SourceTypeKey] ?? SOURCE_IDENTITIES.other
}
