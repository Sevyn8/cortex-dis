import { Link, useParams } from 'react-router'

import { useAuth } from '../auth/useAuth'
import { buttonVariants } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { ActiveMappingSummary } from '../components/ActiveMappingSummary'
import { EmptyState } from '../components/states/EmptyState'
import { ErrorState } from '../components/states/ErrorState'
import { LoadingState } from '../components/states/LoadingState'
import { StatusBadge } from '../components/StatusBadge'
import type { StatusTone } from '../components/StatusBadge'
import { activeTemplateVersion, useMappingTemplate } from '../lib/dis-ui-server/mapping-templates'
import type { MappingTemplateVersion, TemplateStatus } from '../lib/dis-ui-server/mapping-templates'
import { useTemplateMappingFieldsForType } from '../lib/dis-ui-server/mapping-fields'
import type { CatalogField } from '../lib/dis-ui-server/mapping-fields'
import { useStoresOnboarded } from '../lib/dis-ui-server/stores'

function statusTone(status: TemplateStatus): StatusTone {
  if (status === 'active') {
    return 'success'
  }
  if (status === 'staged') {
    return 'warning'
  }
  if (status === 'draft') {
    return 'info'
  }
  return 'neutral'
}

// Template detail (T2), at /sources/:sourceId/templates/:templateId. The active version
// is what recurring batches reuse, so it is made prominent. The active version's mapping
// is shown as TWO distinct concerns: FIELD mappings (column -> canonical field, enriched
// from the T1 catalog) and FORMAT rules (normalize/cast/derive: date format, decimal
// separator). Store locale context (currency/timezone/tax) is read-only here.
export function TemplateDetail() {
  const { templateId } = useParams()
  const { snapshot } = useAuth()
  const detail = useMappingTemplate(snapshot, templateId ?? null)
  // Type-aware catalog keyed on the template's own template_type (null while the detail
  // loads, or if the type is absent -> the hook is gated, no param-less 400).
  const fields = useTemplateMappingFieldsForType(detail.data?.template_type ?? null)
  const stores = useStoresOnboarded(snapshot)

  if (templateId === undefined) {
    return <EmptyState title="No template" message="No template id in the URL." />
  }
  if (detail.isPending) {
    return <LoadingState label="Loading template..." />
  }
  // The detail GET is throw-style 404 (real contract): unknown -> error state.
  if (detail.isError || detail.data === undefined) {
    return <ErrorState message="Could not load this template." />
  }

  const template = detail.data
  const active = activeTemplateVersion(template)
  const catalog: CatalogField[] = fields.data ?? []

  return (
    <section className="flex flex-col gap-6">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h1 className="text-display">{template.template_name}</h1>
          <p className="text-caption text-muted-foreground">
            Template for {template.source_id}. The active version is reused for every new batch.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          {active !== null ? (
            <StatusBadge tone="success">Active: v{active.version}</StatusBadge>
          ) : (
            <StatusBadge tone="neutral">No active version</StatusBadge>
          )}
          {/* T5/T4-real: ingest a batch against this template's active mapping. Gated on an
              active version existing - you cannot ingest against a never-activated template. */}
          {active !== null ? (
            <Link
              to={`/sources/${template.source_id}/templates/${template.template_id}/upload`}
              className={buttonVariants({ variant: 'default', size: 'sm' })}
            >
              Ingest data
            </Link>
          ) : (
            <button
              type="button"
              disabled
              title="No active version to ingest against yet"
              className={buttonVariants({ variant: 'default', size: 'sm' })}
            >
              Ingest data
            </button>
          )}
        </div>
      </header>
      {active === null ? (
        <p className="text-caption text-muted-foreground">
          No active version yet. Activate a mapping before ingesting a batch.
        </p>
      ) : null}

      {/* Version lineage */}
      <Card>
        <CardHeader>
          <CardTitle>Version history</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Version</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Fields</TableHead>
                  <TableHead>Rules</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead>Active window</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {template.versions.map((version: MappingTemplateVersion) => (
                  <TableRow key={version.mapping_version_id}>
                    <TableCell className="font-medium text-foreground">
                      v{version.version}
                    </TableCell>
                    <TableCell>
                      <StatusBadge tone={statusTone(version.status)}>{version.status}</StatusBadge>
                    </TableCell>
                    <TableCell className="text-muted-foreground">{version.field_count}</TableCell>
                    <TableCell className="text-muted-foreground">
                      {version.transform_count}
                    </TableCell>
                    <TableCell className="text-muted-foreground">{version.created_at}</TableCell>
                    <TableCell className="text-muted-foreground">
                      {version.activated_at === null
                        ? '-'
                        : `${version.activated_at} to ${version.deprecated_at ?? 'current'}`}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>

      {/* The active version's mapping, split into the two concerns (read-only). The same
          display is reused by the recurring-batch upload flow (T4). */}
      {active !== null ? <ActiveMappingSummary version={active} catalog={catalog} /> : null}

      {/* Store locale context (read-only; the editable locale declaration is a later slice). */}
      <Card>
        <CardHeader>
          <CardTitle>Store context</CardTitle>
          <p className="text-caption text-muted-foreground">
            Locale facts from your onboarded stores. Used as defaults; mapping store binding is
            resolved at ingest.
          </p>
        </CardHeader>
        <CardContent>
          {stores.isPending ? (
            <LoadingState label="Loading stores..." />
          ) : stores.isError || stores.data === undefined || stores.data.length === 0 ? (
            <p className="text-caption text-muted-foreground">No onboarded stores.</p>
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Store</TableHead>
                    <TableHead>Currency</TableHead>
                    <TableHead>Timezone</TableHead>
                    <TableHead>Tax</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {stores.data.map((store) => (
                    <TableRow key={store.store_id}>
                      <TableCell className="font-medium text-foreground">{store.name}</TableCell>
                      <TableCell className="text-muted-foreground">{store.currency}</TableCell>
                      <TableCell className="text-muted-foreground">{store.timezone}</TableCell>
                      <TableCell className="text-muted-foreground">{store.tax_treatment}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>
    </section>
  )
}
