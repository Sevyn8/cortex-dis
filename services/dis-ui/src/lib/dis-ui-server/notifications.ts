import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import type { AuthSnapshot } from '../../auth/AuthSnapshot'
import { SERVER_MODE } from './mode'
import { QUARANTINE_TRACE_IDS } from './quarantine'

// Notifications endpoints (demand list 6.1-6.4), tenant slice. Fixture mode
// (default); real mode is OPEN (slice 13) and throws. Shapes are PROVISIONAL
// pending Sanjeev's slices 15-17.

// Severity is enumerated by surface map screen 9 (info / warning / error); 6.2's
// example only showed "warning". Provisional pending the real contract.
export type Severity = 'info' | 'warning' | 'error'
export type NotificationFilter = 'unread' | 'all' | 'errors'

// 6.2 shape. `link` is kept per the contract but is NOT wired to navigation this
// checkpoint: 6.2's example target (/quarantine?source=...) uses a query our
// tenant-slice screens do not consume (underspecified; see the slice plan).
export type Notification = {
  id: string
  severity: Severity
  text: string
  source: string
  at: string
  read: boolean
  link: string
}

export type UnreadCount = { unread: number }

// SEED (tenant t_acme9k2l1mn4). Grounded on the kind-style source names and the
// quarantined trace id (QUARANTINE_TRACE_IDS.acmeCanonical) used by the Audit /
// Quarantine fixtures. Initial: 3 unread, 1 read; one error.
const SEED: Record<string, Notification[]> = {
  t_acme9k2l1mn4: [
    {
      id: 'ntf_0001',
      severity: 'warning',
      text: '2 rows quarantined',
      source: 'Shopify POS',
      at: '2026-06-03T09:08:00Z',
      read: false,
      link: '/quarantine',
    },
    {
      id: 'ntf_0002',
      severity: 'info',
      text: 'Mapping v2 promoted to active',
      source: 'Manual CSV Upload',
      at: '2026-06-03T08:30:00Z',
      read: false,
      link: '/sources/manual_csv_upload/mappings',
    },
    {
      id: 'ntf_0003',
      severity: 'error',
      text: `Canonical-shape validation failed for trace ${QUARANTINE_TRACE_IDS.acmeCanonical}`,
      source: 'Manual CSV Upload',
      at: '2026-06-03T09:08:05Z',
      read: false,
      link: '/audit',
    },
    {
      id: 'ntf_0004',
      severity: 'info',
      text: 'Source connected',
      source: 'Manual CSV Upload',
      at: '2026-06-01T12:00:00Z',
      read: true,
      link: '/sources',
    },
  ],
}

function cloneSeed(): Record<string, Notification[]> {
  return Object.fromEntries(
    Object.entries(SEED).map(([tenant, list]) => [tenant, list.map((n) => ({ ...n }))]),
  )
}

// Mutable in-memory store (mark-read / mark-all-read must move the unread count
// the header bell reads). Unlike the read-only fixtures, this one mutates.
let store: Record<string, Notification[]> = cloneSeed()

// Test-only: restore the SEED so mutations do not bleed between tests.
export function __resetNotificationsFixture(): void {
  store = cloneSeed()
}

function tenantList(snapshot: AuthSnapshot): Notification[] {
  return store[snapshot.tenantId ?? ''] ?? []
}

function ensureFixtureMode(fn: string): void {
  if (SERVER_MODE === 'real') {
    throw new Error(`real-mode ${fn} is not implemented (slice 13)`)
  }
}

export async function getNotifications(
  snapshot: AuthSnapshot,
  filter: NotificationFilter,
): Promise<Notification[]> {
  ensureFixtureMode('getNotifications()')
  const list = tenantList(snapshot)
  if (filter === 'unread') {
    return list.filter((n) => !n.read)
  }
  if (filter === 'errors') {
    return list.filter((n) => n.severity === 'error')
  }
  return list
}

export async function getUnreadCount(snapshot: AuthSnapshot): Promise<UnreadCount> {
  ensureFixtureMode('getUnreadCount()')
  return { unread: tenantList(snapshot).filter((n) => !n.read).length }
}

export async function markNotificationRead(snapshot: AuthSnapshot, id: string): Promise<void> {
  ensureFixtureMode('markNotificationRead()')
  const notification = tenantList(snapshot).find((n) => n.id === id)
  if (notification !== undefined) {
    notification.read = true
  }
}

export async function markAllNotificationsRead(snapshot: AuthSnapshot): Promise<void> {
  ensureFixtureMode('markAllNotificationsRead()')
  for (const notification of tenantList(snapshot)) {
    notification.read = true
  }
}

const NOTIFICATIONS_KEY = ['dis-ui-server', 'notifications'] as const

export function useNotifications(snapshot: AuthSnapshot | null, filter: NotificationFilter) {
  return useQuery({
    queryKey: [...NOTIFICATIONS_KEY, 'list', snapshot?.tenantId ?? 'none', filter],
    queryFn: () => getNotifications(snapshot as AuthSnapshot, filter),
    enabled: snapshot !== null,
    staleTime: Infinity,
    retry: false,
  })
}

export function useUnreadCount(snapshot: AuthSnapshot | null) {
  return useQuery({
    queryKey: [...NOTIFICATIONS_KEY, 'unread-count', snapshot?.tenantId ?? 'none'],
    queryFn: () => getUnreadCount(snapshot as AuthSnapshot),
    enabled: snapshot !== null,
    staleTime: Infinity,
    retry: false,
  })
}

// Mutations invalidate the shared NOTIFICATIONS_KEY prefix, refetching both the
// list and the unread-count (so the bell and the screen stay consistent).
export function useMarkRead(snapshot: AuthSnapshot | null) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => markNotificationRead(snapshot as AuthSnapshot, id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: NOTIFICATIONS_KEY }),
  })
}

export function useMarkAllRead(snapshot: AuthSnapshot | null) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => markAllNotificationsRead(snapshot as AuthSnapshot),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: NOTIFICATIONS_KEY }),
  })
}
