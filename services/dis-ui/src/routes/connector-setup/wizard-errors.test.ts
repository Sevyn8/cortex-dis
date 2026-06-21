import { describe, expect, it } from 'vitest'

import { DisUiServerHttpError } from '../../lib/dis-ui-server/client'
import { describeAnalyzeError, describeCreateError } from './wizard-errors'

const GATE_MESSAGE =
  "mapping_rules leave mandatory StoreSkuCurrentPosition column(s) ['currency', 'product_category', 'unit_cost'] unprovided; each must come from a rename or a derive"

describe('describeCreateError', () => {
  it('keeps the semantic-gate sentence verbatim and points back to mapping / type', () => {
    const copy = describeCreateError(
      new DisUiServerHttpError(400, 'mapping_config', GATE_MESSAGE, {}),
    )
    // the backend names the exact missing columns; that is the actionable reason, shown as-is
    expect(copy.reason).toBe(GATE_MESSAGE)
    expect(copy.action).toMatch(/mapping step/i)
    expect(copy.action).toMatch(/template type/i)
  })

  it('maps a name conflict (409) to a rename action', () => {
    const copy = describeCreateError(
      new DisUiServerHttpError(
        409,
        'mapping_template_name_conflict',
        'a template named ... already exists',
        {},
      ),
    )
    expect(copy.action).toMatch(/already exists/i)
    expect(copy.action).toMatch(/source name/i)
  })

  it('maps an invalid template type to a pick-a-valid-type action', () => {
    const copy = describeCreateError(
      new DisUiServerHttpError(400, 'invalid_template_type', 'unknown template_type', {}),
    )
    expect(copy.action).toMatch(/valid template type/i)
  })

  it('degrades a non-HTTP error to a generic reach-the-server message', () => {
    const copy = describeCreateError(new Error('network down'))
    expect(copy.reason).toMatch(/reach the server/i)
    expect(copy.action).toMatch(/try again/i)
  })
})

describe('describeAnalyzeError', () => {
  it('an HTTP failure names the mapping service', () => {
    expect(describeAnalyzeError(new DisUiServerHttpError(500, '', 'boom', {}))).toMatch(
      /mapping service/i,
    )
  })

  it('a non-HTTP failure (parse) points at re-uploading a valid CSV', () => {
    expect(describeAnalyzeError(new Error('papaparse exploded'))).toMatch(/valid CSV/i)
  })
})
