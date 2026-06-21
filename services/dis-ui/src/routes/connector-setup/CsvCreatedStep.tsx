import { CheckCircle2 } from 'lucide-react'

import { Card, CardContent } from '@/components/ui/card'
import { StatusBadge } from '../../components/StatusBadge'
import type { ConnectorWizardState } from './state'

// CSV branch step 6 (Template created), terminal. HONEST about the Slice-16a synthetic-201
// reality: the create returns a real fresh template_id but persists NOTHING (no rules assembled,
// draft v1 / no active version) until backend persistence (16c) lands. So this does NOT claim the
// template is live/listable/ingestible, and does NOT link into the registry/detail (it would 404
// there until 16c). It reads the response gracefully (active_version may be null) and shows a
// "submitted / accepted" state with a clear pending note. The D88 create-as-ACTIVE alignment from
// Sanjeev is expected before deploy; if a future response carries an active version, it surfaces.
export function CsvCreatedStep({ state }: { state: ConnectorWizardState }) {
  const created = state.createdTemplate
  if (created === null) {
    return null
  }
  const isLive = created.activeVersion !== null
  const versionLabel = isLive
    ? `Active v${created.activeVersion}`
    : created.draftVersion !== null
      ? `Draft v${created.draftVersion}`
      : 'Submitted'
  return (
    <div className="flex max-w-2xl flex-col gap-5">
      <div className="flex items-center gap-2">
        <CheckCircle2 aria-hidden="true" className="size-5 text-success" />
        <span className="text-body-strong text-success" role="status">
          {isLive ? 'Created and live' : 'Submitted'}
        </span>
      </div>

      <Card className="p-5">
        <CardContent className="flex flex-col gap-4 p-0">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-body-strong">{created.templateName}</span>
            <StatusBadge tone={isLive ? 'success' : 'neutral'}>{versionLabel}</StatusBadge>
          </div>
          <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="flex flex-col gap-0.5">
              <dt className="text-caption text-muted-foreground">Template type</dt>
              <dd className="text-body">{created.templateType}</dd>
            </div>
            <div className="flex flex-col gap-0.5">
              <dt className="text-caption text-muted-foreground">Template ID</dt>
              <dd className="font-mono text-sm text-muted-foreground">{created.templateId}</dd>
            </div>
          </dl>
          {isLive ? (
            <p className="text-caption text-muted-foreground">
              New files for this source are now processed through this mapping.
            </p>
          ) : (
            <p className="text-caption text-muted-foreground">
              Your mapping was submitted and accepted. It will not appear in the template registry
              or accept data ingestion until backend persistence is enabled; check back once that
              lands.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
