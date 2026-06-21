import type * as React from 'react'

import { cn } from '@/lib/utils'
import type { SourceIdentity } from '../source-identity'

// HeroCard (redesign R1): a larger, more characterful card than the flat base Card, for
// the live hero surface (the CSV upload hero in the connector picker, R2) and anywhere a
// surface needs hero weight. Token-driven (radius-2xl, the identity's color), light+dark.
// Composition primitive: it owns the shell, the icon chip, the title/description, and a
// children slot for the call to action. Behavior is the caller's.
export function HeroCard({
  identity,
  title,
  description,
  className,
  children,
  ...props
}: React.ComponentProps<'div'> & {
  // The source-type identity supplying the icon + accent color (single source of truth).
  identity: SourceIdentity
  title: string
  description?: string
}) {
  const Icon = identity.icon
  return (
    <div
      data-slot="hero-card"
      className={cn(
        'flex flex-col gap-4 rounded-2xl border bg-card p-6 text-card-foreground',
        'border-l-4',
        identity.borderClass,
        className,
      )}
      {...props}
    >
      <div className="flex items-start gap-4">
        <span
          aria-hidden="true"
          className={cn(
            'flex size-12 shrink-0 items-center justify-center rounded-xl',
            identity.bgSoftClass,
            identity.textClass,
            '[&_svg]:size-6',
          )}
        >
          <Icon />
        </span>
        <div className="flex flex-col gap-1">
          <h2 className="text-display-lg">{title}</h2>
          {description !== undefined ? (
            <p className="text-body text-muted-foreground">{description}</p>
          ) : null}
        </div>
      </div>
      {children}
    </div>
  )
}
