import { Bell } from 'lucide-react'
import { Link } from 'react-router'

import { useAuth } from '../auth/useAuth'
import { useUnreadCount } from '../lib/dis-ui-server/notifications'
import { cn } from '@/lib/utils'

// Header bell (demand list 6.1), on the design-system chrome (slice 23): a Bell icon
// link to /notifications with a small unread-count badge. The aria-label still encodes
// the count and stays distinct from the sidebar's "Notifications" nav link, so each is
// addressable on its own (behavior and accessible name unchanged from Checkpoint 2).
export function NotificationBell() {
  const { snapshot } = useAuth()
  const { data } = useUnreadCount(snapshot)
  const unread = data?.unread ?? 0

  return (
    <Link
      to="/notifications"
      aria-label={`Notifications, ${unread} unread`}
      className="relative inline-flex h-8 w-8 items-center justify-center rounded-md text-foreground-muted transition-colors hover:bg-muted hover:text-foreground"
    >
      <Bell aria-hidden="true" className="h-4 w-4" />
      {unread > 0 ? (
        <span
          aria-hidden="true"
          className={cn(
            'absolute -top-0.5 -right-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-danger px-1 text-[10px] font-medium text-primary-foreground',
          )}
        >
          {unread}
        </span>
      ) : null}
    </Link>
  )
}
