import { Check, FileSpreadsheet } from 'lucide-react'
import type { Dispatch, KeyboardEvent } from 'react'
import type { LucideIcon } from 'lucide-react'

import { Card, CardContent } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import { CONNECTOR_ORDER, CONNECTOR_SPECS } from '../../lib/dis-ui-server/connectors-catalog'
import type { ConnectorWizardAction, ConnectorWizardState } from './state'

// Step 1 (Source), unified (Chunk 2): two labeled groups. "Connect a system - live sync" holds
// the POS connector tiles (Shopify / Square / Clover -> the live-sync branch); "Upload files -
// reusable template" holds the CSV / SFTP tile (-> the CSV branch). Picking a tile sets the
// branch and resets per-branch state (reducer). The per-step heading + description are rendered
// by the route. Selected = a 2px info border + a check indicator.

type TileProps = {
  label: string
  description: string
  icon: LucideIcon
  iconClass: string
  iconBgClass: string
  selected: boolean
  onSelect: () => void
}

function Tile({
  label,
  description,
  icon: Icon,
  iconClass,
  iconBgClass,
  selected,
  onSelect,
}: TileProps) {
  function onKeyDown(e: KeyboardEvent<HTMLDivElement>): void {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      onSelect()
    }
  }
  return (
    <Card
      role="radio"
      aria-checked={selected}
      tabIndex={0}
      aria-label={label}
      onClick={onSelect}
      onKeyDown={onKeyDown}
      className={cn(
        'cursor-pointer p-5 transition-colors',
        selected ? 'border-2 border-info' : 'hover:border-border-strong',
      )}
    >
      <CardContent className="flex flex-col gap-3 p-0">
        <div className="flex items-start justify-between">
          <span
            aria-hidden="true"
            className={cn(
              'flex size-12 items-center justify-center rounded-xl',
              iconBgClass,
              iconClass,
            )}
          >
            <Icon className="size-6" />
          </span>
          {selected ? <Check aria-hidden="true" className="size-5 text-info" /> : null}
        </div>
        <div className="flex flex-col gap-1">
          <span className="text-[15px] font-medium text-foreground">{label}</span>
          <span className="text-caption leading-relaxed text-muted-foreground">{description}</span>
        </div>
      </CardContent>
    </Card>
  )
}

export function SourceStep({
  state,
  dispatch,
}: {
  state: ConnectorWizardState
  dispatch: Dispatch<ConnectorWizardAction>
}) {
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <h3 className="text-caption font-medium text-muted-foreground">
          Connect a system - live sync
        </h3>
        <div
          role="radiogroup"
          aria-label="Live sync connector"
          className="grid grid-cols-[repeat(auto-fit,minmax(240px,1fr))] gap-4"
        >
          {CONNECTOR_ORDER.map((key) => {
            const spec = CONNECTOR_SPECS[key]
            return (
              <Tile
                key={key}
                label={spec.label}
                description={spec.description}
                icon={spec.icon}
                iconClass={spec.iconClass}
                iconBgClass={spec.iconBgClass}
                selected={state.branch === 'pos' && state.connector === key}
                onSelect={() => dispatch({ type: 'selectConnector', connector: key })}
              />
            )
          })}
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <h3 className="text-caption font-medium text-muted-foreground">
          Upload files - reusable template
        </h3>
        <div
          role="radiogroup"
          aria-label="File upload source"
          className="grid grid-cols-[repeat(auto-fit,minmax(240px,1fr))] gap-4"
        >
          <Tile
            label="CSV / SFTP"
            description="Upload a sales export or point at an SFTP drop. We map it to a reusable template."
            icon={FileSpreadsheet}
            iconClass="text-foreground"
            iconBgClass="bg-muted"
            selected={state.branch === 'csv'}
            onSelect={() => dispatch({ type: 'selectCsv' })}
          />
        </div>
      </div>
    </div>
  )
}
