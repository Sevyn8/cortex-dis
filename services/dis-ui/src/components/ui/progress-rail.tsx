import { CheckCircle2, Circle } from 'lucide-react'

import { cn } from '@/lib/utils'

// ProgressRail (redesign R1): the consistent 4-step rail for the source journey
// (Connect/Upload, Review mapping, Preview, Go live). Pure presentational: the caller
// passes the step labels and the current index; the rail renders done / current /
// upcoming states. Token-driven (primary for done+current, muted for upcoming),
// light+dark. The shared journey rail for the CSV journey (R3) and the thin POS connect
// step (R5).
export type ProgressRailProps = {
  steps: string[]
  // Zero-based index of the active step. Steps before it are done; after it, upcoming.
  current: number
}

type StepState = 'done' | 'current' | 'upcoming'

function stepState(index: number, current: number): StepState {
  if (index < current) {
    return 'done'
  }
  if (index === current) {
    return 'current'
  }
  return 'upcoming'
}

export function ProgressRail({ steps, current }: ProgressRailProps) {
  return (
    <ol className="flex flex-wrap items-center gap-2" aria-label="Progress">
      {steps.map((label, index) => {
        const state = stepState(index, current)
        return (
          <li
            key={label}
            data-slot="progress-step"
            data-state={state}
            aria-current={state === 'current' ? 'step' : undefined}
            className="flex items-center gap-2"
          >
            <span
              className={cn(
                'flex items-center gap-1.5 text-caption',
                state === 'upcoming' ? 'text-muted-foreground' : 'text-foreground',
              )}
            >
              {state === 'done' ? (
                <CheckCircle2 aria-hidden="true" className="size-4 text-primary" />
              ) : state === 'current' ? (
                <span
                  aria-hidden="true"
                  className="flex size-4 items-center justify-center rounded-full bg-primary"
                >
                  <span className="size-1.5 rounded-full bg-primary-foreground" />
                </span>
              ) : (
                <Circle aria-hidden="true" className="size-4 text-muted-foreground" />
              )}
              {label}
            </span>
            {index < steps.length - 1 ? (
              <span aria-hidden="true" className="h-px w-6 bg-border" />
            ) : null}
          </li>
        )
      })}
    </ol>
  )
}
