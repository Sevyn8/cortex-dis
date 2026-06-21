import { render, screen } from '@testing-library/react'

import { StatusBadge } from './StatusBadge'

describe('StatusBadge', () => {
  it('renders its children verbatim', () => {
    render(<StatusBadge tone="warning">warning</StatusBadge>)
    expect(screen.getByText('warning')).toBeInTheDocument()
  })

  it('applies the semantic tone token classes', () => {
    render(<StatusBadge tone="success">healthy</StatusBadge>)
    expect(screen.getByText('healthy').className).toContain('text-success')
  })

  it('maps danger to the danger token', () => {
    render(<StatusBadge tone="danger">failing</StatusBadge>)
    expect(screen.getByText('failing').className).toContain('text-danger')
  })
})
