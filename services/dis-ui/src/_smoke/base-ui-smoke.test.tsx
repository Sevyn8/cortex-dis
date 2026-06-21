import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { BaseUiSmoke } from './BaseUiSmoke'

// DE-RISK GATE (slice 23 Checkpoint 1): proves @base-ui/react mounts and behaves under
// Vite 8 + React 19, and that tw-animate-css classes apply to a Base UI element.
describe('Base UI de-risk gate', () => {
  it('mounts a Base UI Button', () => {
    render(<BaseUiSmoke />)
    expect(screen.getByRole('button', { name: 'Smoke button' })).toBeInTheDocument()
  })

  it('opens a Base UI Dialog on trigger click (mount + behavior)', async () => {
    const user = userEvent.setup()
    render(<BaseUiSmoke />)

    expect(screen.queryByText('Smoke dialog')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Open dialog' }))

    const popup = await screen.findByText('Smoke dialog')
    expect(popup).toBeInTheDocument()
    // the popup container carries the tw-animate-css enter class (utility wiring applies)
    await waitFor(() => {
      const dialog = screen.getByRole('dialog')
      expect(dialog.className).toContain('animate-in')
    })
  })
})
