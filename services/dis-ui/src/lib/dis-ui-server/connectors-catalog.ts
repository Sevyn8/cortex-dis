import { Clover, ShoppingBag, Square } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

// Live Sync connector catalog (Chunk 1). PURE CONFIG, no I/O: the three POS connectors the
// new "Connect a System" surface offers (Shopify, Square, Clover) with their auth methods,
// the "what you will need" notes, the pre-auth field shown before authorizing, and the
// per-connector API-token fields for the token branch.
//
// DELIBERATELY SELF-CONTAINED visuals (local lucide icons + neutral tone classes): this
// surface does NOT reuse components/source-identity.ts. That helper has no Clover identity
// and is wired into the existing Add Source surface (/connect); duplicating a tiny visual
// config here keeps the old surface at zero risk and avoids touching index.css color tokens.
//
// PROVISIONAL + UI-PROPOSED: these credential shapes are the planned forms, NOT a confirmed
// auth model. Sanjeev's API doc confirms the real per-connector auth (OAuth scopes, fields,
// secret handling) when the connectors get wired; the UI proposes, he confirms.

export type ConnectorKey = 'shopify' | 'square' | 'clover'

// A single credential / pre-auth input. `select` carries `options`; `text` carries an
// optional placeholder. `secret` is a text input rendered as a password field.
export type ConnectorField = {
  name: string
  label: string
  kind: 'text' | 'secret' | 'select'
  placeholder?: string
  options?: { value: string; label: string }[]
}

export type ConnectorAuthMethod = 'oauth' | 'api_token'

export type ConnectorSpec = {
  key: ConnectorKey
  label: string
  // One-line description shown under the connector name on the Source step card.
  description: string
  // Visual only: the lucide icon + a neutral tone class pair for the tile/header chip.
  icon: LucideIcon
  iconClass: string
  iconBgClass: string
  // Copy for the "Sign in with [POS]" recommended path.
  oauthLabel: string
  // What the operator should have ready before authorizing (shown in the Connect step).
  whatYouWillNeed: string[]
  // The single field collected BEFORE authorizing on the OAuth path (domain / region).
  preAuthField: ConnectorField
  // The fields collected on the "Use an API token" branch.
  tokenFields: ConnectorField[]
}

// Region options reused by Square + Clover (PROVISIONAL list).
const REGION_OPTIONS: { value: string; label: string }[] = [
  { value: 'us', label: 'United States' },
  { value: 'eu', label: 'Europe' },
  { value: 'ca', label: 'Canada' },
  { value: 'uk', label: 'United Kingdom' },
  { value: 'au', label: 'Australia' },
]

export const CONNECTOR_SPECS: Record<ConnectorKey, ConnectorSpec> = {
  shopify: {
    key: 'shopify',
    label: 'Shopify',
    description: 'Sync orders, products, and inventory from your Shopify store.',
    icon: ShoppingBag,
    iconClass: 'text-foreground',
    iconBgClass: 'bg-muted',
    oauthLabel: 'Sign in with Shopify',
    whatYouWillNeed: [
      'Your Shopify store domain (for example your-store.myshopify.com).',
      'Permission to install the Sevyn8 app on that store.',
    ],
    preAuthField: {
      name: 'shop_domain',
      label: 'Shopify store domain',
      kind: 'text',
      placeholder: 'your-store.myshopify.com',
    },
    tokenFields: [
      {
        name: 'shop_domain',
        label: 'Shopify store domain',
        kind: 'text',
        placeholder: 'your-store.myshopify.com',
      },
      { name: 'admin_api_token', label: 'Admin API access token', kind: 'secret' },
    ],
  },
  square: {
    key: 'square',
    label: 'Square',
    description: 'Pull sales and catalogue across all your Square locations.',
    icon: Square,
    iconClass: 'text-foreground',
    iconBgClass: 'bg-muted',
    oauthLabel: 'Sign in with Square',
    whatYouWillNeed: [
      'The region your Square account is registered in.',
      'Permission to authorize the Sevyn8 app for that account.',
    ],
    preAuthField: {
      name: 'region',
      label: 'Square region',
      kind: 'select',
      options: REGION_OPTIONS,
    },
    tokenFields: [
      { name: 'region', label: 'Square region', kind: 'select', options: REGION_OPTIONS },
      { name: 'access_token', label: 'Access token', kind: 'secret' },
    ],
  },
  clover: {
    key: 'clover',
    label: 'Clover',
    description: 'Connect your Clover merchant account for live transaction data.',
    icon: Clover,
    iconClass: 'text-foreground',
    iconBgClass: 'bg-muted',
    oauthLabel: 'Sign in with Clover',
    whatYouWillNeed: [
      'The region your Clover merchant account is registered in.',
      'Your Clover merchant ID.',
      'Permission to authorize the Sevyn8 app for that merchant.',
    ],
    preAuthField: {
      name: 'region',
      label: 'Clover region',
      kind: 'select',
      options: REGION_OPTIONS,
    },
    tokenFields: [
      { name: 'region', label: 'Clover region', kind: 'select', options: REGION_OPTIONS },
      { name: 'merchant_id', label: 'Merchant ID', kind: 'text' },
      { name: 'api_token', label: 'API token', kind: 'secret' },
    ],
  },
}

// Display order of the connector tiles in the Source step.
export const CONNECTOR_ORDER: ConnectorKey[] = ['shopify', 'square', 'clover']

// Resolve a connector spec by key, or null for an unknown key.
export function connectorSpec(key: string): ConnectorSpec | null {
  return CONNECTOR_SPECS[key as ConnectorKey] ?? null
}
