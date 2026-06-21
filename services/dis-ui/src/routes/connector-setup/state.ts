import type { ConnectorAuthMethod, ConnectorKey } from '../../lib/dis-ui-server/connectors-catalog'
import type {
  ConnectorAccount,
  ConnectorColumn,
  ConnectorDataType,
  ConnectorMappingField,
  CreatedTemplate,
  LiveConnectorSource,
  LocaleKey,
  LocalePreset,
  SyncCadence,
} from '../../lib/dis-ui-server/connectors-api'
import type { FieldDatatype } from '../../lib/dis-ui-server/mapping-fields'
import type { Branch, StepKey } from './steps'
import { flowFor } from './steps'

// Pure state machine for the unified Add Source wizard (Chunk 1 POS + Chunk 2 CSV/SFTP). It is
// BRANCH-AWARE: `branch` selects which ordered step flow is active, and `stepIndex` indexes into
// that flow. All step-advance gating, the per-field IGNORE flag, and the mapping-target override
// live here so the logic is unit testable without rendering. The route owns only side effects
// (the wired GETs + the stubbed calls) and dispatches actions into this reducer.

export type LocationMapping = { checked: boolean; storeId: string }

// CSV branch: the operator's per-column source-format declaration (16a). datetimeFormat for
// datetime/date targets; isPercentage for number targets. Decimal/thousand separators are
// template-level (csvLocale), not per column.
export type CsvColumnFormat = { datetimeFormat?: string; isPercentage?: boolean }

export type ConnectorWizardState = {
  // null until a Source tile is picked; 'pos' for a live-sync connector, 'csv' for CSV/SFTP.
  branch: Branch | null
  stepIndex: number
  connector: ConnectorKey | null
  sourceName: string
  authMethod: ConnectorAuthMethod
  // Pre-auth field values (OAuth path: shop domain / region), by field name.
  preAuth: Record<string, string>
  // API-token field values (token path), by field name.
  tokenFields: Record<string, string>
  // Set once the (stubbed) authorize / token submit succeeds (POS branch).
  account: ConnectorAccount | null
  // locationId -> { checked, chosen DIS store_id } (POS branch).
  locations: Record<string, LocationMapping>
  dataTypes: ConnectorDataType[]
  cadence: SyncCadence
  // CSV branch: the chosen sample file name + whether the (stubbed) analysis has run.
  csvFileName: string
  csvAnalysisReady: boolean
  // sourceField -> chosen canonical key. Absent => use the suggestion's suggested target.
  // The suggestion set itself is NOT held here: it lives in the route's react-query layer and is
  // passed into the gating helpers; the reducer holds only the operator's decisions.
  mappingOverrides: Record<string, string>
  // sourceField -> ignored (assign to the `__ignore__` catalog field; excluded from the template).
  ignored: Record<string, boolean>
  // The chosen template type (CSV branch picks it on the Template type step; POS defaults it).
  templateType: string
  // CSV branch source-format declarations (16a). Template-level locale (decimal/thousand) +
  // per-column datetime format / percentage flag, assembled into the create columns[] body.
  csvLocale: LocaleKey
  csvColumnFormat: Record<string, CsvColumnFormat>
  // Terminal results (branch-specific).
  liveSource: LiveConnectorSource | null
  createdTemplate: CreatedTemplate | null
}

export const initialConnectorWizardState: ConnectorWizardState = {
  branch: null,
  stepIndex: 0,
  connector: null,
  sourceName: '',
  authMethod: 'oauth',
  preAuth: {},
  tokenFields: {},
  account: null,
  locations: {},
  dataTypes: ['orders'],
  cadence: 'daily',
  csvFileName: '',
  csvAnalysisReady: false,
  mappingOverrides: {},
  ignored: {},
  templateType: '',
  csvLocale: 'us',
  csvColumnFormat: {},
  liveSource: null,
  createdTemplate: null,
}

export type ConnectorWizardAction =
  | { type: 'selectConnector'; connector: ConnectorKey }
  | { type: 'selectCsv' }
  | { type: 'setSourceName'; value: string }
  | { type: 'setAuthMethod'; method: ConnectorAuthMethod }
  | { type: 'setPreAuthField'; name: string; value: string }
  | { type: 'setTokenField'; name: string; value: string }
  | { type: 'setAccount'; account: ConnectorAccount }
  | { type: 'setLocationChecked'; locationId: string; checked: boolean }
  | { type: 'setLocationStore'; locationId: string; storeId: string }
  | { type: 'toggleDataType'; dataType: ConnectorDataType }
  | { type: 'setCadence'; cadence: SyncCadence }
  | { type: 'setCsvFile'; fileName: string }
  | { type: 'setCsvAnalysisReady' }
  | { type: 'setMappingTarget'; sourceField: string; target: string }
  | { type: 'toggleIgnore'; sourceField: string }
  | { type: 'setTemplateType'; value: string }
  | { type: 'setCsvLocale'; locale: LocaleKey }
  | { type: 'setCsvDatetimeFormat'; sourceField: string; format: string }
  | { type: 'setCsvPercentage'; sourceField: string; isPercentage: boolean }
  | { type: 'setLiveSource'; source: LiveConnectorSource }
  | { type: 'setCreatedTemplate'; template: CreatedTemplate }
  | { type: 'next' }
  | { type: 'back' }

function clampStep(index: number, flowLength: number): number {
  return Math.max(0, Math.min(flowLength - 1, index))
}

// Reset the per-branch decisions when (re)choosing a Source tile, so a half-built mapping for
// one branch/connector never leaks into another.
function resetForSourceChange(state: ConnectorWizardState): ConnectorWizardState {
  return {
    ...state,
    stepIndex: 0,
    preAuth: {},
    tokenFields: {},
    account: null,
    csvFileName: '',
    csvAnalysisReady: false,
    mappingOverrides: {},
    ignored: {},
    templateType: '',
    csvLocale: 'us',
    csvColumnFormat: {},
    liveSource: null,
    createdTemplate: null,
  }
}

export function connectorWizardReducer(
  state: ConnectorWizardState,
  action: ConnectorWizardAction,
): ConnectorWizardState {
  switch (action.type) {
    case 'selectConnector':
      return { ...resetForSourceChange(state), branch: 'pos', connector: action.connector }
    case 'selectCsv':
      return { ...resetForSourceChange(state), branch: 'csv', connector: null }
    case 'setSourceName':
      return { ...state, sourceName: action.value }
    case 'setAuthMethod':
      return { ...state, authMethod: action.method }
    case 'setPreAuthField':
      return { ...state, preAuth: { ...state.preAuth, [action.name]: action.value } }
    case 'setTokenField':
      return { ...state, tokenFields: { ...state.tokenFields, [action.name]: action.value } }
    case 'setAccount':
      return { ...state, account: action.account }
    case 'setLocationChecked':
      return {
        ...state,
        locations: {
          ...state.locations,
          [action.locationId]: {
            checked: action.checked,
            storeId: state.locations[action.locationId]?.storeId ?? '',
          },
        },
      }
    case 'setLocationStore':
      return {
        ...state,
        locations: {
          ...state.locations,
          [action.locationId]: {
            checked: state.locations[action.locationId]?.checked ?? true,
            storeId: action.storeId,
          },
        },
      }
    case 'toggleDataType': {
      const has = state.dataTypes.includes(action.dataType)
      return {
        ...state,
        dataTypes: has
          ? state.dataTypes.filter((d) => d !== action.dataType)
          : [...state.dataTypes, action.dataType],
      }
    }
    case 'setCadence':
      return { ...state, cadence: action.cadence }
    case 'setCsvFile':
      return { ...state, csvFileName: action.fileName, csvAnalysisReady: false }
    case 'setCsvAnalysisReady':
      return { ...state, csvAnalysisReady: true }
    case 'setMappingTarget':
      return {
        ...state,
        mappingOverrides: { ...state.mappingOverrides, [action.sourceField]: action.target },
      }
    case 'toggleIgnore':
      return {
        ...state,
        ignored: {
          ...state.ignored,
          [action.sourceField]: !(state.ignored[action.sourceField] ?? false),
        },
      }
    case 'setTemplateType':
      return { ...state, templateType: action.value }
    case 'setCsvLocale':
      return { ...state, csvLocale: action.locale }
    case 'setCsvDatetimeFormat':
      return {
        ...state,
        csvColumnFormat: {
          ...state.csvColumnFormat,
          [action.sourceField]: {
            ...state.csvColumnFormat[action.sourceField],
            datetimeFormat: action.format,
          },
        },
      }
    case 'setCsvPercentage':
      return {
        ...state,
        csvColumnFormat: {
          ...state.csvColumnFormat,
          [action.sourceField]: {
            ...state.csvColumnFormat[action.sourceField],
            isPercentage: action.isPercentage,
          },
        },
      }
    case 'setLiveSource':
      return { ...state, liveSource: action.source }
    case 'setCreatedTemplate':
      return { ...state, createdTemplate: action.template }
    case 'next':
      return { ...state, stepIndex: clampStep(state.stepIndex + 1, flowFor(state.branch).length) }
    case 'back':
      return { ...state, stepIndex: clampStep(state.stepIndex - 1, flowFor(state.branch).length) }
    default:
      return state
  }
}

// The active step key for the current branch + index.
export function currentStepKey(state: ConnectorWizardState): StepKey {
  const flow = flowFor(state.branch)
  return flow[clampStep(state.stepIndex, flow.length)]
}

// The canonical target for a field: the operator's override if set, else the suggested target,
// else '' (unmapped). Mirrors the CSV path's overrideFor pattern.
export function mappingTargetFor(
  state: ConnectorWizardState,
  field: ConnectorMappingField,
): string {
  return state.mappingOverrides[field.sourceField] ?? field.suggestedTarget ?? ''
}

export function isIgnored(state: ConnectorWizardState, sourceField: string): boolean {
  return state.ignored[sourceField] ?? false
}

// The fields that will actually contribute to the template (not ignored), in order. The
// suggestion set is passed in (it lives in the route's query layer, not the reducer).
export function activeMappingFields(
  state: ConnectorWizardState,
  mappingFields: ConnectorMappingField[],
): ConnectorMappingField[] {
  return mappingFields.filter((f) => !isIgnored(state, f.sourceField))
}

// Whether the wizard may advance from the current step. Pure: drives the Next/primary CTA
// disabled state. Switches on the active branch's step KEY. `mappingFields` is the
// (query-supplied) suggestion set, needed only by the mapping step. Preview is non-blocking
// (always advanceable); the terminal steps (live/created) cannot advance.
export function canAdvance(
  state: ConnectorWizardState,
  mappingFields: ConnectorMappingField[] = [],
): boolean {
  switch (currentStepKey(state)) {
    case 'source':
      return state.branch !== null
    case 'connect':
      // The authorize / token-submit action establishes the account and advances.
      return state.sourceName.trim().length > 0 && state.account !== null
    case 'authorized':
      return state.account !== null
    case 'locations': {
      const checked = Object.values(state.locations).filter((l) => l.checked)
      return checked.length > 0 && checked.every((l) => l.storeId.length > 0)
    }
    case 'dataSync':
      return state.dataTypes.length > 0
    case 'upload':
      return state.csvAnalysisReady && state.sourceName.trim().length > 0
    case 'templateType':
      return state.templateType.length > 0
    case 'mapping': {
      if (mappingFields.length === 0) {
        return false
      }
      return activeMappingFields(state, mappingFields).every(
        (f) => mappingTargetFor(state, f).length > 0,
      )
    }
    case 'preview':
      return true
    case 'live':
    case 'created':
      return false
    default:
      return false
  }
}

// Slugify the source name into a 16a-valid source_id (^[a-z0-9_]{1,128}$). PROVISIONAL stopgap
// (collision/quality risk): a dedicated source_id field or registry comes later. Empty/odd
// names degrade to a safe placeholder so the create body is always well-formed.
export function slugifySourceId(name: string): string {
  const slug = name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 128)
  return slug.length > 0 ? slug : 'csv_source'
}

// Assemble the Slice-16a create columns[] from the wizard state. Ignored columns -> dest_key
// "__ignore__" (no format). Otherwise dest_key is the chosen catalog key; format declarations
// are attached by the TARGET datatype: datetime/date -> src_datetime_format (per-column);
// number -> the template-level locale's decimal/thousand separators + the per-column percentage
// flag. Other datatypes carry no format declaration. `datatypeByKey` maps dest_key -> datatype
// (from the type-aware catalog the branch fetched).
export function assembleConnectorColumns(
  state: ConnectorWizardState,
  mappingFields: ConnectorMappingField[],
  datatypeByKey: Map<string, FieldDatatype | null>,
  locale: LocalePreset,
): ConnectorColumn[] {
  return mappingFields.map((field) => {
    const srcKey = field.sourceField
    if (isIgnored(state, srcKey)) {
      return { src_key: srcKey, dest_key: '__ignore__' }
    }
    const destKey = mappingTargetFor(state, field)
    const datatype = datatypeByKey.get(destKey) ?? null
    const decl = state.csvColumnFormat[srcKey]
    const column: ConnectorColumn = { src_key: srcKey, dest_key: destKey }
    if (datatype === 'datetime' || datatype === 'date') {
      column.src_datetime_format = decl?.datetimeFormat ?? null
    } else if (datatype === 'number') {
      column.src_decimal_separator = locale.decimal
      column.src_thousand_separator = locale.thousand
      if (decl?.isPercentage === true) {
        column.src_is_percentage = true
      }
    }
    return column
  })
}
