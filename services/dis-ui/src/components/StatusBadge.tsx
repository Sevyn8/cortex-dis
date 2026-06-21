import type { ReactNode } from 'react'

import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

// Semantic status badge (slice 23 craft spec): renders a status / health / severity /
// state value as a Badge pill in a semantic tone. The base-nova Badge primitive has no
// success/warning/info variant, so the semantic tokens are applied here at the call
// site rather than by editing the copied primitive (FM5). Children render verbatim.
export type StatusTone = 'success' | 'warning' | 'danger' | 'info' | 'neutral'

const TONE_CLASS: Record<StatusTone, string> = {
  success: 'border-transparent bg-success/10 text-success',
  warning: 'border-transparent bg-warning/10 text-warning',
  danger: 'border-transparent bg-danger/10 text-danger',
  info: 'border-transparent bg-info/10 text-info',
  neutral: 'border-transparent bg-muted text-muted-foreground',
}

export function StatusBadge({
  tone,
  className,
  children,
}: {
  tone: StatusTone
  className?: string
  children: ReactNode
}) {
  return <Badge className={cn(TONE_CLASS[tone], className)}>{children}</Badge>
}
