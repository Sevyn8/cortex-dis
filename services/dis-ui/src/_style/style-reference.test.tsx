import { render, screen } from '@testing-library/react'

import { StyleReference } from './StyleReference'

// De-risk gate (redesign R1): the style reference renders the new language. A light smoke
// test, plus a dark-mode render to prove both modes mount (FM2).
describe('StyleReference (R1 de-risk gate)', () => {
  it('renders the four source-type identities', () => {
    render(<StyleReference />)
    expect(screen.getByText('CSV upload')).toBeInTheDocument()
    expect(screen.getByText('Shopify POS')).toBeInTheDocument()
    expect(screen.getByText('Square')).toBeInTheDocument()
    expect(screen.getByText('Other POS/ERP')).toBeInTheDocument()
  })

  it('renders the hero card and the four-step progress rail', () => {
    render(<StyleReference />)
    expect(screen.getByText('Upload a CSV')).toBeInTheDocument()
    for (const step of ['Connect/Upload', 'Review mapping', 'Preview', 'Go live']) {
      expect(screen.getByText(step)).toBeInTheDocument()
    }
  })

  it('mounts under the dark theme class (both modes render)', () => {
    render(
      <div className="dark">
        <StyleReference />
      </div>,
    )
    expect(screen.getByText('CSV upload')).toBeInTheDocument()
    expect(screen.getByText('Upload a CSV')).toBeInTheDocument()
  })
})
