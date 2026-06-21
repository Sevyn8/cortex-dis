import { Outlet } from 'react-router'

import { useAuth } from '../auth/useAuth'
import { NotificationBell } from '../components/NotificationBell'
import { Sidebar } from '../components/Sidebar'
import { Button } from '@/components/ui/button'
import { useMe } from '../lib/dis-ui-server/me'
import { ThemeToggle } from '../theme/ThemeToggle'

// Authenticated shell on the design-system chrome (slice 23, craft bar): a sticky top
// bar (h-14, border-b, backdrop-blur) carrying the notification bell, the theme toggle,
// and the user (avatar initial + email) with a logout control; a collapsible sidebar; and
// the routed page body. T8: the DIS brand lives ONLY in the sidebar header now (one brand
// on screen), so the top bar no longer carries its own mark/wordmark. The header is a /me
// consumer (demand list 1.1), reading the display email from the profile call and falling
// back to the token's userId (email is not a token claim).
export function AppLayout() {
  const { snapshot, logout } = useAuth()
  const { data } = useMe(snapshot)

  const display = data?.email ?? snapshot?.userId ?? ''
  const initial = display.charAt(0).toUpperCase() || '?'

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 flex h-14 items-center gap-4 border-b border-border bg-background/95 px-6 backdrop-blur supports-[backdrop-filter]:bg-background/80">
        {/* T8: brand removed here; it lives once, in the sidebar header. The user/controls
            group stays right-aligned via ml-auto. */}
        <div className="ml-auto flex items-center gap-3">
          <NotificationBell />
          <ThemeToggle />
          <div className="flex items-center gap-2">
            <span
              aria-hidden="true"
              className="flex h-7 w-7 items-center justify-center rounded-full bg-muted text-xs font-medium text-foreground"
            >
              {initial}
            </span>
            <span className="text-sm text-foreground-muted">{display}</span>
          </div>
          <Button variant="ghost" size="sm" onClick={logout}>
            Log out
          </Button>
        </div>
      </header>
      <div className="flex">
        <Sidebar />
        <main className="flex-1 p-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
