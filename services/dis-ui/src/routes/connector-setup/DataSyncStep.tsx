import type { Dispatch } from 'react'

import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { SYNC_CADENCES } from '../../lib/dis-ui-server/connectors-api'
import type { ConnectorDataType, SyncCadence } from '../../lib/dis-ui-server/connectors-api'
import type { ConnectorWizardAction, ConnectorWizardState } from './state'

// Step 5 (Data & sync): which data types to sync (orders / products / inventory) and how
// often. The gate requires at least one data type selected (cadence has a default).
const DATA_TYPES: { value: ConnectorDataType; label: string }[] = [
  { value: 'orders', label: 'Orders' },
  { value: 'products', label: 'Products' },
  { value: 'inventory', label: 'Inventory' },
]

export function DataSyncStep({
  state,
  dispatch,
}: {
  state: ConnectorWizardState
  dispatch: Dispatch<ConnectorWizardAction>
}) {
  return (
    <div className="flex max-w-md flex-col gap-6">
      <div className="flex flex-col gap-2.5">
        <span className="text-caption text-muted-foreground">Data to sync</span>
        <div className="flex flex-col gap-2.5">
          {DATA_TYPES.map((dt) => (
            <label key={dt.value} className="flex items-center gap-2 text-body">
              <input
                type="checkbox"
                aria-label={dt.label}
                checked={state.dataTypes.includes(dt.value)}
                onChange={() => dispatch({ type: 'toggleDataType', dataType: dt.value })}
              />
              {dt.label}
            </label>
          ))}
        </div>
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="connector-cadence">Sync cadence</Label>
        <Select
          id="connector-cadence"
          value={state.cadence}
          onChange={(e) => dispatch({ type: 'setCadence', cadence: e.target.value as SyncCadence })}
        >
          {SYNC_CADENCES.map((c) => (
            <option key={c.value} value={c.value}>
              {c.label}
            </option>
          ))}
        </Select>
      </div>
    </div>
  )
}
