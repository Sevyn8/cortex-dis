import { render, screen } from '@testing-library/react'

import { EmptyState } from './EmptyState'
import { ErrorState } from './ErrorState'
import { LoadingState } from './LoadingState'
import { PermissionDenied } from './PermissionDenied'

describe('state primitives', () => {
  it('LoadingState renders its label', () => {
    render(<LoadingState label="Loading sources..." />)
    expect(screen.getByRole('status')).toHaveTextContent('Loading sources...')
  })

  it('EmptyState renders title and message', () => {
    render(<EmptyState title="No sources" message="Nothing here yet." />)
    expect(screen.getByRole('heading', { name: 'No sources' })).toBeInTheDocument()
    expect(screen.getByText('Nothing here yet.')).toBeInTheDocument()
  })

  it('ErrorState renders the alert and a retry control', () => {
    render(<ErrorState message="Boom" onRetry={() => {}} />)
    expect(screen.getByRole('alert')).toHaveTextContent('Boom')
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument()
  })

  it('PermissionDenied renders an access-denied alert', () => {
    render(<PermissionDenied />)
    expect(screen.getByRole('alert')).toHaveTextContent(/access denied/i)
  })
})
