import { useReducer, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import { Link } from 'react-router'

import { useAuth } from '../auth/useAuth'

import { Button, buttonVariants } from '@/components/ui/button'
import {
  analyzeCsvSample,
  createConnectorSource,
  createCsvTemplate,
  exchangeToken,
  fetchCsvPreviewRows,
  fetchLocations,
  fetchMappingSuggestions,
  fetchPreviewRows,
  fetchTemplateType,
  initiateOAuth,
  localePreset,
  submitApiToken,
} from '../lib/dis-ui-server/connectors-api'
import {
  useTemplateMappingFields,
  useTemplateMappingFieldsForType,
} from '../lib/dis-ui-server/mapping-fields'
import type { FieldDatatype } from '../lib/dis-ui-server/mapping-fields'
import { useTemplateTypes } from '../lib/dis-ui-server/template-types'
import { useStoresOnboarded } from '../lib/dis-ui-server/stores'
import { StepRail } from './connector-setup/StepRail'
import { STEP_DEFS, flowFor } from './connector-setup/steps'
import {
  assembleConnectorColumns,
  canAdvance,
  connectorWizardReducer,
  currentStepKey,
  initialConnectorWizardState,
  isIgnored,
  mappingTargetFor,
  slugifySourceId,
} from './connector-setup/state'
import { SourceStep } from './connector-setup/SourceStep'
import { ConnectStep } from './connector-setup/ConnectStep'
import { AuthorizedStep } from './connector-setup/AuthorizedStep'
import { LocationsStep } from './connector-setup/LocationsStep'
import { DataSyncStep } from './connector-setup/DataSyncStep'
import { CsvUploadStep } from './connector-setup/CsvUploadStep'
import { TemplateTypeStep } from './connector-setup/TemplateTypeStep'
import { MappingStep } from './connector-setup/MappingStep'
import { PreviewStep } from './connector-setup/PreviewStep'
import { LiveStep } from './connector-setup/LiveStep'
import { CsvCreatedStep } from './connector-setup/CsvCreatedStep'
import { describeAnalyzeError, describeCreateError } from './connector-setup/wizard-errors'
import type { WizardErrorCopy } from './connector-setup/wizard-errors'

// Unified Add Source surface (Chunk 1 POS + Chunk 2 CSV/SFTP), at /connectors/new. Two branches
// share the shell (StepRail, container, typography) but run different step flows; the reducer is
// branch-aware. WIRED (mode-aware, fixture-backed in tests): GET /api/v1/template-types and GET
// /api/v1/template-mapping-fields?template_type=X drive the CSV branch's Template type step and
// type-aware canonical targets. STUBBED at the connectors-api seam: CSV sample upload/analysis,
// the create-template POST, and preview rows (contracts not confirmed) - plus the entire POS
// branch (Chunk 1). The existing old Add Source (/connect, MappingReview) is untouched.
export function ConnectorSetup() {
  const { snapshot } = useAuth()
  const [state, dispatch] = useReducer(connectorWizardReducer, initialConnectorWizardState)
  const [authorizing, setAuthorizing] = useState(false)
  // The real uploaded CSV File (non-serializable; kept out of the pure reducer). The reducer
  // holds only the file NAME for display/gating; this is what analyzeCsvSample actually parses.
  const [csvFile, setCsvFile] = useState<File | null>(null)
  // CSV create (POST /mapping-templates) submit state + the rendered failure copy. A 4xx (the
  // semantic gate) is caught and shown on the Preview step, never left as a console-only throw.
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<WizardErrorCopy | null>(null)

  const stepKey = currentStepKey(state)
  const isCsv = state.branch === 'csv'

  const stores = useStoresOnboarded(snapshot)

  // ----- POS branch data (all stubbed, unchanged from Chunk 1) -----
  const posCatalog = useTemplateMappingFields(state.branch === 'pos')
  const locationsQuery = useQuery({
    queryKey: ['connectors', 'locations', state.connector],
    queryFn: () => fetchLocations(state.connector ?? 'shopify'),
    enabled: state.branch === 'pos' && state.connector !== null,
    staleTime: Infinity,
    retry: false,
  })
  const posMappingQuery = useQuery({
    queryKey: ['connectors', 'mapping', state.connector, state.dataTypes],
    queryFn: () => fetchMappingSuggestions(state.connector ?? 'shopify', state.dataTypes),
    enabled: state.branch === 'pos' && stepKey === 'mapping',
    staleTime: Infinity,
    retry: false,
  })
  const posTemplateTypeQuery = useQuery({
    queryKey: ['connectors', 'template-type'],
    queryFn: fetchTemplateType,
    enabled: state.branch === 'pos' && stepKey === 'preview',
    staleTime: Infinity,
    retry: false,
  })
  const posPreviewQuery = useQuery({
    queryKey: ['connectors', 'preview'],
    queryFn: fetchPreviewRows,
    enabled: state.branch === 'pos' && stepKey === 'preview',
    staleTime: Infinity,
    retry: false,
  })

  // ----- CSV branch data (WIRED: type-aware catalog + types + analysis; stubbed preview) -----
  const templateTypesQuery = useTemplateTypes()
  // WIRED: the type-aware canonical catalog (requires the chosen template_type).
  const csvFieldsQuery = useTemplateMappingFieldsForType(isCsv ? state.templateType : null)
  // WIRED (D90): real parse (papaparse) + type-aware /mapping-suggestions. Runs at the mapping
  // step, once the file AND the chosen template_type are both known (queryKey keys on both).
  const csvAnalysisQuery = useQuery({
    queryKey: ['connectors', 'csv-analysis', state.csvFileName, state.templateType],
    queryFn: () => analyzeCsvSample(csvFile as File, state.templateType, csvFieldsQuery.data ?? []),
    // Gate on the type-aware catalog being loaded too: the fixture-mode mechanical matcher scores
    // against it (real mode ignores it and reads its own), so running before it lands would yield
    // empty suggestions. The catalog is keyed by template_type and stable, so this resolves once.
    enabled:
      isCsv &&
      stepKey === 'mapping' &&
      csvFile !== null &&
      state.templateType.length > 0 &&
      (csvFieldsQuery.data?.length ?? 0) > 0,
    staleTime: Infinity,
    retry: false,
  })
  // STUBBED: the CSV preview rows.
  const csvPreviewQuery = useQuery({
    queryKey: ['connectors', 'csv-preview'],
    queryFn: fetchCsvPreviewRows,
    enabled: isCsv && stepKey === 'preview',
    staleTime: Infinity,
    retry: false,
  })

  // Branch-resolved views the steps consume.
  const mapping = isCsv ? (csvAnalysisQuery.data ?? null) : (posMappingQuery.data ?? null)
  const mappingFields = mapping?.fields ?? []
  const mappingCatalog = isCsv ? (csvFieldsQuery.data ?? []) : (posCatalog.data ?? [])
  const mappingLoading = isCsv
    ? csvAnalysisQuery.isLoading || csvFieldsQuery.isLoading
    : posMappingQuery.isLoading || posCatalog.isPending
  const previewRows = isCsv ? (csvPreviewQuery.data ?? []) : (posPreviewQuery.data ?? [])
  const previewLoading = isCsv ? csvPreviewQuery.isLoading : posPreviewQuery.isLoading
  // CSV mapping step: a parse / suggestions / catalog failure is surfaced as an inline error with
  // a retry, instead of the perpetual loading spinner (mapping stays null on failure otherwise).
  const analyzeErrorRaw =
    isCsv && stepKey === 'mapping' ? (csvFieldsQuery.error ?? csvAnalysisQuery.error ?? null) : null
  function retryAnalyze(): void {
    void csvFieldsQuery.refetch()
    void csvAnalysisQuery.refetch()
  }
  // POS keeps its Chunk-1 template-type handling (stub fetch + preview select); CSV uses the
  // type chosen on its own step.
  const effectiveTemplateType = isCsv
    ? state.templateType
    : state.templateType !== ''
      ? state.templateType
      : (posTemplateTypeQuery.data?.value ?? '')

  // Authorize (OAuth) or submit the API token (POS). STUBBED: sets the account and advances.
  async function handleAuthorize(): Promise<void> {
    const connector = state.connector
    if (connector === null) {
      return
    }
    setAuthorizing(true)
    try {
      const account =
        state.authMethod === 'oauth'
          ? await initiateOAuth(connector, state.preAuth).then(() =>
              exchangeToken(connector, { state: `stub-state-${connector}` }),
            )
          : await submitApiToken(connector, state.tokenFields)
      dispatch({ type: 'setAccount', account })
      dispatch({ type: 'next' })
    } finally {
      setAuthorizing(false)
    }
  }

  // Create the live source (POS, STUBBED) and advance to the Live confirmation.
  async function handleGoLive(): Promise<void> {
    const connector = state.connector
    if (connector === null) {
      return
    }
    const locationIds = Object.entries(state.locations)
      .filter(([, m]) => m.checked)
      .map(([id]) => id)
    const ignoredFields = mappingFields
      .filter((f) => isIgnored(state, f.sourceField))
      .map((f) => mappingTargetFor(state, f))
      .filter((t) => t.length > 0)
    const source = await createConnectorSource({
      connector,
      sourceName: state.sourceName,
      authMethod: state.authMethod,
      locationIds,
      dataTypes: state.dataTypes,
      cadence: state.cadence,
      ignoredFields,
      templateType: effectiveTemplateType,
    })
    dispatch({ type: 'setLiveSource', source })
    dispatch({ type: 'next' })
  }

  // Create the mapping template (CSV, REAL: Slice-16a semantic columns[] contract, D89). Assemble
  // per-column { src_key, dest_key, format declarations } from the wizard state (ignored columns
  // -> dest_key "__ignore__"; datatype-driven format from the chosen locale + per-column picker),
  // then POST. 16a returns a SYNTHETIC 201 (nothing persisted until 16c); the Created step reads
  // the response honestly.
  async function handleCreateTemplate(): Promise<void> {
    // dest_key -> datatype, from the type-aware catalog, so the assembler attaches the right
    // format declarations (datetime format / number separators) per column.
    const datatypeByKey = new Map<string, FieldDatatype | null>()
    for (const f of csvFieldsQuery.data ?? []) {
      if (!datatypeByKey.has(f.key)) {
        datatypeByKey.set(f.key, f.datatype)
      }
    }
    const columns = assembleConnectorColumns(
      state,
      mappingFields,
      datatypeByKey,
      localePreset(state.csvLocale),
    )
    setCreateError(null)
    setCreating(true)
    try {
      const created = await createCsvTemplate({
        sourceId: slugifySourceId(state.sourceName),
        templateName: state.sourceName,
        templateType: state.templateType,
        columns,
      })
      dispatch({ type: 'setCreatedTemplate', template: created })
      dispatch({ type: 'next' })
    } catch (err) {
      // The backend's gate message (the named missing/illegal columns) is the actionable reason;
      // hold it for the Preview step. Wizard state is preserved so the user can correct and retry.
      setCreateError(describeCreateError(err))
    } finally {
      setCreating(false)
    }
  }

  function renderStep() {
    switch (stepKey) {
      case 'source':
        return <SourceStep state={state} dispatch={dispatch} />
      case 'connect':
        return (
          <ConnectStep
            state={state}
            dispatch={dispatch}
            onAuthorize={() => void handleAuthorize()}
            authorizing={authorizing}
          />
        )
      case 'authorized':
        return <AuthorizedStep state={state} />
      case 'locations':
        return (
          <LocationsStep
            state={state}
            dispatch={dispatch}
            locations={locationsQuery.data ?? []}
            stores={stores.data ?? []}
            loading={locationsQuery.isLoading}
          />
        )
      case 'dataSync':
        return <DataSyncStep state={state} dispatch={dispatch} />
      case 'upload':
        return (
          <CsvUploadStep
            state={state}
            dispatch={dispatch}
            file={csvFile}
            onSelectFile={setCsvFile}
          />
        )
      case 'templateType':
        return (
          <TemplateTypeStep
            state={state}
            dispatch={dispatch}
            templateTypes={templateTypesQuery.data ?? []}
            loading={templateTypesQuery.isLoading}
          />
        )
      case 'mapping':
        return (
          <MappingStep
            state={state}
            dispatch={dispatch}
            mapping={mapping}
            catalog={mappingCatalog}
            loading={mappingLoading}
            formatDeclarations={isCsv}
            error={analyzeErrorRaw !== null ? describeAnalyzeError(analyzeErrorRaw) : null}
            onRetry={retryAnalyze}
          />
        )
      case 'preview':
        return (
          <PreviewStep
            state={state}
            dispatch={dispatch}
            mappingFields={mappingFields}
            templateType={effectiveTemplateType}
            rows={previewRows}
            loading={previewLoading}
            readOnlyTemplateType={isCsv}
            createError={createError}
          />
        )
      case 'live':
        return <LiveStep state={state} />
      case 'created':
        return <CsvCreatedStep state={state} />
      default:
        return null
    }
  }

  // Footer primary action varies by step. Connect has no generic Next (its authorize button
  // advances); the terminal steps (live/created) show a Done link.
  function renderFooter() {
    if (stepKey === 'live' || stepKey === 'created') {
      return (
        <div className="flex gap-3">
          <Link to="/" className={buttonVariants({ variant: 'default' })}>
            Done
          </Link>
        </div>
      )
    }

    const back =
      state.stepIndex > 0 ? (
        <Button
          type="button"
          variant="ghost"
          onClick={() => {
            setCreateError(null)
            dispatch({ type: 'back' })
          }}
        >
          Back
        </Button>
      ) : null

    if (stepKey === 'connect') {
      // The authorize / token-submit button inside ConnectStep advances; only Back here.
      return <div className="flex gap-3">{back}</div>
    }

    const submitting = stepKey === 'preview' && isCsv && creating
    const label =
      stepKey === 'authorized'
        ? 'Continue'
        : stepKey === 'mapping'
          ? 'Continue to preview'
          : stepKey === 'preview'
            ? isCsv
              ? submitting
                ? 'Creating...'
                : 'Create template'
              : 'Go live'
            : 'Next'

    const onClick =
      stepKey === 'preview'
        ? isCsv
          ? () => void handleCreateTemplate()
          : () => void handleGoLive()
        : () => dispatch({ type: 'next' })

    return (
      <div className="flex gap-3">
        {back}
        <Button
          type="button"
          disabled={submitting || !canAdvance(state, mappingFields)}
          onClick={onClick}
        >
          {submitting ? <Loader2 aria-hidden="true" className="size-4 animate-spin" /> : null}
          {label}
        </Button>
      </div>
    )
  }

  const flow = flowFor(state.branch)
  const meta = STEP_DEFS[stepKey]

  return (
    // Centered container (~920px) so the wizard uses the page width. Page padding comes from
    // AppLayout's <main> (p-6); this only centers + caps width.
    <section className="mx-auto flex w-full max-w-[920px] flex-col gap-8">
      <header className="flex flex-col gap-3">
        <h1 className="text-display">Connect a system</h1>
        <p className="text-body text-muted-foreground">
          Connect a live system or upload a file. We map it to the canonical schema, you approve, it
          goes live.
        </p>
      </header>

      <StepRail steps={flow.map((k) => STEP_DEFS[k].label)} current={state.stepIndex} />

      <div className="flex flex-col gap-6">
        <header className="flex flex-col gap-1.5">
          <h2 className="text-[18px] leading-6 font-medium text-foreground">{meta.title}</h2>
          <p className="text-body text-muted-foreground">{meta.description}</p>
        </header>

        {renderStep()}
      </div>

      <div className="border-t border-border pt-6">{renderFooter()}</div>
    </section>
  )
}
