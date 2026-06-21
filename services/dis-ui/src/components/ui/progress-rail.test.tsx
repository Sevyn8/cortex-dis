import { render, screen } from '@testing-library/react'

import { ProgressRail } from './progress-rail'

const STEPS = ['Connect/Upload', 'Review mapping', 'Preview', 'Go live']

describe('ProgressRail', () => {
  it('renders every step label', () => {
    render(<ProgressRail steps={STEPS} current={1} />)
    for (const label of STEPS) {
      expect(screen.getByText(label)).toBeInTheDocument()
    }
  })

  it('marks steps before current done, current as current, after upcoming', () => {
    render(<ProgressRail steps={STEPS} current={1} />)
    const items = screen.getAllByRole('listitem')
    expect(items[0]).toHaveAttribute('data-state', 'done')
    expect(items[1]).toHaveAttribute('data-state', 'current')
    expect(items[1]).toHaveAttribute('aria-current', 'step')
    expect(items[2]).toHaveAttribute('data-state', 'upcoming')
    expect(items[3]).toHaveAttribute('data-state', 'upcoming')
  })
})
