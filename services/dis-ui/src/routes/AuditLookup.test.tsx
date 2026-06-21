import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import type { AuthSnapshot } from '../auth/AuthSnapshot'
import { AUDIT_HEALTHY_TRACE_ID } from '../lib/dis-ui-server/audit'
import { QUARANTINE_TRACE_IDS } from '../lib/dis-ui-server/quarantine'
import { renderWithProviders } from '../test/renderWithProviders'
import { AuditLookup } from './AuditLookup'

const UNKNOWN_TRACE_ID = '0190ac0e-1a01-7001-8a01-0000000000ff'

const tenant: AuthSnapshot = {
  userId: 'u_acmeuser0001',
  tenantId: 't_acme9k2l1mn4',
  storeId: 's_acme0001a4b7',
  roles: ['dis:read'],
}

async function lookup(traceId: string) {
  const user = userEvent.setup()
  renderWithProviders(<AuditLookup />, { snapshot: tenant })
  await user.type(screen.getByLabelText('Trace ID'), traceId)
  await user.click(screen.getByRole('button', { name: /look up/i }))
}

describe('AuditLookup', () => {
  it('renders the full lifecycle for a known trace, with mapping_version_id on mapped', async () => {
    await lookup(AUDIT_HEALTHY_TRACE_ID)
    expect(await screen.findByText(/received/)).toBeInTheDocument()
    expect(screen.getByText(/validated/)).toBeInTheDocument()
    expect(screen.getByText(/mapped/)).toBeInTheDocument()
    expect(screen.getByText(/committed/)).toBeInTheDocument()
    expect(screen.getByText(/v1/)).toBeInTheDocument() // mapping_version_id on the mapped stage
  })

  it('renders the quarantined terminal stage with its error_code', async () => {
    await lookup(QUARANTINE_TRACE_IDS.acmeCanonical)
    expect(await screen.findByText(/quarantined/)).toBeInTheDocument()
    expect(screen.getByText(/CANONICAL_SHAPE_INVALID/)).toBeInTheDocument()
  })

  it('renders the empty state for an unknown trace_id', async () => {
    await lookup(UNKNOWN_TRACE_ID)
    expect(await screen.findByRole('heading', { name: 'Trace not found' })).toBeInTheDocument()
  })
})
