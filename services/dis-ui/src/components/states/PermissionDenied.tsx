import { ShieldX } from 'lucide-react'

// Reusable permission-denied state (craft spec): icon + heading + guidance. Built for
// reuse, not wired to any route yet (no ops/cross-tenant surface exists; FM3 gates on
// isOps only). role=alert so it is announced. It lands on a route with the first ops
// surface.
export function PermissionDenied({
  message = 'You do not have access to this area.',
}: {
  message?: string
}) {
  return (
    <div role="alert" className="flex flex-col items-center gap-2 p-10 text-center">
      <span className="flex h-10 w-10 items-center justify-center rounded-full bg-muted text-muted-foreground">
        <ShieldX aria-hidden="true" className="h-5 w-5" />
      </span>
      <h2 className="text-subheading">Access denied</h2>
      <p className="text-caption text-muted-foreground">{message}</p>
    </div>
  )
}
