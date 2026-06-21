import { Navigate, Route, Routes } from 'react-router'

import { AuthBoundary } from '../auth/AuthBoundary'
import { OpsBoundary } from '../auth/OpsBoundary'
import { AppLayout } from './AppLayout'
import { AuditLookup } from './AuditLookup'
import { ConnectorSetup } from './ConnectorSetup'
import { DevLogin } from './DevLogin'
import { IndexRoute } from './IndexRoute'
import { IngestData } from './IngestData'
import { MappingVersions } from './MappingVersions'
import { NotFound } from './NotFound'
import { Notifications } from './Notifications'
import { QuarantineConsole } from './QuarantineConsole'
import { RecurringBatchUpload } from './RecurringBatchUpload'
import { Shadow } from './Shadow'
import { SourceCreate } from './SourceCreate'
import { SourceEdit } from './SourceEdit'
import { SourcesIndex } from './SourcesIndex'
import { SourceTemplates } from './SourceTemplates'
import { TemplateDetail } from './TemplateDetail'
import { StyleReference } from '../_style/StyleReference'

// Router-agnostic route registry. App.tsx wraps this in a BrowserRouter; tests
// wrap it in a MemoryRouter. /dev/login is public; everything under AuthBoundary
// requires a valid token. The index `/` renders the one Dashboard for every persona
// (IndexRoute; the Ops Fleet screen and its ops-landing redirect were removed). The
// /ops/* subtree is still wrapped in OpsBoundary (ops-gated). An unknown path renders
// the not-found state.
export function AppRoutes() {
  return (
    <Routes>
      <Route path="/dev/login" element={<DevLogin />} />
      {/* Dev-only style reference (redesign R1 de-risk gate). Public like /dev/login, NOT
          in the tenant/ops nav; renders the new visual language for light/dark review. */}
      <Route path="/dev/style" element={<StyleReference />} />
      <Route element={<AuthBoundary />}>
        <Route element={<AppLayout />}>
          <Route index element={<IndexRoute />} />
          {/* Connect a System: the unified add-source wizard (POS Live Sync branches +
              CSV/SFTP branch). The retired OLD Add Source picker (/connect) and the old
              /upload onboarding wizard were removed; this is the single add-source surface. */}
          <Route path="/connectors/new" element={<ConnectorSetup />} />
          {/* T5: Ingest Data is the flat template list (per-row ingest). Source CRUD
              stays at /sources (SourcesIndex), reached via its "Manage sources" link. */}
          <Route path="/ingest" element={<IngestData />} />
          <Route path="/sources" element={<SourcesIndex />} />
          <Route path="/sources/new" element={<SourceCreate />} />
          <Route path="/sources/:sourceId/edit" element={<SourceEdit />} />
          <Route path="/sources/:sourceId/mappings" element={<MappingVersions />} />
          {/* Template-aware mapping surface (T2, D68): templates list + template detail. */}
          <Route path="/sources/:sourceId/templates" element={<SourceTemplates />} />
          <Route path="/sources/:sourceId/templates/:templateId" element={<TemplateDetail />} />
          {/* Recurring-batch upload (T4): reuse a template's active mapping for a new batch. */}
          <Route
            path="/sources/:sourceId/templates/:templateId/upload"
            element={<RecurringBatchUpload />}
          />
          <Route path="/sources/:sourceId/shadow" element={<Shadow />} />
          <Route path="/quarantine" element={<QuarantineConsole />} />
          <Route path="/audit" element={<AuditLookup />} />
          <Route path="/notifications" element={<Notifications />} />
          {/* Ops subtree: OpsBoundary gates ALL /ops/* (non-ops -> PermissionDenied). The Ops
              Fleet screen was removed; the legacy quarantine/audit redirects remain. */}
          <Route path="/ops" element={<OpsBoundary />}>
            {/* T9: the separate fleet Quarantine/Audit routes are retired. Quarantine and Audit
                are now ONE scope-aware screen each at /quarantine and /audit (the component
                branches on isOps into fleet mode for ops). These redirects keep old fleet links
                working with no 404; they live inside OpsBoundary, so a non-ops user still hits
                PermissionDenied rather than being redirected. */}
            <Route path="quarantine" element={<Navigate to="/quarantine" replace />} />
            <Route path="audit" element={<Navigate to="/audit" replace />} />
            <Route path="*" element={<NotFound />} />
          </Route>
          <Route path="*" element={<NotFound />} />
        </Route>
      </Route>
    </Routes>
  )
}
