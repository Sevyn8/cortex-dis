import { CheckCircle2 } from 'lucide-react'

import { Card, CardContent } from '@/components/ui/card'
import { StatusBadge } from '../../components/StatusBadge'
import { connectorSpec } from '../../lib/dis-ui-server/connectors-catalog'
import type { ConnectorWizardState } from './state'

// Step 3 (Authorized): the connected / verified confirmation. The account summary is the
// STUBBED account returned by the connectors-api authorize/submit (business name placeholder,
// read-only badge, token-stored note). No real provider call happened.
export function AuthorizedStep({ state }: { state: ConnectorWizardState }) {
  const spec = state.connector === null ? null : connectorSpec(state.connector)
  const account = state.account
  if (spec === null || account === null) {
    return null
  }
  return (
    <Card className="max-w-2xl p-5">
      <CardContent className="flex flex-col gap-4 p-0">
        <div className="flex items-center gap-2">
          <CheckCircle2 aria-hidden="true" className="size-5 text-success" />
          <span className="text-body-strong">Connected to {spec.label}</span>
          <StatusBadge tone="success">Verified</StatusBadge>
        </div>
        <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div className="flex flex-col gap-0.5">
            <dt className="text-caption text-muted-foreground">Business</dt>
            <dd className="text-body">{account.businessName}</dd>
          </div>
          <div className="flex flex-col gap-0.5">
            <dt className="text-caption text-muted-foreground">Account</dt>
            <dd className="font-mono text-sm text-muted-foreground">{account.accountId}</dd>
          </div>
        </dl>
        <div className="flex flex-wrap items-center gap-2">
          {account.readOnly ? <StatusBadge tone="info">Read-only access</StatusBadge> : null}
          {account.tokenStored ? (
            <span className="text-caption text-muted-foreground">
              Access token stored server-side.
            </span>
          ) : null}
        </div>
      </CardContent>
    </Card>
  )
}
