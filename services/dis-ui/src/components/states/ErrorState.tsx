import { TriangleAlert } from 'lucide-react'

import { Button } from '@/components/ui/button'

// Reusable error state (craft spec): icon + message + optional retry action. Named
// ErrorState (not Error) to avoid shadowing the global. role=alert so it is announced.
export function ErrorState({
  message = 'Something went wrong.',
  onRetry,
}: {
  message?: string
  onRetry?: () => void
}) {
  return (
    <div role="alert" className="flex flex-col items-center gap-2 p-10 text-center">
      <span className="flex h-10 w-10 items-center justify-center rounded-full bg-danger/10 text-danger">
        <TriangleAlert aria-hidden="true" className="h-5 w-5" />
      </span>
      <p className="text-body-strong text-foreground">{message}</p>
      {onRetry !== undefined ? (
        <Button type="button" variant="outline" size="sm" onClick={onRetry}>
          Retry
        </Button>
      ) : null}
    </div>
  )
}
