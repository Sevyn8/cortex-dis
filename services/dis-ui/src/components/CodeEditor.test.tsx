import { render, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'

import { CodeEditor } from './CodeEditor'

// De-risk gate (slice 26): CodeMirror 6 must mount, render, and round-trip its value
// under Vite 8 + React 19 + jsdom before any screen is built on it. (Live keystroke
// behavior is a browser concern verified at `pnpm dev`; the jsdom gate is mount + render
// + value-readable + the external-value sync contract.)
function Harness({ initial }: { initial: string }) {
  const [value, setValue] = useState(initial)
  return (
    <div>
      <CodeEditor value={value} onChange={setValue} />
      <button type="button" onClick={() => setValue('select 2')}>
        set
      </button>
    </div>
  )
}

describe('CodeEditor de-risk gate', () => {
  it('mounts a CodeMirror editor and renders its initial value', () => {
    const { container } = render(<CodeEditor value="select 1" onChange={() => {}} />)
    expect(container.querySelector('.cm-editor')).not.toBeNull()
    expect(container.querySelector('.cm-content')?.textContent).toContain('select 1')
  })

  it('reflects an external value change into the editor', async () => {
    const user = userEvent.setup()
    const { container, getByRole } = render(<Harness initial="select 1" />)
    expect(container.querySelector('.cm-content')?.textContent).toContain('select 1')
    await user.click(getByRole('button', { name: 'set' }))
    // the sync effect dispatches the new doc into the live editor
    await waitFor(() =>
      expect(container.querySelector('.cm-content')?.textContent).toContain('select 2'),
    )
  })
})
