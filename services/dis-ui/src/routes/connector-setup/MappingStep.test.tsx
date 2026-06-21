import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { MappingStep } from './MappingStep'
import { initialConnectorWizardState } from './state'

// MappingStep gates content on three states. These pin the two added by the "no silent failures"
// fix: the honest CSV loading label, and the error state that replaces the perpetual spinner.

describe('MappingStep states', () => {
  it('CSV loading names the real work (parse + suggestions)', () => {
    render(
      <MappingStep
        state={initialConnectorWizardState}
        dispatch={vi.fn()}
        mapping={null}
        catalog={[]}
        loading={true}
        formatDeclarations={true}
      />,
    )
    expect(
      screen.getByText('Reading your file and suggesting field mappings...'),
    ).toBeInTheDocument()
  })

  it('an analyze error shows an alert with a retry, not a perpetual spinner', async () => {
    const user = userEvent.setup()
    const onRetry = vi.fn()
    render(
      <MappingStep
        state={initialConnectorWizardState}
        dispatch={vi.fn()}
        mapping={null}
        catalog={[]}
        loading={true}
        formatDeclarations={true}
        error="We could not read this file. Check that it is a valid CSV and re-upload."
        onRetry={onRetry}
      />,
    )
    // error takes precedence over loading: no spinner label, an alert is shown instead
    expect(
      screen.queryByText('Reading your file and suggesting field mappings...'),
    ).not.toBeInTheDocument()
    expect(await screen.findByRole('alert')).toHaveTextContent(/valid CSV/i)
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })
})
