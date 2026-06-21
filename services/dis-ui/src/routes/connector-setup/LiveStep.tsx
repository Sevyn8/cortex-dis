import { CheckCircle2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { StatusBadge } from '../../components/StatusBadge'
import { connectorSpec } from '../../lib/dis-ui-server/connectors-catalog'
import type { ConnectorWizardState } from './state'

// Step 8 (Live): the "source is live" confirmation plus a source card (status, last sync,
// records, reconnect / pause). All STUBBED: the source was created via the connectors-api
// stub; the reconnect / pause controls are non-functional placeholders for the wired build.
export function LiveStep({ state }: { state: ConnectorWizardState }) {
  const spec = state.connector === null ? null : connectorSpec(state.connector)
  const source = state.liveSource
  if (spec === null || source === null) {
    return null
  }
  return (
    <div className="flex max-w-2xl flex-col gap-5">
      <div className="flex items-center gap-2">
        <CheckCircle2 aria-hidden="true" className="size-5 text-success" />
        <span className="text-body-strong text-success" role="status">
          {spec.label} is live
        </span>
      </div>

      <Card className="p-5">
        <CardContent className="flex flex-col gap-4 p-0">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-body-strong">{source.sourceName}</span>
            <StatusBadge tone={source.status === 'live' ? 'success' : 'neutral'}>
              {source.status === 'live' ? 'Live' : 'Paused'}
            </StatusBadge>
          </div>
          <dl className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <div className="flex flex-col gap-0.5">
              <dt className="text-caption text-muted-foreground">Connector</dt>
              <dd className="text-body">{spec.label}</dd>
            </div>
            <div className="flex flex-col gap-0.5">
              <dt className="text-caption text-muted-foreground">Last sync</dt>
              <dd className="text-body">{source.lastSyncAt ?? 'Not yet run'}</dd>
            </div>
            <div className="flex flex-col gap-0.5">
              <dt className="text-caption text-muted-foreground">Records synced</dt>
              <dd className="text-body">{source.recordsSynced}</dd>
            </div>
          </dl>
          <div className="flex flex-wrap gap-2">
            {/* Stubbed controls: non-functional placeholders for the wired build. */}
            <Button type="button" variant="outline" size="sm" disabled>
              Reconnect
            </Button>
            <Button type="button" variant="outline" size="sm" disabled>
              Pause
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
