import { Copy } from 'lucide-react'

import { cn } from '@/lib/utils'

// Copy affordance for mono identifiers (trace_id, source_id) per the craft spec. A
// small icon button; clipboard access is guarded so it is inert where the API is
// absent (e.g. jsdom under tests). Purely a UI convenience; carries no data behavior.
export function CopyButton({ value, label = 'Copy', className }: { value: string; label?: string; className?: string }) {
  return (
    <button
      type="button"
      aria-label={label}
      onClick={() => {
        void navigator.clipboard?.writeText(value)
      }}
      className={cn(
        'inline-flex h-5 w-5 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-muted hover:text-foreground',
        className,
      )}
    >
      <Copy aria-hidden="true" className="h-3 w-3" />
    </button>
  )
}
