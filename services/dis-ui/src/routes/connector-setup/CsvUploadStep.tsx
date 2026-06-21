import type { Dispatch } from 'react'

import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { FileDropzone } from '../../components/FileDropzone'
import type { ConnectorWizardAction, ConnectorWizardState } from './state'

// CSV branch step 2 (Upload): a source name + a sample file (reusing the shared FileDropzone).
// REAL: the selected File is lifted to the route (onSelectFile) which parses it (papaparse) and
// calls the type-aware /mapping-suggestions endpoint at the mapping step. The reducer still
// records the file NAME (display + the source-name + file gate on Next). Nothing is ingested -
// only the columns are read for mapping.
export function CsvUploadStep({
  state,
  dispatch,
  file,
  onSelectFile,
}: {
  state: ConnectorWizardState
  dispatch: Dispatch<ConnectorWizardAction>
  // The real selected File, held by the route (kept out of the pure reducer; non-serializable).
  // Used directly for the dropzone card so it shows the true name and size (not a 0-byte stub).
  file: File | null
  onSelectFile: (file: File | null) => void
}) {
  function onSelect(next: File | null): void {
    onSelectFile(next)
    if (next === null) {
      dispatch({ type: 'setCsvFile', fileName: '' })
      return
    }
    // Record the file name (display + Next gate). The real parse + suggestions run at the
    // mapping step, once the chosen template_type is known.
    dispatch({ type: 'setCsvFile', fileName: next.name })
    dispatch({ type: 'setCsvAnalysisReady' })
  }

  return (
    <div className="flex max-w-xl flex-col gap-6">
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="csv-source-name">Source name</Label>
        <Input
          id="csv-source-name"
          placeholder="Weekly sales export"
          value={state.sourceName}
          onChange={(e) => dispatch({ type: 'setSourceName', value: e.target.value })}
        />
      </div>

      <FileDropzone
        id="csv-sample"
        label="Sample file"
        accept=".csv,text/csv"
        hint="We read the columns from a sample. Nothing is ingested yet."
        file={file}
        onSelect={onSelect}
      />
    </div>
  )
}
