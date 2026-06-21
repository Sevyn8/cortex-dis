import { sql } from '@codemirror/lang-sql'
import { EditorState } from '@codemirror/state'
import { EditorView } from '@codemirror/view'
import { useEffect, useRef } from 'react'
import { basicSetup } from 'codemirror'

// Controlled CodeMirror 6 editor (slice 26). Hand-rolled on the CodeMirror core packages
// (no extra React wrapper dep). The theme is tokenized to the design system via CSS
// custom properties, so it follows light/dark with the `.dark` class rather than using
// CodeMirror's default theme. Contract: `{ value, onChange }` - the same contract a
// styled <textarea> fallback would honor.
const tokenTheme = EditorView.theme({
  '&': {
    backgroundColor: 'var(--surface)',
    color: 'var(--foreground)',
    fontSize: '13px',
    border: '1px solid var(--border)',
    borderRadius: 'var(--radius-md)',
  },
  '.cm-content': {
    fontFamily: 'var(--font-mono)',
    caretColor: 'var(--foreground)',
  },
  '.cm-gutters': {
    backgroundColor: 'var(--surface-raised)',
    color: 'var(--muted-foreground)',
    border: 'none',
  },
  '&.cm-focused': {
    outline: '2px solid var(--ring)',
    outlineOffset: '1px',
  },
  '.cm-activeLine, .cm-activeLineGutter': {
    backgroundColor: 'var(--muted)',
  },
})

export function CodeEditor({
  value,
  onChange,
  ariaLabel = 'SQL editor',
}: {
  value: string
  onChange: (next: string) => void
  ariaLabel?: string
}) {
  const hostRef = useRef<HTMLDivElement | null>(null)
  const viewRef = useRef<EditorView | null>(null)
  // Keep the latest onChange without re-creating the editor on every render.
  const onChangeRef = useRef(onChange)
  useEffect(() => {
    onChangeRef.current = onChange
  })

  useEffect(() => {
    if (hostRef.current === null) {
      return
    }
    const view = new EditorView({
      parent: hostRef.current,
      state: EditorState.create({
        doc: value,
        extensions: [
          basicSetup,
          sql(),
          tokenTheme,
          EditorView.contentAttributes.of({ 'aria-label': ariaLabel }),
          EditorView.updateListener.of((update) => {
            if (update.docChanged) {
              onChangeRef.current(update.state.doc.toString())
            }
          }),
        ],
      }),
    })
    viewRef.current = view
    return () => {
      view.destroy()
      viewRef.current = null
    }
    // Create once; external value changes are synced by the effect below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Sync external value changes (e.g. the sample-query load buttons) into the editor.
  useEffect(() => {
    const view = viewRef.current
    if (view !== null && value !== view.state.doc.toString()) {
      view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: value } })
    }
  }, [value])

  return <div ref={hostRef} />
}
