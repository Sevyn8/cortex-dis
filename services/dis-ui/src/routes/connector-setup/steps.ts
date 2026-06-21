// Unified Add Source wizard steps (Chunk 2). The surface now has TWO branches that share the
// shell (StepRail, container, typography) but run different step sequences:
//   - POS (live sync, Chunk 1):  Source -> Connect -> Authorized -> Locations -> Data & sync ->
//                                AI mapping -> Preview -> Live
//   - CSV / SFTP (Chunk 2):      Source -> Upload -> Template type -> AI mapping -> Preview ->
//                                Template created
// A step REGISTRY (label + per-step heading/description) is the single source of copy; each
// branch is just an ordered list of step keys into it. The route renders the active branch's
// flow behind the local StepRail; the reducer indexes into that flow.

export type StepKey =
  | 'source'
  | 'connect'
  | 'authorized'
  | 'locations'
  | 'dataSync'
  | 'mapping'
  | 'preview'
  | 'live'
  | 'upload'
  | 'templateType'
  | 'created'

export type StepDef = {
  // Short label shown in the StepRail chip.
  label: string
  // Per-step heading + description, rendered once by the route (shared type scale).
  title: string
  description: string
}

export const STEP_DEFS: Record<StepKey, StepDef> = {
  source: {
    label: 'Source',
    title: 'Add a source',
    description: 'Connect a live system or upload a file. Both feed the same canonical schema.',
  },
  connect: {
    label: 'Connect',
    title: 'Connect your account',
    description: 'Name this source and choose how to authenticate.',
  },
  authorized: {
    label: 'Authorized',
    title: 'Connection verified',
    description: 'We confirmed read-only access to your account.',
  },
  locations: {
    label: 'Locations',
    title: 'Map your locations',
    description: 'Choose which locations to sync and map each to a DIS store.',
  },
  dataSync: {
    label: 'Data & sync',
    title: 'Choose data and cadence',
    description: 'Pick which data types to sync and how often.',
  },
  mapping: {
    label: 'AI mapping',
    title: 'Review the field mapping',
    description:
      'We mapped each source field to the canonical schema. Confirm, correct, or ignore.',
  },
  preview: {
    label: 'Preview',
    title: 'Preview the canonical data',
    description: 'A sample of how your data maps to the canonical schema.',
  },
  live: {
    label: 'Live',
    title: 'Your source is live',
    description: 'Sales now sync automatically into DIS.',
  },
  upload: {
    label: 'Upload',
    title: 'Upload a sample',
    description: 'Upload a CSV (or point at an SFTP drop) so we can read its columns.',
  },
  templateType: {
    label: 'Template type',
    title: 'Choose the template type',
    description: 'Pick what this data represents. It determines the canonical fields you map to.',
  },
  created: {
    label: 'Template created',
    title: 'Template created',
    description: 'Your mapping template is live. New files for this source flow through it.',
  },
}

export type Branch = 'pos' | 'csv'

// The ordered step keys for each branch.
export const POS_FLOW: StepKey[] = [
  'source',
  'connect',
  'authorized',
  'locations',
  'dataSync',
  'mapping',
  'preview',
  'live',
]

export const CSV_FLOW: StepKey[] = [
  'source',
  'upload',
  'templateType',
  'mapping',
  'preview',
  'created',
]

// The active step-key list for a branch. POS is the default before a branch is chosen (both
// flows start with `source`, and the Source step cannot advance until a tile is picked).
export function flowFor(branch: Branch | null): StepKey[] {
  return branch === 'csv' ? CSV_FLOW : POS_FLOW
}

// ----- Backward-compat (Chunk 1) -------------------------------------------------------------
// The POS flow's label list + numeric index map, kept stable for the route's POS rendering and
// the existing state unit tests (which address POS steps by these indices).
export const CONNECTOR_STEPS: string[] = POS_FLOW.map((k) => STEP_DEFS[k].label)

export const CONNECTOR_STEP_INDEX = {
  source: 0,
  connect: 1,
  authorized: 2,
  locations: 3,
  dataSync: 4,
  mapping: 5,
  preview: 6,
  live: 7,
} as const

export const CONNECTOR_STEP_COUNT = POS_FLOW.length
