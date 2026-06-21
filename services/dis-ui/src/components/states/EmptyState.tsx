import { Inbox } from 'lucide-react'
import type { ReactNode } from 'react'

// Reusable empty state (craft spec): icon + heading + guidance + optional action
// (children). Used across screens and the not-found route.
export function EmptyState({
  title,
  message,
  children,
}: {
  title: string
  message?: string
  children?: ReactNode
}) {
  return (
    <section className="flex flex-col items-center gap-2 p-10 text-center">
      <span className="flex h-10 w-10 items-center justify-center rounded-full bg-muted text-muted-foreground">
        <Inbox aria-hidden="true" className="h-5 w-5" />
      </span>
      <h2 className="text-subheading">{title}</h2>
      {message !== undefined ? <p className="text-caption text-muted-foreground">{message}</p> : null}
      {children}
    </section>
  )
}
