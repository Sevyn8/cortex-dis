import { Check } from 'lucide-react'

import { cn } from '@/lib/utils'

// Compact "chip" step indicator, scoped to the Connect a System surface (Chunk 1). This is a
// LOCAL component, deliberately NOT the shared ProgressRail: the connector wizard has 8 steps
// and needs the mockup's tight single-row pill stepper with three distinct states. The shared
// ProgressRail (used by the CSV journey and the existing POS connect step) is left untouched so
// no other surface changes.
//
// Layout: one flex row, small gaps, allowed to wrap only when the viewport is genuinely too
// narrow (flex-wrap fallback); at normal width all chips sit on one line. Each chip is a compact
// rounded-full pill (~3px x 8px padding, 11px text) with an inline marker (a check icon for done,
// the step number otherwise) and the step label, no wrapping inside the chip.
//
// State tokens map to the repo's existing semantic utilities (light/dark safe, no hardcoded hex),
// the same ones StatusBadge uses:
//   done     -> success tint + check icon   (bg-success/10 text-success)
//   current  -> info tint + bold number     (bg-info/10 text-info)
//   upcoming -> muted + number              (bg-muted text-muted-foreground)
export type StepRailProps = {
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

const CHIP_CLASS: Record<StepState, string> = {
  done: 'bg-success/10 text-success',
  current: 'bg-info/10 text-info',
  upcoming: 'bg-muted text-muted-foreground',
}

export function StepRail({ steps, current }: StepRailProps) {
  return (
    <ol className="flex flex-wrap items-center gap-1.5" aria-label="Progress">
      {steps.map((label, index) => {
        const state = stepState(index, current)
        return (
          <li
            key={label}
            data-slot="step-chip"
            data-state={state}
            aria-current={state === 'current' ? 'step' : undefined}
            className={cn(
              'flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[13px] leading-none whitespace-nowrap',
              CHIP_CLASS[state],
            )}
          >
            {state === 'done' ? (
              <Check aria-hidden="true" className="size-[14px]" />
            ) : (
              <span
                aria-hidden="true"
                className={cn('tabular-nums', state === 'current' && 'font-medium')}
              >
                {index + 1}
              </span>
            )}
            {label}
          </li>
        )
      })}
    </ol>
  )
}
