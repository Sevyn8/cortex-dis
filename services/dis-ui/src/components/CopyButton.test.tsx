import { render, screen } from '@testing-library/react'

import { CopyButton } from './CopyButton'

describe('CopyButton', () => {
  it('renders an icon button with its accessible label', () => {
    render(<CopyButton value="0190ac0e" label="Copy trace id" />)
    expect(screen.getByRole('button', { name: 'Copy trace id' })).toBeInTheDocument()
  })
})
