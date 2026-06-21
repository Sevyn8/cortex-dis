import { render, screen } from '@testing-library/react'

import App from './App'

describe('App', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('renders the Dev Login heading when unauthenticated', async () => {
    render(<App />)
    expect(
      await screen.findByRole('heading', { level: 1, name: /dev login/i }),
    ).toBeInTheDocument()
  })
})
