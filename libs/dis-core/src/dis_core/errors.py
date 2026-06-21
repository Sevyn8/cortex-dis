"""The DIS error hierarchy — single ``DisError`` root.

Every error raised by DIS code descends from :class:`DisError`. Root CLAUDE.md:
"Define error types in ``libs/dis-core/errors.py``. Don't reach for raw
``RuntimeError`` or ``ValueError``."

This module is **leaf-level** within ``dis-core``: it imports nothing first-party
(no ``dis_core.identity``, no other ``dis_core`` module). Submodules that need
these errors import *from* here, never the other way round, so the import graph
stays acyclic and ``dis-testing`` (which depends on ``dis-core``) can reparent its
test-only errors onto :class:`DisError` without inversion.

The Identity Service client errors live here (moved from
``dis_core.identity.client`` in Slice 3) and are re-exported by
``dis_core.identity`` for backward compatibility, so existing imports
(``from dis_core.identity import IdentityNotFoundError``) are unchanged.

Per-domain errors for the data-plane libs (RLS, PII, storage, mapping,
validation, audit) are added by their own slices as they gain a real raiser;
this module ships the root, the consolidated Slice 2 interim identity errors, and
(Slice 4) the data-plane errors for ``dis-rls``, ``dis-pii``, and ``dis-storage``
(build to current need).
"""

from __future__ import annotations


class DisError(Exception):
    """Root of every DIS error. Catch this to catch any DIS-domain failure."""


# -- Identity Service client errors --------------------------------------------
# Moved verbatim from dis_core.identity.client (Slice 3 consolidation). Signatures
# are preserved so the identity client and its tests are unaffected.


class IdentityClientError(DisError):
    """Base error for Identity Service client calls.

    Carries the contract's ``Error`` envelope fields when the server returned one,
    plus the HTTP status code for callers that branch on transport-level outcome.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_code: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        self.trace_id = trace_id


class IdentityNotFoundError(IdentityClientError):
    """The token / upload session / endpoint config did not map to a tenant+store.

    Maps to HTTP 404 / ``error_code == "identity_not_found"``. Hard failure: callers
    should not retry.
    """


class IdentityServiceUnavailableError(IdentityClientError):
    """Customer Master unhealthy and stale window exceeded (HTTP 503 / circuit_open).

    Resolve callers retry with backoff; validate callers fall back to identity_mirror.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_code: str | None = None,
        trace_id: str | None = None,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(
            message,
            status_code=status_code,
            error_code=error_code,
            trace_id=trace_id,
        )
        self.retry_after = retry_after


# -- Data-plane safety errors (Slice 4) ----------------------------------------
# Raised by dis-rls / dis-pii / dis-storage. Each carries the load-bearing context
# (root CLAUDE.md code-quality rule 5: errors carry tenant_id, trace_id, and the
# load-bearing identifier). NONE of these ever carry a raw PII value (hard rule 2).


class RlsContextError(DisError):
    """The RLS session could not be opened safely.

    Raised by ``dis-rls`` when the connection reached the wrong target (e.g. not
    ``ithina_dis_db``) or the connected role can bypass RLS (SUPERUSER or BYPASSRLS),
    either of which would make tenant isolation silently void. Carries what was
    actually observed on the server so the failure is diagnosable.
    """

    def __init__(
        self,
        message: str,
        *,
        database: str | None = None,
        role: str | None = None,
        tenant_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.database = database
        self.role = role
        self.tenant_id = tenant_id


class PiiBackendNotConfiguredError(DisError):
    """A source mapping flags PII column(s) but no backend is configured to handle them.

    The ``dis-pii`` fail-loud gate raises this *before* any persistence path so PII
    cannot land silently (hard rule 2). ``columns`` are the flagged column *names*
    only — never the values. In v1.0 no real backend exists, so the gate raises on
    every detected PII column.
    """

    def __init__(
        self,
        message: str,
        *,
        columns: tuple[str, ...] = (),
        tenant_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.columns = columns
        self.tenant_id = tenant_id
        self.trace_id = trace_id


class StorageError(DisError):
    """A ``dis-storage`` operation failed (path construction, signing, or object access).

    Carries the object path and tenant/trace context when known; never a payload.
    """

    def __init__(
        self,
        message: str,
        *,
        object_path: str | None = None,
        tenant_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.object_path = object_path
        self.tenant_id = tenant_id
        self.trace_id = trace_id


# -- Audit error (Slice 6) -----------------------------------------------------
# Raised by dis-audit. Audit emission is fire-and-forget (hard rule 11): the writer
# logs this with context and reports failure rather than propagating it, so the data
# path is never blocked. Carries the load-bearing identifiers (code-quality rule 5);
# never a raw PII value or payload.


class AuditWriteError(DisError):
    """An ``audit.events`` write could not be performed.

    Raised by ``dis-audit`` backend selection (``select_writer``) when a required value is
    missing — e.g. the Postgres backend without an engine (no silent fallback, code-quality
    rule 4). Note the *fire-and-forget* write path does NOT raise: a write failure or a
    tenant-less event (the D43 contract violation) is logged and reported as ``False`` so the
    data path is never blocked (hard rule 11). Carries ``tenant_id`` / ``trace_id`` / ``stage``
    / ``failure_code`` for diagnosis.
    """

    def __init__(
        self,
        message: str,
        *,
        tenant_id: str | None = None,
        trace_id: str | None = None,
        stage: str | None = None,
        failure_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.tenant_id = tenant_id
        self.trace_id = trace_id
        self.stage = stage
        self.failure_code = failure_code


# -- Quarantine error (Slice 11a) ------------------------------------------------
# Raised by dis-quarantine. The OPPOSITE posture to AuditWriteError's path: the
# quarantine write is the data path (the held thing itself, not a record of it), so
# a failed write RAISES loudly — the consumer must NACK the message rather than
# ack-and-lose it. Never swallowed.


class QuarantineWriteError(DisError):
    """A ``quarantine.*`` write could not be performed.

    Raised by ``dis-quarantine``'s writer on any insert failure (store down, FK
    rejection, RLS misconfiguration) and on a tenant-less record (the same D43-shaped
    contract: there is no tenant-less quarantine path). Deliberately NOT
    fire-and-forget — quarantine holds the failed data itself, so the caller must
    see the failure and keep the message live (nack; the Pub/Sub dead-letter policy
    backstops). Carries ``tenant_id`` / ``trace_id`` / ``failure_code`` for diagnosis.
    """

    def __init__(
        self,
        message: str,
        *,
        tenant_id: str | None = None,
        trace_id: str | None = None,
        failure_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.tenant_id = tenant_id
        self.trace_id = trace_id
        self.failure_code = failure_code


# -- Pipeline-mechanics errors (Slice 5) ----------------------------------------
# Raised by dis-mapping and dis-validation. Both libs are pure (no I/O); these are
# *config / contract* errors raised loudly at construction or materialization time
# (code-quality rule 4) — they are NOT the per-cell / per-row data failures, which
# are returned as typed result objects, never raised (slice-05, D18/D20). Each
# carries the load-bearing identifiers (code-quality rule 5); NEVER a cell value.


class MappingError(DisError):
    """Base for dis-mapping failures.

    Carries optional ``tenant_id`` / ``trace_id`` (when the caller supplied a log
    context) and the load-bearing ``column`` where one applies. Never a cell value
    (root CLAUDE.md: never log PII or raw payloads).
    """

    def __init__(
        self,
        message: str,
        *,
        column: str | None = None,
        tenant_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.column = column
        self.tenant_id = tenant_id
        self.trace_id = trace_id


class MappingConfigError(MappingError):
    """The mapping config (``mapping_rules``) is invalid — fail loud at construction.

    Raised by ``SourceMapping`` validation: unknown op, missing/invalid op args
    (including the mandatory ``parse_decimal``/``parse_integer`` separator
    declarations — locale is asserted, never inferred), colliding rename targets,
    normalize/cast keys that no rename produces, derive targets that collide with
    renames, or a transform list whose composition is type-invalid. Never deferred
    to runtime (code-quality rule 4).
    """


class InvalidTemplateTypeError(MappingError):
    """A ``template_type`` outside the in-code vocabulary (Slice 14d).

    Raised when a request (the type-aware field catalog, or create/edit) names a
    ``template_type`` that is not a member of ``dis_validation.TEMPLATE_TYPES``,
    or attempts to change a template's lineage-fixed type. Maps to HTTP 400.
    Carries the offending value so the envelope can name it (never PII).
    """

    def __init__(
        self,
        message: str,
        *,
        template_type: str | None = None,
        tenant_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        super().__init__(message, tenant_id=tenant_id, trace_id=trace_id)
        self.template_type = template_type


class MappingInputError(MappingError):
    """The chunk handed to the engine violates the caller contract.

    Raised when a column the mapping's rename declares is absent from the input
    frame, or a column with declared normalize rules is not string-typed. This is
    a caller-contract violation (the source-shape suite gates chunk shape before
    mapping in the pipeline, D18), not a per-cell data failure — so it raises
    rather than returning failures.
    """


class ValidationSuiteError(DisError):
    """Base for dis-validation failures (suite definition / materialization layer).

    NOT the per-row validation outcomes — those are returned as typed result
    objects (``SourceShapeFailure`` / ``CanonicalShapeFailure``), never raised.
    """

    def __init__(
        self,
        message: str,
        *,
        model: str | None = None,
        column: str | None = None,
        tenant_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.model = model
        self.column = column
        self.tenant_id = tenant_id
        self.trace_id = trace_id


class SuiteDefinitionError(ValidationSuiteError):
    """A suite definition is invalid or cannot be materialized into a Pandera schema.

    E.g. an owned column the target model does not carry, an unsupported dtype in
    the model-to-Pandera deriver, or a malformed expectation. Fail loud at
    materialization, never at validation runtime.
    """


class SuiteDriftError(ValidationSuiteError):
    """The canonical-shape suite and the canonical model have drifted apart.

    Raised by the drift guard (``dis_validation.provenance``) when the provenance
    classification does not partition the model's field set exactly (both
    directions), when a suite's column set differs from its declared source-owned
    set, or when a mapping-time suite is requested for a model that is not
    mapping-produced (``store_sku_signal_history``, D22/D31/D32). This error is the
    *proof mechanism* of slice-05 criterion 6: it errors rather than skips.
    """


# -- Enrichment error (Slice 5b) -------------------------------------------------
# Raised by dis-enrichment (pure lib): a CONFIG/CONTRACT error raised loudly at the
# call boundary (code-quality rule 4), NOT a per-row data failure. Carries the
# load-bearing identifiers (rule 5); never a resolved value.


class EnrichmentError(DisError):
    """The enrichment caller-contract was violated — fail loud (slice-5b).

    Raised by ``apply_enrichment`` when the handed-in ``facts`` omit a registered
    field for the target table: the consumer must resolve every registered field
    from the authoritative internal source before calling the pure engine. NOT the
    present-but-blank case (D97, deferred — written through this slice). Carries
    ``table`` plus optional ``tenant_id`` / ``trace_id``; never a resolved value.
    """

    def __init__(
        self,
        message: str,
        *,
        table: str | None = None,
        tenant_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.table = table
        self.tenant_id = tenant_id
        self.trace_id = trace_id


# -- Streaming-consumer canonical-sink errors (Slice 30b) ------------------------
# Raised by the consumer's dual-write sink. Distinct classes exist where the audit
# trail needs a stable failure_code (dis-audit FailureCode maps exception type ->
# code); a bare DisError would fall through to the INFRA_FAILURE catch-all bucket.


class HotPositionMissingError(DisError):
    """The REVISED-D63 hot-merge miss: a first-seen SKU on an INCOMPLETE-mapping chunk.

    No ``store_sku_current_position`` row exists and the completeness-gated
    projection cannot create one; the event history is RETAINED (the batch already
    committed) and the chunk nacks toward quarantine (Slice 11). Carries the
    load-bearing identifiers (code-quality rule 5) so the FAILURE audit can join
    back to the chunk and the mapping.
    """

    def __init__(
        self,
        message: str,
        *,
        tenant_id: str | None = None,
        trace_id: str | None = None,
        mapping_version_id: int | None = None,
        miss_count: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.tenant_id = tenant_id
        self.trace_id = trace_id
        self.mapping_version_id = mapping_version_id
        self.miss_count = miss_count


# -- Mirror Sync errors (Slice 7) ----------------------------------------------
# Raised by the mirror-sync-consumer service (DB-pull mode), which reads Customer
# Master's Postgres under a platform read context and upserts into identity_mirror.
# Each carries trace_id and the load-bearing identifier (code-quality rule 5); never
# a raw payload. The DIS-side write reuses dis-rls' RlsContextError (wrong DB / role).


class MirrorSyncError(DisError):
    """Base for mirror-sync-consumer (DB-pull) failures.

    Carries the per-run ``trace_id`` and, where a per-tenant context applies, the
    ``tenant_id``, so a failed sync is diagnosable (code-quality rule 5).
    """

    def __init__(
        self,
        message: str,
        *,
        trace_id: str | None = None,
        tenant_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.trace_id = trace_id
        self.tenant_id = tenant_id


class CustomerMasterReadError(MirrorSyncError):
    """The Customer Master read could not be performed safely.

    Raised when the CM connection reached the wrong target (not the expected
    Customer Master database, or — worse — the DIS database), or the platform read
    context did not take effect in the read transaction (``app.user_type`` is not
    ``'PLATFORM'``). Under CM's FORCE RLS a mis-set context silently returns zero
    rows (``docs/ithina_master_db_read_access.md`` §2), so this is raised **before**
    any mirror write rather than letting an empty read masquerade as an empty source.
    Carries what was observed on the server (``database`` / ``role`` / ``user_type``).
    """

    def __init__(
        self,
        message: str,
        *,
        database: str | None = None,
        role: str | None = None,
        user_type: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        super().__init__(message, trace_id=trace_id)
        self.database = database
        self.role = role
        self.user_type = user_type


# -- CSV ingest worker errors (Slice 9b) -----------------------------------------
# Raised by services/csv-ingest-worker (the Phase-2 CSV worker, D36/D54). Each carries
# tenant_id / trace_id where known and the load-bearing detail (code-quality rule 5);
# never a payload, a cell value, or a PII value. The worker reads identity and
# trace_id off the csv.received event (D54) — none of these errors is ever raised
# with a worker-minted trace_id.


class CsvIngestError(DisError):
    """Base for csv-ingest-worker failures.

    Carries the event's ``tenant_id`` / ``trace_id`` (both read from the
    ``csv.received`` envelope, never minted by the worker, hard rule 4) so every
    ingest failure is diagnosable end-to-end.
    """

    def __init__(
        self,
        message: str,
        *,
        tenant_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.tenant_id = tenant_id
        self.trace_id = trace_id


class EventContractError(CsvIngestError):
    """An inbound event envelope violates its frozen contract (hard rule 10).

    Raised when a required field is missing, empty, or malformed (e.g. a non-UUID
    identity field, a bad ``upload_session_id``). Required values never fall back
    silently (code-quality rule 4) — this includes the idempotency-key components.
    ``field`` names the violating contract field. Terminal: a redelivery of the same
    malformed envelope fails identically, so the consumer acks after raising.
    """

    def __init__(
        self,
        message: str,
        *,
        field: str | None = None,
        tenant_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        super().__init__(message, tenant_id=tenant_id, trace_id=trace_id)
        self.field = field


class EventPathMismatchError(CsvIngestError):
    """The event's identity and its GCS path's parsed components disagree.

    The event is the trust boundary (D54) — the worker never re-resolves identity —
    but the canonical object path embeds tenant/source/trace (D53), so a mismatch
    means a malformed producer, not a resolution question. Raised loudly before any
    read of the object. Carries which ``field`` diverged and both observed values
    (identifiers only, never payload).
    """

    def __init__(
        self,
        message: str,
        *,
        field: str | None = None,
        event_value: str | None = None,
        path_value: str | None = None,
        tenant_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        super().__init__(message, tenant_id=tenant_id, trace_id=trace_id)
        self.field = field
        self.event_value = event_value
        self.path_value = path_value


class PreflightFailedError(CsvIngestError):
    """The DuckDB structural preflight rejected the object (D13/D16).

    Structural only: does-not-parse-as-CSV, no header, implausible structure.
    Column- and mapping-aware failures are the source-shape suite's (Slice 10) and
    are never raised here. ``reason`` is a short machine-stable code; ``detail`` is
    human context (column counts, row counts — never cell values or payload).
    """

    def __init__(
        self,
        message: str,
        *,
        reason: str | None = None,
        detail: str | None = None,
        tenant_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        super().__init__(message, tenant_id=tenant_id, trace_id=trace_id)
        self.reason = reason
        self.detail = detail


# -- dis-ui-server auth-seam errors (Slice 13a) ---------------------------------
# Raised by the dis-ui-server auth dependency chain (API_CONTRACT.md §2.1/§2.3) and
# mapped by its FastAPI exception handlers to 401/403 + the §2.3 error envelope.
# The seam is the SOLE source of tenant_id (never a body / query / unverified
# header), so these errors are the only way a request without a verified scope
# proceeds — by not proceeding. None ever carries a token value or a claim payload
# (hard rule 2 posture: a raw bearer token is credential material, never context).


class AuthTokenError(DisError):
    """The bearer token is missing, expired, malformed, or carries bad claims.

    Maps to HTTP 401 (contract §2.3). ``reason`` is a short machine-stable code
    (e.g. ``missing_bearer``, ``expired``, ``bad_claims``) — never the token
    itself and never raw claim values.
    """

    def __init__(
        self,
        message: str,
        *,
        reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.reason = reason


class TenantScopeError(DisError):
    """A tenant-scoped endpoint was called without a tenant in the verified token.

    Maps to HTTP 403 (contract §2.3). Also covers a resource that belongs to
    another tenant. ``tenant_id`` is the verified token's tenant where one exists
    (``None`` for a platform user hitting a tenant endpoint).
    """

    def __init__(
        self,
        message: str,
        *,
        tenant_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.tenant_id = tenant_id


class OpsRoleRequiredError(DisError):
    """An ops endpoint was called without the ``dis:ops`` role. Maps to HTTP 403."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


# -- dis-ui-server data-endpoint errors (Slice 14b) -----------------------------
# Raised by the dis-ui-server mapping-template handlers/repos (API_CONTRACT.md
# §2.3) and mapped by its FastAPI exception handlers to the §2.3 envelope. Each
# carries the load-bearing identifiers (code-quality rule 5); none ever carries a
# mapping_rules payload or any request-body value.


class ResourceNotFoundError(DisError):
    """A throw-style lookup found no row visible to the caller. Maps to HTTP 404.

    Used where the contract pins throw-style (not 200-with-null) semantics, e.g.
    ``GET /mapping-templates/{template_id}``. Under RLS, "does not exist" and
    "belongs to another tenant" are deliberately the same 404 (no existence
    oracle). ``resource`` is the resource family (e.g. ``mapping_template``);
    ``identifier`` is the looked-up key.
    """

    def __init__(
        self,
        message: str,
        *,
        resource: str | None = None,
        identifier: str | None = None,
        tenant_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.resource = resource
        self.identifier = identifier
        self.tenant_id = tenant_id


class MappingTemplateNameConflictError(DisError):
    """``template_name`` is already taken by another template of the same source.

    Maps to HTTP 409. The database arbiter is the GiST EXCLUDE constraint
    ``ex_csm_template_name_per_source`` (name unique per ``(tenant, source)``
    among non-DEPRECATED rows, version rows of ONE template excepted); this error
    is its domain form, raised by the repo's IntegrityError translation — never
    a raw 500.
    """

    def __init__(
        self,
        message: str,
        *,
        tenant_id: str | None = None,
        source_id: str | None = None,
        template_name: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.tenant_id = tenant_id
        self.source_id = source_id
        self.template_name = template_name


class MappingStateConflictError(DisError):
    """The template's lifecycle state refuses the requested operation. Maps to HTTP 409.

    Raised when an edit targets a template whose every version is DEPRECATED
    (D17: the lineage is closed; create a new template), or when a concurrent
    edit lost the per-template version-sequence race (``uq_csm_seq_per_source``).
    ``expected``/``actual`` name the state mismatch in lifecycle vocabulary.
    """

    def __init__(
        self,
        message: str,
        *,
        template_id: str | None = None,
        tenant_id: str | None = None,
        expected: str | None = None,
        actual: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.template_id = template_id
        self.tenant_id = tenant_id
        self.expected = expected
        self.actual = actual


class FieldCatalogDriftError(DisError):
    """The authored field-catalog labels drifted from the derived canonical field set.

    Raised at dis-ui-server startup by the catalog builder's both-directions
    check: a mapping-produced canonical column without an authored label, or an
    authored label whose column no longer exists. Either way the catalog cannot
    be served truthfully — fail the boot loudly (crashloop is the correct
    misconfiguration signal), never serve a partial catalog. ``missing`` /
    ``stale`` carry column names only.
    """

    def __init__(
        self,
        message: str,
        *,
        missing: tuple[str, ...] = (),
        stale: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.message = message
        self.missing = missing
        self.stale = stale


# -- dis-ui-server CSV-upload errors (Slice 8) -----------------------------------
# Raised by the synchronous csv-uploads endpoint (the CSV-upload Phase 1 receiver;
# supersedes the D36 signed-URL mechanic) and mapped by the dis-ui-server exception
# handlers to the §2.3 envelope. Each carries the load-bearing identifiers
# (code-quality rule 5); none ever carries file bytes, a multipart field value
# beyond the named identifiers, or any payload content (never log/echo PII).


class PayloadTooLargeError(DisError):
    """The upload body crossed the size ceiling. Maps to HTTP 413.

    Raised either by the spoofable-but-cheap ``Content-Length`` early check or by
    the real boundary — the streaming guard that counts bytes as they arrive and
    aborts mid-stream, never after a full read. ``limit_bytes`` is the enforced
    ceiling; ``observed_bytes`` is the declared or counted size at rejection.
    """

    def __init__(
        self,
        message: str,
        *,
        limit_bytes: int | None = None,
        observed_bytes: int | None = None,
        tenant_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.limit_bytes = limit_bytes
        self.observed_bytes = observed_bytes
        self.tenant_id = tenant_id
        self.trace_id = trace_id


class UploadRequestError(DisError):
    """The multipart request is malformed. Maps to HTTP 400.

    A required part is missing (``file``, ``template_id``, ``store_code``), a
    field is not parseable in its declared form (e.g. ``template_id`` is not a
    UUID), or the body is not parseable multipart at all. ``part`` names the
    offending part; values are never echoed (a request body can contain anything,
    including PII).
    """

    def __init__(
        self,
        message: str,
        *,
        part: str | None = None,
        tenant_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.part = part
        self.tenant_id = tenant_id
        self.trace_id = trace_id


class UploadStructureError(DisError):
    """The uploaded file failed the tier-0 structural gate (D51). Maps to HTTP 422.

    Structural only: empty file, does not decode as UTF-8, does not parse as CSV,
    below the minimum-rows floor. Column- and mapping-aware checks are tier 1
    (the source-shape suite, downstream) and are never raised here. ``reason`` is
    a short machine-stable code (``empty_file``, ``not_utf8``, ``not_csv``,
    ``below_min_rows``); never cell values, never payload.
    """

    def __init__(
        self,
        message: str,
        *,
        reason: str | None = None,
        tenant_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.reason = reason
        self.tenant_id = tenant_id
        self.trace_id = trace_id


class StoreStateConflictError(DisError):
    """The store's lifecycle state refuses the requested operation. Maps to HTTP 409.

    The :class:`MappingStateConflictError` shape for stores: the request is
    well-formed and the store IS the caller's (a cross-tenant or unknown
    ``store_code`` is a 404 *before* this gate — no existence oracle), but its
    ``identity_mirror.stores.status`` is not in the operation's allowed set
    (CSV upload v1: ACTIVE only). Operator-resolvable in Customer Master.
    """

    def __init__(
        self,
        message: str,
        *,
        store_id: str | None = None,
        store_code: str | None = None,
        tenant_id: str | None = None,
        expected: str | None = None,
        actual: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.store_id = store_id
        self.store_code = store_code
        self.tenant_id = tenant_id
        self.expected = expected
        self.actual = actual


class EventPublishError(DisError):
    """A Pub/Sub publish failed after the side effects before it succeeded. Maps to HTTP 503.

    Raised by the csv-uploads endpoint when the ``csv.received`` publish fails
    AFTER the GCS object is written (the accepted orphan-object posture: the
    object is unreferenced, no bronze row exists, and a client retry converges via
    the deterministic ``upload_session_id``). Retryable: the dependency, not the
    request, is at fault. ``topic`` names the target; the payload is never carried.
    """

    def __init__(
        self,
        message: str,
        *,
        topic: str | None = None,
        tenant_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.topic = topic
        self.tenant_id = tenant_id
        self.trace_id = trace_id
