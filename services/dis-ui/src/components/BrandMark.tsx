import { Boxes } from 'lucide-react'

import { cn } from '@/lib/utils'

// PLACEHOLDER DIS logo mark (T7/T8). This is the ONLY place the brand mark is defined:
// swap the real DIS logo asset in here (replace the rounded-square + lucide glyph with an
// <img src={disLogo} /> or an inline SVG) and the brand header picks it up everywhere.
// Decorative by default (aria-hidden); the adjacent "DIS" wordmark carries the name. Color
// comes from the sidebar-primary token (the single restrained blue accent), so it is
// dark-mode-safe and not a hardcoded hex.
export function BrandMark({ className }: { className?: string }) {
  return (
    <span
      aria-hidden="true"
      data-testid="brand-mark"
      data-placeholder="dis-logo"
      className={cn(
        'flex size-8 shrink-0 items-center justify-center rounded-lg bg-sidebar-primary text-sidebar-primary-foreground',
        className,
      )}
    >
      <Boxes className="size-5" strokeWidth={1.5} />
    </span>
  )
}
