import { Bell, Database, FileSearch, LayoutDashboard, Plug, ShieldAlert } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

// T7: nav items are grouped into labeled sections (Ithina-console look). The section is a
// visual/structural grouping only; it changes no route, target, label, icon, or ops-gating.
// OPERATIONS was retired with the Ops Fleet screen (its last item; Query was removed earlier),
// so the section is gone; the remaining ops surfaces are the scope-aware MONITORING screens.
export type NavSection = 'OVERVIEW' | 'DATA' | 'MONITORING'

// Render order of the section groups.
export const NAV_SECTION_ORDER: NavSection[] = ['OVERVIEW', 'DATA', 'MONITORING']

export type NavItem = {
  label: string
  to: string
  // The labeled section this item lives under (T7). Grouping/visual only.
  section: NavSection
  // Visual only (slice 23): the sidebar icon shown beside the label (and alone when
  // collapsed). Does not affect routing or gating.
  icon?: LucideIcon
  // Ops-only item: rendered only when isOps(snapshot) is true. The flag is the single gate
  // (the Sidebar drops a section with no visible items). No ops-only items remain today, but
  // the flag is kept for future ops surfaces.
  ops?: boolean
}

// Tenant navigation. Dashboard is the index (`/`), the single landing for every persona;
// Mappings is intentionally NOT a top-level item (reached via a source,
// /sources/:sourceId/mappings). T7 tags each item with its section.
export const NAV_ITEMS: NavItem[] = [
  { label: 'Dashboard', to: '/', icon: LayoutDashboard, section: 'OVERVIEW' },
  // DATA: the source/template lifecycle, action-first. "Upload Data" is the flat template
  // registry (/ingest), one card per template with View + per-template upload. "Connect a
  // System" (/connectors/new) is the single add-source surface (POS Live Sync branches +
  // CSV/SFTP branch); the old "Add Source" picker (/connect) and /upload wizard were retired.
  { label: 'Upload Data', to: '/ingest', icon: Database, section: 'DATA' },
  { label: 'Connect a System', to: '/connectors/new', icon: Plug, section: 'DATA' },
  // MONITORING: Quarantine and Audit are scope-aware (T9): a tenant sees their own tenant
  // (scope locked, no tenant filter); an ops user sees the fleet-wide view with a tenant
  // filter, via the SAME route/screen (the component branches on isOps). Always visible.
  { label: 'Quarantine', to: '/quarantine', icon: ShieldAlert, section: 'MONITORING' },
  { label: 'Audit', to: '/audit', icon: FileSearch, section: 'MONITORING' },
  { label: 'Notifications', to: '/notifications', icon: Bell, section: 'MONITORING' },
]
