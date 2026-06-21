import { useState } from 'react'

import { useAuth } from '../auth/useAuth'
import { Button } from '@/components/ui/button'
import { EmptyState } from '../components/states/EmptyState'
import { ErrorState } from '../components/states/ErrorState'
import { LoadingState } from '../components/states/LoadingState'
import { StatusBadge } from '../components/StatusBadge'
import type { StatusTone } from '../components/StatusBadge'
import { cn } from '@/lib/utils'
import {
  useMarkAllRead,
  useMarkRead,
  useNotifications,
} from '../lib/dis-ui-server/notifications'
import type { NotificationFilter, Severity } from '../lib/dis-ui-server/notifications'

const FILTERS: { value: NotificationFilter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'unread', label: 'Unread' },
  { value: 'errors', label: 'Errors' },
]

function severityTone(severity: Severity): StatusTone {
  if (severity === 'error') {
    return 'danger'
  }
  if (severity === 'warning') {
    return 'warning'
  }
  return 'info'
}

// Notifications (surface map screen 9), TENANT slice, on the design-system craft bar.
// List (demand list 6.2) with a segmented filter; severity as a semantic badge;
// per-row mark-read (6.3) and mark-all-read (6.4). The header bell reads the unread
// count (6.1); the mutations invalidate the shared query prefix so the bell updates.
// Read-only otherwise (the `link` field is not wired this checkpoint).
export function Notifications() {
  const { snapshot } = useAuth()
  const [filter, setFilter] = useState<NotificationFilter>('all')
  const list = useNotifications(snapshot, filter)
  const markRead = useMarkRead(snapshot)
  const markAllRead = useMarkAllRead(snapshot)

  return (
    <section className="flex flex-col gap-4">
      <header className="flex items-baseline justify-between">
        <div>
          <h1 className="text-display">Notifications</h1>
          <p className="text-caption text-muted-foreground">Alerts for this tenant.</p>
        </div>
        <Button type="button" variant="outline" size="sm" onClick={() => markAllRead.mutate()}>
          Mark all read
        </Button>
      </header>

      <div className="inline-flex w-fit rounded-md border border-border p-0.5 text-sm">
        {FILTERS.map((f) => (
          <button
            key={f.value}
            type="button"
            onClick={() => setFilter(f.value)}
            className={cn(
              'rounded px-2.5 py-1 transition-colors',
              filter === f.value
                ? 'bg-secondary font-medium text-secondary-foreground'
                : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {f.label}
          </button>
        ))}
      </div>

      <div>{renderBody()}</div>
    </section>
  )

  function renderBody() {
    if (list.isPending) {
      return <LoadingState label="Loading notifications..." />
    }
    if (list.isError) {
      return <ErrorState message="Could not load notifications." onRetry={() => void list.refetch()} />
    }
    if (list.data.length === 0) {
      return <EmptyState title="No notifications" message="Nothing to show for this filter." />
    }
    return (
      <ul className="flex flex-col gap-2 text-sm">
        {list.data.map((n) => (
          <li
            key={n.id}
            className={cn('rounded-md border border-border p-3', n.read ? 'bg-card' : 'bg-surface-raised/40')}
          >
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <StatusBadge tone={severityTone(n.severity)}>{n.severity}</StatusBadge>
                <span className={cn(!n.read && 'font-medium')}>{n.text}</span>
              </div>
              {n.read ? (
                <span className="text-caption text-muted-foreground">read</span>
              ) : (
                <Button type="button" variant="ghost" size="xs" onClick={() => markRead.mutate(n.id)}>
                  Mark read
                </Button>
              )}
            </div>
            <div className="mt-1 text-caption text-muted-foreground">
              {n.source} · {n.at}
            </div>
          </li>
        ))}
      </ul>
    )
  }
}
