import type { Dispatch } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { cn } from '@/lib/utils'
import { connectorSpec } from '../../lib/dis-ui-server/connectors-catalog'
import type {
  ConnectorAuthMethod,
  ConnectorField,
} from '../../lib/dis-ui-server/connectors-catalog'
import type { ConnectorWizardAction, ConnectorWizardState } from './state'

// Step 2 (Connect): source name + auth-method toggle (Sign in = recommended / Use an API
// token). For the OAuth path we show "what you will need", the single pre-auth field, an
// authorize button, and a note that Sevyn8 app credentials are managed server-side. The
// token path shows the per-connector token fields + a submit button. BOTH the authorize and
// token-submit actions are STUBBED (they call the connectors-api stub and advance; no real
// call) - the route owns that via onAuthorize.
export function ConnectStep({
  state,
  dispatch,
  onAuthorize,
  authorizing,
}: {
  state: ConnectorWizardState
  dispatch: Dispatch<ConnectorWizardAction>
  onAuthorize: () => void
  authorizing: boolean
}) {
  const spec = state.connector === null ? null : connectorSpec(state.connector)
  if (spec === null) {
    return null
  }

  const methods: { value: ConnectorAuthMethod; label: string }[] = [
    { value: 'oauth', label: `${spec.oauthLabel} (recommended)` },
    { value: 'api_token', label: 'Use an API token' },
  ]

  function fieldValue(name: string): string {
    return (state.authMethod === 'oauth' ? state.preAuth[name] : state.tokenFields[name]) ?? ''
  }

  function setField(name: string, value: string): void {
    dispatch(
      state.authMethod === 'oauth'
        ? { type: 'setPreAuthField', name, value }
        : { type: 'setTokenField', name, value },
    )
  }

  function renderField(field: ConnectorField) {
    const id = `connector-${field.name}`
    return (
      <div key={field.name} className="flex flex-col gap-1">
        <Label htmlFor={id}>{field.label}</Label>
        {field.kind === 'select' ? (
          <Select
            id={id}
            value={fieldValue(field.name)}
            onChange={(e) => setField(field.name, e.target.value)}
          >
            <option value="">Select {field.label.toLowerCase()}...</option>
            {(field.options ?? []).map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </Select>
        ) : (
          <Input
            id={id}
            type={field.kind === 'secret' ? 'password' : 'text'}
            placeholder={field.placeholder}
            value={fieldValue(field.name)}
            onChange={(e) => setField(field.name, e.target.value)}
          />
        )}
      </div>
    )
  }

  // Required-field completeness for the active path (gates the authorize / submit button).
  const requiredFields = state.authMethod === 'oauth' ? [spec.preAuthField] : spec.tokenFields
  const fieldsComplete = requiredFields.every((f) => fieldValue(f.name).trim().length > 0)
  const canAuthorize = state.sourceName.trim().length > 0 && fieldsComplete && !authorizing

  return (
    <div className="flex max-w-xl flex-col gap-6">
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="connector-source-name">Source name</Label>
        <Input
          id="connector-source-name"
          placeholder={`${spec.label} sales`}
          value={state.sourceName}
          onChange={(e) => dispatch({ type: 'setSourceName', value: e.target.value })}
        />
      </div>

      {/* Auth-method toggle. */}
      <div className="flex flex-col gap-1.5">
        <span className="text-caption text-muted-foreground">How do you want to connect?</span>
        <div role="radiogroup" aria-label="Authentication method" className="flex flex-wrap gap-2">
          {methods.map((m) => {
            const selected = state.authMethod === m.value
            return (
              <button
                key={m.value}
                type="button"
                role="radio"
                aria-checked={selected}
                onClick={() => dispatch({ type: 'setAuthMethod', method: m.value })}
                className={cn(
                  'rounded-md border px-3 py-2 text-body transition-colors',
                  selected
                    ? 'border-info bg-info/10 text-foreground'
                    : 'border-border text-muted-foreground hover:bg-muted',
                )}
              >
                {m.label}
              </button>
            )
          })}
        </div>
      </div>

      {/* What you will need. */}
      <div className="flex flex-col gap-1.5">
        <span className="text-caption text-muted-foreground">What you will need</span>
        <ul className="flex list-disc flex-col gap-1 pl-5 text-body text-muted-foreground">
          {spec.whatYouWillNeed.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </div>

      {/* The credential fields for the active path. */}
      <div className="flex flex-col gap-4">
        {state.authMethod === 'oauth'
          ? renderField(spec.preAuthField)
          : spec.tokenFields.map((f) => renderField(f))}
      </div>

      <div className="flex flex-col gap-2">
        <Button type="button" disabled={!canAuthorize} onClick={() => onAuthorize()}>
          {state.authMethod === 'oauth' ? spec.oauthLabel : 'Connect with token'}
        </Button>
        {state.authMethod === 'oauth' ? (
          <p className="text-caption text-muted-foreground">
            The Sevyn8 app credentials are managed server-side; you never enter them here.
          </p>
        ) : (
          <p className="text-caption text-muted-foreground">
            Your token is stored server-side and used read-only; it is never kept in the browser.
          </p>
        )}
      </div>
    </div>
  )
}
