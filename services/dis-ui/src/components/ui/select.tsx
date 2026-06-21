import type * as React from 'react'

import { cn } from '@/lib/utils'

// Select. NOT from the admin-frontend (its components/ui has no select.tsx); built from
// our design tokens to match the Input treatment (base-nova). Styled NATIVE <select>:
// the DIS screens use native selects for their filters, so this keeps their behavior
// intact for the screen rebuild (no Base UI Select rewrite, which has no source to match).
function Select({ className, children, ...props }: React.ComponentProps<'select'>) {
  return (
    <select
      data-slot="select"
      className={cn(
        'h-8 w-full min-w-0 rounded-md border border-input bg-transparent px-2.5 py-1 text-sm transition-colors outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 dark:bg-input/30',
        className,
      )}
      {...props}
    >
      {children}
    </select>
  )
}

export { Select }
