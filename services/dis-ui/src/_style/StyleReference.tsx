import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { HeroCard } from '@/components/ui/hero-card'
import { ProgressRail } from '@/components/ui/progress-rail'
import { Button } from '@/components/ui/button'
import { StatusBadge } from '../components/StatusBadge'
import type { StatusTone } from '../components/StatusBadge'
import { SOURCE_IDENTITIES, sourceIdentity } from '../components/source-identity'
import type { SourceTypeKey } from '../components/source-identity'
import { cn } from '@/lib/utils'

// Dev-only style reference (redesign R1 de-risk gate). NOT in the tenant/ops nav; reached
// at the public /dev/style route. Renders the new visual language - the source-type
// identities, the HeroCard, the ProgressRail, the semantic StatusBadge tones, and Card
// variants - in one page so the language can be eyeballed in light AND dark (toggle via
// the usual theme control). It inherits ThemeProvider; it does not theme-toggle itself.
// Mirrors the _smoke de-risk pattern. Renders nothing tenant-scoped and calls no backend.

const IDENTITY_ORDER: SourceTypeKey[] = ['csv', 'shopify_pos', 'square', 'other']

const JOURNEY_STEPS = ['Connect/Upload', 'Review mapping', 'Preview', 'Go live']

const TONES: StatusTone[] = ['success', 'warning', 'danger', 'info', 'neutral']

export function StyleReference() {
  const csv = sourceIdentity('csv')
  return (
    <section className="mx-auto flex max-w-4xl flex-col gap-8 p-8">
      <header>
        <h1 className="text-display-lg">DIS UI style reference</h1>
        <p className="text-body text-muted-foreground">
          Redesign R1 visual language. Dev-only; toggle light and dark to verify both modes.
        </p>
      </header>

      <div className="flex flex-col gap-3">
        <h2 className="text-heading">Source-type identities</h2>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {IDENTITY_ORDER.map((key) => {
            const identity = SOURCE_IDENTITIES[key]
            const Icon = identity.icon
            return (
              <div
                key={identity.key}
                className={cn('flex items-center gap-3 rounded-md border p-3', identity.borderClass)}
              >
                <span
                  aria-hidden="true"
                  className={cn(
                    'flex size-9 items-center justify-center rounded-lg',
                    identity.bgSoftClass,
                    identity.textClass,
                  )}
                >
                  <Icon className="size-5" />
                </span>
                <div className="flex flex-col">
                  <span className="text-body-strong">{identity.label}</span>
                  <span className="text-micro text-muted-foreground">
                    {identity.live ? 'Live' : 'Soon'}
                  </span>
                </div>
                <span className={cn('ml-auto size-3 rounded-full', identity.dotClass)} aria-hidden="true" />
              </div>
            )
          })}
        </div>
      </div>

      <div className="flex flex-col gap-3">
        <h2 className="text-heading">Hero card</h2>
        <HeroCard
          identity={csv}
          title="Upload a CSV"
          description="Drag and drop a sales export. We map it to the canonical schema, you approve."
        >
          <div>
            <Button type="button">Choose a file</Button>
          </div>
        </HeroCard>
      </div>

      <div className="flex flex-col gap-3">
        <h2 className="text-heading">Progress rail</h2>
        <ProgressRail steps={JOURNEY_STEPS} current={1} />
      </div>

      <div className="flex flex-col gap-3">
        <h2 className="text-heading">Status tones</h2>
        <div className="flex flex-wrap gap-2">
          {TONES.map((tone) => (
            <StatusBadge key={tone} tone={tone}>
              {tone}
            </StatusBadge>
          ))}
        </div>
      </div>

      <div className="flex flex-col gap-3">
        <h2 className="text-heading">Card</h2>
        <Card>
          <CardHeader>
            <CardTitle>Card title</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-body text-muted-foreground">
              The base flat card, unchanged. The hero card above is the new characterful variant.
            </p>
          </CardContent>
        </Card>
      </div>
    </section>
  )
}
