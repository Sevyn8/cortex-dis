import type { ConnectorMappingField } from '../../lib/dis-ui-server/connectors-api'
import { localePreset } from '../../lib/dis-ui-server/connectors-api'
import type { FieldDatatype } from '../../lib/dis-ui-server/mapping-fields'
import { CONNECTOR_STEP_COUNT, CONNECTOR_STEP_INDEX } from './steps'
import {
  activeMappingFields,
  assembleConnectorColumns,
  canAdvance,
  connectorWizardReducer,
  currentStepKey,
  initialConnectorWizardState,
  isIgnored,
  mappingTargetFor,
  slugifySourceId,
} from './state'
import type { ConnectorWizardState } from './state'

function field(sourceField: string, suggestedTarget: string | null): ConnectorMappingField {
  return {
    sourceField,
    suggestedTarget,
    alternatives: [],
    confidence: 0.9,
    reasoning: null,
    detectedFormat: null,
    sampleValues: [],
  }
}

// Pure stepper-logic + ignore-toggle tests for the Live Sync connector wizard. No rendering;
// just the reducer and its gating helpers.

const MAPPING_FIELDS: ConnectorMappingField[] = [
  {
    sourceField: 'order_id',
    suggestedTarget: 'transaction_id',
    alternatives: [],
    confidence: 0.9,
    reasoning: 'ref',
    detectedFormat: null,
    sampleValues: ['1'],
  },
  {
    sourceField: 'gateway',
    suggestedTarget: null,
    alternatives: ['payment_method'],
    confidence: 0.3,
    reasoning: null,
    detectedFormat: null,
    sampleValues: ['cash'],
  },
]

function reduce(
  state: ConnectorWizardState,
  ...actions: Parameters<typeof connectorWizardReducer>[1][]
) {
  return actions.reduce((s, a) => connectorWizardReducer(s, a), state)
}

describe('connectorWizardReducer - stepper', () => {
  it('starts at the Source step and cannot advance without a connector', () => {
    expect(initialConnectorWizardState.stepIndex).toBe(CONNECTOR_STEP_INDEX.source)
    expect(canAdvance(initialConnectorWizardState)).toBe(false)
  })

  it('selecting a connector unblocks the Source step', () => {
    const next = connectorWizardReducer(initialConnectorWizardState, {
      type: 'selectConnector',
      connector: 'shopify',
    })
    expect(next.connector).toBe('shopify')
    expect(canAdvance(next)).toBe(true)
  })

  it('next advances and back retreats, clamped to the step bounds', () => {
    const atOne = connectorWizardReducer(initialConnectorWizardState, { type: 'next' })
    expect(atOne.stepIndex).toBe(1)
    // back from step 0 stays at 0
    expect(connectorWizardReducer(initialConnectorWizardState, { type: 'back' }).stepIndex).toBe(0)
    // next never exceeds the last step
    let s = initialConnectorWizardState
    for (let i = 0; i < CONNECTOR_STEP_COUNT + 5; i += 1) {
      s = connectorWizardReducer(s, { type: 'next' })
    }
    expect(s.stepIndex).toBe(CONNECTOR_STEP_COUNT - 1)
  })

  it('switching connector resets credential + account state', () => {
    const s = reduce(
      initialConnectorWizardState,
      { type: 'selectConnector', connector: 'shopify' },
      { type: 'setPreAuthField', name: 'shop_domain', value: 'x.myshopify.com' },
      {
        type: 'setAccount',
        account: { businessName: 'b', accountId: 'a', readOnly: true, tokenStored: true },
      },
      { type: 'selectConnector', connector: 'square' },
    )
    expect(s.connector).toBe('square')
    expect(s.preAuth).toEqual({})
    expect(s.account).toBeNull()
  })
})

describe('connectorWizardReducer - step gating', () => {
  it('Connect step requires a source name and an established account', () => {
    let s: ConnectorWizardState = {
      ...initialConnectorWizardState,
      stepIndex: CONNECTOR_STEP_INDEX.connect,
      connector: 'shopify',
    }
    expect(canAdvance(s)).toBe(false)
    s = connectorWizardReducer(s, { type: 'setSourceName', value: 'Shop sales' })
    expect(canAdvance(s)).toBe(false)
    s = connectorWizardReducer(s, {
      type: 'setAccount',
      account: { businessName: 'b', accountId: 'a', readOnly: true, tokenStored: true },
    })
    expect(canAdvance(s)).toBe(true)
  })

  it('Locations step requires at least one checked location, each with a store', () => {
    let s: ConnectorWizardState = {
      ...initialConnectorWizardState,
      stepIndex: CONNECTOR_STEP_INDEX.locations,
    }
    expect(canAdvance(s)).toBe(false)
    s = connectorWizardReducer(s, {
      type: 'setLocationChecked',
      locationId: 'loc_001',
      checked: true,
    })
    expect(canAdvance(s)).toBe(false) // checked but no store
    s = connectorWizardReducer(s, {
      type: 'setLocationStore',
      locationId: 'loc_001',
      storeId: 'store-1',
    })
    expect(canAdvance(s)).toBe(true)
  })

  it('Data & sync step requires at least one data type', () => {
    let s: ConnectorWizardState = {
      ...initialConnectorWizardState,
      stepIndex: CONNECTOR_STEP_INDEX.dataSync,
    }
    expect(canAdvance(s)).toBe(true) // 'orders' is selected by default
    s = connectorWizardReducer(s, { type: 'toggleDataType', dataType: 'orders' })
    expect(s.dataTypes).toEqual([])
    expect(canAdvance(s)).toBe(false)
  })

  it('Preview step is non-blocking and the Live step is terminal', () => {
    expect(
      canAdvance({ ...initialConnectorWizardState, stepIndex: CONNECTOR_STEP_INDEX.preview }),
    ).toBe(true)
    expect(
      canAdvance({ ...initialConnectorWizardState, stepIndex: CONNECTOR_STEP_INDEX.live }),
    ).toBe(false)
  })
})

describe('connectorWizardReducer - mapping + ignore', () => {
  const base: ConnectorWizardState = {
    ...initialConnectorWizardState,
    stepIndex: CONNECTOR_STEP_INDEX.mapping,
    connector: 'shopify',
  }

  it('mappingTargetFor uses the override, else the suggestion, else empty', () => {
    expect(mappingTargetFor(base, MAPPING_FIELDS[0])).toBe('transaction_id') // suggestion
    expect(mappingTargetFor(base, MAPPING_FIELDS[1])).toBe('') // no suggestion -> unmapped
    const overridden = connectorWizardReducer(base, {
      type: 'setMappingTarget',
      sourceField: 'order_id',
      target: 'line_item_seq',
    })
    expect(mappingTargetFor(overridden, MAPPING_FIELDS[0])).toBe('line_item_seq')
  })

  it('cannot advance while a non-ignored field is unmapped', () => {
    // gateway has no target -> blocked
    expect(canAdvance(base, MAPPING_FIELDS)).toBe(false)
  })

  it('ignoring the unmapped field unblocks advancing and excludes it from active fields', () => {
    const s = connectorWizardReducer(base, { type: 'toggleIgnore', sourceField: 'gateway' })
    expect(isIgnored(s, 'gateway')).toBe(true)
    expect(activeMappingFields(s, MAPPING_FIELDS).map((f) => f.sourceField)).toEqual(['order_id'])
    expect(canAdvance(s, MAPPING_FIELDS)).toBe(true)
  })

  it('toggleIgnore flips the flag back off', () => {
    const on = connectorWizardReducer(base, { type: 'toggleIgnore', sourceField: 'gateway' })
    const off = connectorWizardReducer(on, { type: 'toggleIgnore', sourceField: 'gateway' })
    expect(isIgnored(off, 'gateway')).toBe(false)
  })
})

describe('connectorWizardReducer - CSV branch (Chunk 2)', () => {
  const csv = connectorWizardReducer(initialConnectorWizardState, { type: 'selectCsv' })

  it('selectCsv sets the csv branch (no connector) and unblocks the Source step', () => {
    expect(csv.branch).toBe('csv')
    expect(csv.connector).toBeNull()
    expect(canAdvance(csv)).toBe(true)
  })

  it('runs the CSV flow keys in order (source -> upload -> templateType -> mapping -> preview -> created)', () => {
    expect(currentStepKey(csv)).toBe('source')
    expect(currentStepKey({ ...csv, stepIndex: 1 })).toBe('upload')
    expect(currentStepKey({ ...csv, stepIndex: 2 })).toBe('templateType')
    expect(currentStepKey({ ...csv, stepIndex: 3 })).toBe('mapping')
    expect(currentStepKey({ ...csv, stepIndex: 4 })).toBe('preview')
    expect(currentStepKey({ ...csv, stepIndex: 5 })).toBe('created')
  })

  it('Upload step requires a read sample AND a source name', () => {
    let s: ConnectorWizardState = { ...csv, stepIndex: 1 }
    expect(canAdvance(s)).toBe(false)
    s = connectorWizardReducer(s, { type: 'setCsvFile', fileName: 'sales.csv' })
    expect(canAdvance(s)).toBe(false) // file recorded, analysis not ready, no name
    s = connectorWizardReducer(s, { type: 'setCsvAnalysisReady' })
    expect(canAdvance(s)).toBe(false) // analysis ready, still no name
    s = connectorWizardReducer(s, { type: 'setSourceName', value: 'Export' })
    expect(canAdvance(s)).toBe(true)
  })

  it('setCsvFile resets the analysis-ready flag (a new file must be re-read)', () => {
    const ready = connectorWizardReducer(csv, { type: 'setCsvAnalysisReady' })
    expect(ready.csvAnalysisReady).toBe(true)
    const replaced = connectorWizardReducer(ready, { type: 'setCsvFile', fileName: 'other.csv' })
    expect(replaced.csvAnalysisReady).toBe(false)
  })

  it('Template type step requires a chosen type; Created step is terminal', () => {
    let s: ConnectorWizardState = { ...csv, stepIndex: 2 }
    expect(canAdvance(s)).toBe(false)
    s = connectorWizardReducer(s, { type: 'setTemplateType', value: 'sales' })
    expect(canAdvance(s)).toBe(true)
    expect(canAdvance({ ...csv, stepIndex: 5 })).toBe(false)
  })

  it('clamps next to the CSV flow length (6 steps)', () => {
    let s = csv
    for (let i = 0; i < 20; i += 1) {
      s = connectorWizardReducer(s, { type: 'next' })
    }
    expect(s.stepIndex).toBe(5)
  })

  it('switching from POS to CSV resets per-branch state (templateType cleared)', () => {
    const s = reduce(
      initialConnectorWizardState,
      { type: 'selectConnector', connector: 'shopify' },
      { type: 'setSourceName', value: 'Shop' },
      { type: 'setTemplateType', value: 'sales' },
      { type: 'selectCsv' },
    )
    expect(s.branch).toBe('csv')
    expect(s.templateType).toBe('')
  })
})

describe('CSV source-format declarations + create assembly (D89/D90)', () => {
  const base: ConnectorWizardState = {
    ...initialConnectorWizardState,
    branch: 'csv',
    templateType: 'sales',
  }

  it('setCsvLocale / setCsvDatetimeFormat / setCsvPercentage update the declaration state', () => {
    let s = connectorWizardReducer(base, { type: 'setCsvLocale', locale: 'eu' })
    expect(s.csvLocale).toBe('eu')
    s = connectorWizardReducer(s, {
      type: 'setCsvDatetimeFormat',
      sourceField: 'sold_at',
      format: 'DD-MM-YYYY',
    })
    s = connectorWizardReducer(s, {
      type: 'setCsvPercentage',
      sourceField: 'discount',
      isPercentage: true,
    })
    expect(s.csvColumnFormat.sold_at.datetimeFormat).toBe('DD-MM-YYYY')
    expect(s.csvColumnFormat.discount.isPercentage).toBe(true)
  })

  it('slugifySourceId makes a 16a-valid source_id and degrades empty/odd names', () => {
    expect(slugifySourceId('Weekly Export!')).toBe('weekly_export')
    expect(slugifySourceId('  ***  ')).toBe('csv_source')
    expect(slugifySourceId('already_ok_1')).toBe('already_ok_1')
  })

  it('assembleConnectorColumns builds the 16a columns[] from wizard state', () => {
    const fields = [
      field('item_code', 'sku_id'), // text -> no format declaration
      field('amount', 'unit_sale_price'), // number -> locale separators (+ percentage)
      field('sold_at', 'source_sale_timestamp'), // datetime -> src_datetime_format
      field('junk', null), // ignored -> __ignore__
    ]
    const datatypeByKey = new Map<string, FieldDatatype | null>([
      ['sku_id', 'text'],
      ['unit_sale_price', 'number'],
      ['source_sale_timestamp', 'datetime'],
    ])
    let s: ConnectorWizardState = {
      ...base,
      mappingOverrides: {
        item_code: 'sku_id',
        amount: 'unit_sale_price',
        sold_at: 'source_sale_timestamp',
      },
    }
    s = connectorWizardReducer(s, { type: 'toggleIgnore', sourceField: 'junk' })
    s = connectorWizardReducer(s, {
      type: 'setCsvDatetimeFormat',
      sourceField: 'sold_at',
      format: 'DD-MM-YYYY',
    })
    s = connectorWizardReducer(s, {
      type: 'setCsvPercentage',
      sourceField: 'amount',
      isPercentage: true,
    })

    const columns = assembleConnectorColumns(s, fields, datatypeByKey, localePreset('eu'))

    expect(columns[0]).toEqual({ src_key: 'item_code', dest_key: 'sku_id' })
    expect(columns[1]).toEqual({
      src_key: 'amount',
      dest_key: 'unit_sale_price',
      src_decimal_separator: ',',
      src_thousand_separator: '.', // EU dot-thousands (422s until 16b; assembled anyway)
      src_is_percentage: true,
    })
    expect(columns[2]).toEqual({
      src_key: 'sold_at',
      dest_key: 'source_sale_timestamp',
      src_datetime_format: 'DD-MM-YYYY',
    })
    // ignored column -> __ignore__, no format declarations
    expect(columns[3]).toEqual({ src_key: 'junk', dest_key: '__ignore__' })
  })
})
