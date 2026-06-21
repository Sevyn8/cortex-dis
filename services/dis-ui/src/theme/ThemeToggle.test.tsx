import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { ThemeProvider } from './ThemeProvider'
import { ThemeToggle } from './ThemeToggle'

// The toggle is the mechanism that flips the token set: next-themes adds/removes the
// `.dark` class on <html>, which re-points the oklch tokens. jsdom cannot compute the
// resulting colors, so we assert the class flip (the cause), not the pixels.
describe('ThemeToggle', () => {
  afterEach(() => {
    document.documentElement.classList.remove('dark', 'light')
  })

  it('toggles the dark class on the document element', async () => {
    const user = userEvent.setup()
    render(
      <ThemeProvider>
        <ThemeToggle />
      </ThemeProvider>,
    )

    const button = screen.getByRole('button', { name: 'Toggle theme' })

    await user.click(button)
    await waitFor(() => expect(document.documentElement.classList.contains('dark')).toBe(true))

    await user.click(button)
    await waitFor(() => expect(document.documentElement.classList.contains('dark')).toBe(false))
  })
})
