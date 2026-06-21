import { PanelLeftClose, PanelLeftOpen } from 'lucide-react'
import { useState } from 'react'
import { NavLink } from 'react-router'

import { isOps } from '../auth/AuthSnapshot'
import { useAuth } from '../auth/useAuth'
import { cn } from '@/lib/utils'
import { BrandMark } from './BrandMark'
import { NAV_ITEMS, NAV_SECTION_ORDER } from './nav'
import type { NavItem } from './nav'

// The sidebar, restyled (T7) to match the Ithina Superadmin Console: a brand header
// (placeholder mark + "Ithina" wordmark + "DATA PLATFORM" subtitle), nav grouped into
// labeled uppercase sections, thin-stroke icons, regular-weight labels, generous spacing,
// and a subtle light-grey active highlight via the --sidebar-accent token (dark-mode-safe).
// Behavior is unchanged from before: items flagged `ops` render only for an ops snapshot
// (isOps), `items` is injectable so tests can verify the gate, active-route highlighting
// uses NavLink's isActive, and collapse (w-60 expanded / w-16 collapsed) still works. A
// section whose items are all filtered out renders no header (so OPERATIONS auto-hides for
// tenants). When collapsed, the wordmark/subtitle/section headers/labels become sr-only.
export function Sidebar({ items = NAV_ITEMS }: { items?: NavItem[] }) {
  const { snapshot } = useAuth()
  const ops = snapshot !== null && isOps(snapshot)
  const visible = items.filter((item) => (item.ops === true ? ops : true))
  const [collapsed, setCollapsed] = useState(false)

  // Group visible items by section, in NAV_SECTION_ORDER; drop empty sections so a section
  // with no visible item (e.g. OPERATIONS for a tenant) renders neither header nor items.
  const sections = NAV_SECTION_ORDER.map((section) => ({
    section,
    items: visible.filter((item) => item.section === section),
  })).filter((group) => group.items.length > 0)

  return (
    <nav
      aria-label="Primary"
      className={cn(
        'sticky top-0 flex h-screen shrink-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground transition-[width] duration-150',
        collapsed ? 'w-16' : 'w-64',
      )}
    >
      {/* Brand header: placeholder mark + wordmark + subtitle (FM3, BrandMark is the swap point). */}
      <div className={cn('flex items-center gap-3 px-4 py-5', collapsed && 'justify-center px-0')}>
        <BrandMark />
        <span className={cn('flex flex-col leading-none', collapsed && 'sr-only')}>
          <span className="text-subheading text-sidebar-foreground">DIS</span>
          <span className="mt-1 text-micro tracking-[0.12em] text-sidebar-foreground/55">
            DATA PLATFORM
          </span>
        </span>
      </div>

      <div className="flex flex-1 flex-col gap-5 overflow-y-auto px-3 pb-3">
        {sections.map((group) => (
          <div key={group.section} className="flex flex-col gap-1">
            <h2
              className={cn(
                'px-2 pb-1 text-label text-sidebar-foreground/55',
                collapsed && 'sr-only',
              )}
            >
              {group.section}
            </h2>
            <ul className="flex flex-col gap-0.5">
              {group.items.map((item) => {
                const Icon = item.icon
                return (
                  <li key={item.to}>
                    <NavLink
                      to={item.to}
                      end={item.to === '/'}
                      className={({ isActive }) =>
                        cn(
                          'flex items-center gap-3 rounded-md px-3 py-2 text-body transition-colors',
                          isActive
                            ? 'bg-sidebar-accent text-sidebar-accent-foreground'
                            : 'text-sidebar-foreground/70 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground',
                        )
                      }
                    >
                      {Icon ? (
                        <Icon aria-hidden="true" strokeWidth={1.5} className="size-[18px] shrink-0" />
                      ) : null}
                      <span className={cn(collapsed && 'sr-only')}>{item.label}</span>
                    </NavLink>
                  </li>
                )
              })}
            </ul>
          </div>
        ))}
      </div>

      <button
        type="button"
        aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        onClick={() => setCollapsed((c) => !c)}
        className="m-3 flex h-9 items-center justify-center gap-2 rounded-md border border-sidebar-border bg-sidebar-accent/20 text-xs text-sidebar-foreground/60 transition-colors duration-150 hover:bg-surface-raised hover:text-sidebar-foreground"
      >
        {collapsed ? (
          <PanelLeftOpen aria-hidden="true" strokeWidth={1.5} className="h-4 w-4" />
        ) : (
          <PanelLeftClose aria-hidden="true" strokeWidth={1.5} className="h-4 w-4" />
        )}
        <span className={cn(collapsed && 'sr-only')}>Collapse</span>
      </button>
    </nav>
  )
}
