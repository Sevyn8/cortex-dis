import type * as React from 'react'

import { cn } from '@/lib/utils'

// Label. NOT from the admin-frontend (its components/ui has no label.tsx); built from
// our design tokens in the shadcn-canonical treatment so it matches the base-nova look.
// Styled native <label>; the DIS screens use native labels for their forms.
function Label({ className, ...props }: React.ComponentProps<'label'>) {
  return (
    <label
      data-slot="label"
      className={cn(
        'flex items-center gap-2 text-sm font-medium leading-none select-none group-data-[disabled=true]:pointer-events-none group-data-[disabled=true]:opacity-50 peer-disabled:cursor-not-allowed peer-disabled:opacity-50',
        className,
      )}
      {...props}
    />
  )
}

export { Label }
