import { Loader2 } from 'lucide-react'

// Reusable loading state (craft spec): a spinner + label. role=status so it is
// announced and queryable; screens use this rather than rolling their own.
export function LoadingState({ label = 'Loading...' }: { label?: string }) {
  return (
    <div role="status" className="flex items-center gap-2 p-6 text-caption text-muted-foreground">
      <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
      {label}
    </div>
  )
}
