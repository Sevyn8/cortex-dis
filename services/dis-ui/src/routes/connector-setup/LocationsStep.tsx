import type { Dispatch } from 'react'

import { Select } from '@/components/ui/select'
import { LoadingState } from '../../components/states/LoadingState'
import type { ConnectorLocation } from '../../lib/dis-ui-server/connectors-api'
import type { OnboardedStore } from '../../lib/dis-ui-server/stores'
import type { ConnectorWizardAction, ConnectorWizardState } from './state'

// Step 4 (Locations): the provider locations (STUBBED list) are checked and mapped to a DIS
// store. The store options are the tenant's real onboarded stores (reused stores lib). The
// gate (state.canAdvance) requires at least one checked location, each with a store chosen.
export function LocationsStep({
  state,
  dispatch,
  locations,
  stores,
  loading,
}: {
  state: ConnectorWizardState
  dispatch: Dispatch<ConnectorWizardAction>
  locations: ConnectorLocation[]
  stores: OnboardedStore[]
  loading: boolean
}) {
  if (loading) {
    return <LoadingState label="Loading locations..." />
  }
  return (
    <ul className="flex max-w-2xl flex-col gap-2.5">
      {locations.map((loc) => {
        const mapping = state.locations[loc.id]
        const checked = mapping?.checked ?? false
        return (
          <li
            key={loc.id}
            className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-md border border-border px-4 py-3"
          >
            <label className="flex items-center gap-2 text-body">
              <input
                type="checkbox"
                aria-label={`Sync ${loc.name}`}
                checked={checked}
                onChange={(e) =>
                  dispatch({
                    type: 'setLocationChecked',
                    locationId: loc.id,
                    checked: e.target.checked,
                  })
                }
              />
              <span className="font-medium text-foreground">{loc.name}</span>
            </label>
            <span className="text-caption text-muted-foreground">{loc.address}</span>
            <Select
              aria-label={`DIS store for ${loc.name}`}
              disabled={!checked}
              value={mapping?.storeId ?? ''}
              onChange={(e) =>
                dispatch({
                  type: 'setLocationStore',
                  locationId: loc.id,
                  storeId: e.target.value,
                })
              }
              className="ml-auto h-7 w-auto"
            >
              <option value="">Map to store...</option>
              {stores.map((s) => (
                <option key={s.store_id} value={s.store_id}>
                  {s.name}
                </option>
              ))}
            </Select>
          </li>
        )
      })}
    </ul>
  )
}
