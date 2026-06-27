import { render, screen } from '@testing-library/react'

import App from './App'

// dev-stub mode (selectable only in a dev build, which vitest is): the
// unauthenticated app lands on the local /dev/login persona picker. In Auth0 mode
// the unauthenticated path redirects to the hosted login instead (covered in the
// AuthProvider/AuthBoundary Auth0 tests).
describe('App', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.stubEnv('VITE_AUTH_MODE', 'dev-stub')
  })

  afterEach(() => {
    vi.unstubAllEnvs()
  })

  it('renders the Dev Login heading when unauthenticated', async () => {
    render(<App />)
    expect(
      await screen.findByRole('heading', { level: 1, name: /dev login/i }),
    ).toBeInTheDocument()
  })
})
