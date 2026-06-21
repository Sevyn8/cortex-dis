import { DisUiServerHttpError } from '../../lib/dis-ui-server/client'

// Human-facing copy for the CSV wizard's two failure points (no silent failures).
//
// The backend's message is the actionable part of a create failure: the semantic gate names the
// exact missing or illegal canonical columns, so it is shown verbatim as the reason; the action
// line is keyed off the error code/status. Non-HTTP errors (network, unknown) degrade to a
// generic reach-the-server message.

export type WizardErrorCopy = { reason: string; action: string }

// A failed POST /mapping-templates (Fix 2). Rendered as an inline alert on the Preview step.
export function describeCreateError(err: unknown): WizardErrorCopy {
  if (err instanceof DisUiServerHttpError) {
    if (err.status === 409 || err.code === 'mapping_template_name_conflict') {
      return {
        reason: err.message,
        action: 'A template for this source already exists. Change the source name and try again.',
      }
    }
    if (err.code === 'invalid_template_type') {
      return { reason: err.message, action: 'Go back and choose a valid template type.' }
    }
    // mapping_config (the semantic gate) and any other 4xx: the message names the columns.
    return {
      reason: err.message,
      action:
        'Go back to the mapping step to adjust your column mappings, or change the template type if this is a different kind of file.',
    }
  }
  return {
    reason: 'We could not reach the server to create your template.',
    action: 'Check your connection and try again.',
  }
}

// A failed CSV analysis (parse, suggestions, or catalog) on the AI-mapping step. Replaces the
// perpetual loading spinner. An HTTP error is the suggestions/catalog endpoint; anything else
// (e.g. a papaparse throw) is a client-side parse failure on an unreadable file.
export function describeAnalyzeError(err: unknown): string {
  if (err instanceof DisUiServerHttpError) {
    return 'We could not analyze this file: the mapping service did not respond as expected. Try again, or go back and re-upload.'
  }
  return 'We could not read this file. Check that it is a valid CSV and re-upload.'
}
