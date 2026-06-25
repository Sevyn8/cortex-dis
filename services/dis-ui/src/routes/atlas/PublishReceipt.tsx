import { CheckCircle2 } from 'lucide-react'
import type { ReactNode } from 'react'
import { Link, useLocation, useParams } from 'react-router'

import { buttonVariants } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { StatusBadge } from '../../components/StatusBadge'
import type { PublishReceipt as PublishReceiptModel } from '../../lib/dis-ui-server/atlas'

// The publish-receipt surface (A4 PR3b): renders the PublishReceipt the console got back from
// POST /atlas/drafts/{id}/publish, passed via router location state. On a direct visit / refresh
// (no state) it degrades to a link into the published-schema detail.
export function PublishReceipt() {
  const { draftId } = useParams<{ draftId: string }>()
  const location = useLocation()
  const receipt = (location.state ?? null) as PublishReceiptModel | null

  if (receipt === null) {
    return (
      <section className="mx-auto flex w-full max-w-[640px] flex-col gap-4">
        <h1 className="text-display">Publish receipt</h1>
        <p className="text-body text-muted-foreground">
          The receipt is shown right after publishing. View the published schema instead.
        </p>
        {draftId !== undefined ? (
          <Link
            to={`/atlas/published/${draftId}`}
            className={buttonVariants({ variant: 'default' })}
          >
            View published schema
          </Link>
        ) : null}
      </section>
    )
  }

  return (
    <section className="mx-auto flex w-full max-w-[640px] flex-col gap-6">
      <header className="flex items-center gap-3">
        <CheckCircle2 aria-hidden="true" className="size-7 text-success" />
        <h1 className="text-display">Published</h1>
      </header>

      <Card>
        <CardContent className="flex flex-col gap-3">
          <Row label="Vertical" value={<span className="font-mono">{receipt.vertical}</span>} />
          <Row label="Status" value={<StatusBadge tone="success">{receipt.status}</StatusBadge>} />
          <Row label="Schema version" value={`v${receipt.schema_version}`} />
          <Row
            label="Publish audit"
            value={
              receipt.audit_emitted ? (
                <StatusBadge tone="success">emitted</StatusBadge>
              ) : (
                // Honest: the CM action-ledger binding is A5; the audit sink may not be wired yet.
                <StatusBadge tone="neutral">not emitted (CM binding lands in A5)</StatusBadge>
              )
            }
          />
        </CardContent>
      </Card>

      <div className="flex gap-3">
        <Link to="/atlas" className={buttonVariants({ variant: 'default' })}>
          Back to registry
        </Link>
        <Link
          to={`/atlas/published/${receipt.draft_id}`}
          className={buttonVariants({ variant: 'outline' })}
        >
          View published schema
        </Link>
      </div>
    </section>
  )
}

function Row({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <span className="text-caption text-muted-foreground">{label}</span>
      <span className="text-body text-foreground">{value}</span>
    </div>
  )
}
