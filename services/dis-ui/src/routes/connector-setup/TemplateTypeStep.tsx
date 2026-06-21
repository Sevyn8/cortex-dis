import { Check } from 'lucide-react'
import type { Dispatch } from 'react'

import { Card, CardContent } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import { LoadingState } from '../../components/states/LoadingState'
import type { TemplateType } from '../../lib/dis-ui-server/template-types'
import type { ConnectorWizardAction, ConnectorWizardState } from './state'

// CSV branch step 3 (Template type), WIRED. The packet axis MUST be chosen before mapping (the
// field catalog requires ?template_type=). Options come from GET /api/v1/template-types
// (display_name + description). Picking a type sets state.templateType, which then drives the
// type-aware GET /template-mapping-fields on the next step. Selected = a 2px info border + check.
export function TemplateTypeStep({
  state,
  dispatch,
  templateTypes,
  loading,
}: {
  state: ConnectorWizardState
  dispatch: Dispatch<ConnectorWizardAction>
  templateTypes: TemplateType[]
  loading: boolean
}) {
  if (loading) {
    return <LoadingState label="Loading template types..." />
  }
  return (
    <div
      role="radiogroup"
      aria-label="Template type"
      className="grid grid-cols-[repeat(auto-fit,minmax(240px,1fr))] gap-4"
    >
      {templateTypes.map((t) => {
        const selected = state.templateType === t.key
        return (
          <Card
            key={t.key}
            role="radio"
            aria-checked={selected}
            tabIndex={0}
            aria-label={t.display_name}
            onClick={() => dispatch({ type: 'setTemplateType', value: t.key })}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                dispatch({ type: 'setTemplateType', value: t.key })
              }
            }}
            className={cn(
              'cursor-pointer p-5 transition-colors',
              selected ? 'border-2 border-info' : 'hover:border-border-strong',
            )}
          >
            <CardContent className="flex flex-col gap-2 p-0">
              <div className="flex items-center justify-between">
                <span className="text-[15px] font-medium text-foreground">{t.display_name}</span>
                {selected ? <Check aria-hidden="true" className="size-5 text-info" /> : null}
              </div>
              <span className="text-caption leading-relaxed text-muted-foreground">
                {t.description}
              </span>
            </CardContent>
          </Card>
        )
      })}
    </div>
  )
}
