"""``POST /v1/csv-uploads`` — CSV upload Phase 1, synchronous (Slice 8).

Supersedes the D36 signed-URL mechanic and closes D54's completion-detection
fork (register entry at the commit gate): the file streams THROUGH this handler
in one request — size-guarded mid-stream, tier-0 gated (D51/D52), identity
resolved once (D37: tenant from the verified token, store from the mirror,
source from the template lineage), written to the canonical GCS path (D53),
audited, and announced via ``csv.received`` (D54: the worker trusts the event
and re-resolves nothing). No bronze write here — the worker owns bronze (D5).

The handler is SEQUENCE only (one concern per function): every gate and
resolution lives in its own module (``upload_stream`` / ``tier0`` / the repos /
``publisher``), and the order is load-bearing —

    auth → trace mint → stream+limit → tier-0 → template (404/409) →
    store (404, THEN the ACTIVE gate's 409 — no existence oracle) →
    GCS write (503) → publish (503; the object stays as an accepted orphan) →
    audit → 201.

trace_id is minted HERE (with §4.3, the only minting sites in this service) and
bound to the request context so every error envelope and audit row carries it
(hard rule 4: this endpoint IS the receiver).
"""

from __future__ import annotations

import hashlib
import time
from typing import Annotated, Any
from uuid import UUID

import anyio.to_thread
from fastapi import APIRouter, Depends, Request
from sqlalchemy import Row

from dis_audit import FailureCode, Outcome, Stage
from dis_core.errors import (
    DisError,
    EventPublishError,
    MappingStateConflictError,
    PayloadTooLargeError,
    ResourceNotFoundError,
    StorageError,
    StoreStateConflictError,
    UploadRequestError,
    UploadStructureError,
)
from dis_core.logging import get_logger
from dis_core.timestamps import now_utc
from dis_core.trace_id import bind_trace_id, new_trace_id
from dis_storage import StorageClient, build_object_path
from dis_ui_server.audit import UiAudit
from dis_ui_server.auth.identity import Identity
from dis_ui_server.auth.scope import require_tenant, tenant_uuid_of
from dis_ui_server.config import (
    CSV_RECEIVED_TOPIC,
    CSV_UPLOAD_BODY_CEILING_BYTES,
    CSV_UPLOAD_MAX_FILE_BYTES,
    SERVICE_NAME,
)
from dis_ui_server.publisher import Publisher, build_csv_received
from dis_ui_server.repos.mapping_templates import resolve_active_template
from dis_ui_server.repos.stores import resolve_store_by_code
from dis_ui_server.repos.tenants import get_tenant_display_code
from dis_ui_server.schemas.csv_uploads import CsvUploadResult
from dis_ui_server.tier0 import run_tier0
from dis_ui_server.upload_stream import ParsedUpload, read_csv_upload

router = APIRouter()

_log = get_logger(SERVICE_NAME)

# The store lifecycle states a CSV may be uploaded against (operator decision,
# Slice 8 review): ACTIVE only. OPENING has no live operations generating data
# yet — an upload there is more likely an onboarding mistake than real ingress,
# and a re-upload after activation is cheap. INACTIVE/CLOSED are not ingesting.
_UPLOADABLE_STORE_STATUSES = frozenset({"ACTIVE"})


def derive_upload_session_id(tenant_id: UUID, store_id: UUID, template_id: UUID, payload_sha256: str) -> str:
    """The deterministic per-upload lineage id (the resolved D54/D58 mechanic).

    ``us_`` + the first 12 lowercase-hex chars of SHA-256 over the upload's
    logical identity (tenant | store | template | content hash). Deterministic on
    purpose: a client RETRY of the same bytes re-derives the same id, so the
    worker's 24h dedup key ``(tenant, source_payload_id, payload_sha256)`` fires
    and exactly one bronze row exists per logical upload — a random mint would
    give every retry a fresh key and double-count id-less sources (D65). Hex is a
    subset of the contract's ``^us_[a-z0-9]{12}$``, so the wire pattern holds.
    """
    digest = hashlib.sha256(f"{tenant_id}|{store_id}|{template_id}|{payload_sha256}".encode()).hexdigest()
    return f"us_{digest[:12]}"


def _gate_store_uploadable(store: Row[Any], *, tenant_id: UUID, store_code: str) -> None:
    """The lifecycle gate, AFTER resolution: resolved-but-not-ACTIVE is a 409.

    Endpoint policy (not a repo concern): the store exists and IS the caller's —
    a cross-tenant code never reaches here (the repo's tenant predicate made it
    a 404), so this 409 confirms nothing a 404 was hiding.
    """
    if store.status not in _UPLOADABLE_STORE_STATUSES:
        raise StoreStateConflictError(
            f"store {store_code!r} is not ACTIVE; a CSV can only be uploaded for an ACTIVE store",
            store_id=str(store.store_id),
            store_code=store_code,
            tenant_id=str(tenant_id),
            expected="ACTIVE",
            actual=store.status,
        )


def _parse_template_id(parsed: ParsedUpload) -> UUID:
    """The ``template_id`` field as a UUID; a malformed value is a 400 (never echoed)."""
    raw = parsed.fields["template_id"].strip()
    try:
        return UUID(raw)
    except ValueError as exc:
        raise UploadRequestError(
            "template_id is not a valid UUID",
            part="template_id",
        ) from exc


# Tier-0's closed reason set -> the stable vocabulary (Slice 30b).
_TIER0_CODES: dict[str, FailureCode] = {
    "empty_file": FailureCode.UPLOAD_EMPTY_FILE,
    "not_utf8": FailureCode.UPLOAD_NOT_UTF8,
    "not_csv": FailureCode.UPLOAD_NOT_CSV,
    "below_min_rows": FailureCode.UPLOAD_BELOW_MIN_ROWS,
}


def _rejection_code(step: str, exc: DisError) -> FailureCode:
    """The stable ``FailureCode`` for a 4xx rejection (Slice 30b).

    ``step`` disambiguates the two ``ResourceNotFoundError`` sources (template
    vs store); class identity decides the rest. An unexpected ``DisError`` in
    the gate sequence falls through to ``INFRA_FAILURE`` (the emitting site
    preserves the class name in ``event_data``).
    """
    if isinstance(exc, PayloadTooLargeError):
        return FailureCode.UPLOAD_TOO_LARGE
    if isinstance(exc, UploadRequestError):
        return FailureCode.UPLOAD_REQUEST_MALFORMED
    if isinstance(exc, UploadStructureError):
        return _TIER0_CODES.get(exc.reason or "", FailureCode.UPLOAD_REQUEST_MALFORMED)
    if isinstance(exc, MappingStateConflictError):
        return FailureCode.TEMPLATE_NOT_ACTIVE
    if isinstance(exc, StoreStateConflictError):
        return FailureCode.STORE_NOT_ACTIVE
    if isinstance(exc, ResourceNotFoundError):
        return FailureCode.TEMPLATE_NOT_FOUND if step == "template" else FailureCode.STORE_NOT_FOUND
    return FailureCode.INFRA_FAILURE


@router.post("/csv-uploads", status_code=201)
async def upload_csv(
    request: Request,
    identity: Annotated[Identity, Depends(require_tenant)],
) -> CsvUploadResult:
    """One synchronous upload: multipart ``file`` + ``template_id`` + ``store_code``."""
    tenant_id = tenant_uuid_of(identity)  # token only — never the body (14b rule)
    trace_id = new_trace_id()  # this endpoint IS the receiver (hard rule 4)
    # Bound WITHOUT a reset, deliberately: the §2.3 exception handlers render the
    # envelope AFTER this coroutine unwinds, and they read the trace off this
    # context. The contextvar is task-local (one ASGI task per request), so the
    # binding dies with the request and cannot leak across requests.
    bind_trace_id(trace_id)
    return await _process_upload(request, identity=identity, tenant_id=tenant_id, trace_id=trace_id)


async def _process_upload(
    request: Request, *, identity: Identity, tenant_id: UUID, trace_id: UUID
) -> CsvUploadResult:
    engine = request.app.state.engine
    storage: StorageClient = request.app.state.storage
    publisher: Publisher = request.app.state.publisher
    audit: UiAudit = request.app.state.audit
    bucket: str = request.app.state.config.gcs_bucket_bronze
    log = _log.bind(stage="csv_upload", tenant_id=str(tenant_id), trace_id=str(trace_id))
    t0 = time.monotonic()

    # Steps 1-4 are the 4xx gate sequence. A rejection emits one FAILURE audit
    # with its stable FailureCode and then re-raises UNCHANGED (Slice 30b:
    # emit-then-re-raise — status codes and the §2.3 envelope are untouched;
    # the emit is fire-and-forget, so an audit failure can never turn a clean
    # 4xx into a 5xx). `step` disambiguates the two ResourceNotFoundError sites.
    step = "upload"
    try:
        # 1. Stream + limits (413 mid-stream) and multipart shape (400). The body is
        #    fully read here or not at all; nothing below re-reads the request.
        parsed = await read_csv_upload(
            request,
            max_file_bytes=CSV_UPLOAD_MAX_FILE_BYTES,
            body_ceiling_bytes=CSV_UPLOAD_BODY_CEILING_BYTES,
        )
        template_id = _parse_template_id(parsed)
        store_code = parsed.fields["store_code"].strip()

        # 2. Tier-0 structural gate (D51/D52): a failure is a clean 422 — no GCS
        #    write, no publish, nothing persisted.
        step = "tier0"
        tier0 = run_tier0(parsed.file_bytes, tenant_id=str(tenant_id), trace_id=str(trace_id))

        # 3. Template: 404 unknown/cross-tenant (RLS), 409 not ACTIVE. The ACTIVE row
        #    carries the lineage's source_id — the request never names a source.
        step = "template"
        template = await resolve_active_template(engine, tenant_id, template_id)
        source_id: str = template.source_id

        # 4. Store: resolve FIRST (404, no existence oracle), THEN the lifecycle
        #    gate (409, ACTIVE-only v1).
        step = "store"
        store = await resolve_store_by_code(engine, tenant_id, store_code)
        _gate_store_uploadable(store, tenant_id=tenant_id, store_code=store_code)
    except DisError as exc:
        code = _rejection_code(step, exc)
        extra: dict[str, Any] = {"step": step, "exception_class": type(exc).__name__}
        reason = getattr(exc, "reason", None)
        if reason:
            extra["reason"] = reason
        await _emit_failure(
            audit,
            identity,
            request,
            tenant_id,
            trace_id,
            code,
            exc,
            duration_ms=_elapsed_ms(t0),
            extra=extra,
        )
        raise

    display_code = await get_tenant_display_code(engine, tenant_id)

    # 5. Lineage + path (D53: UUID tenant segment; one timestamp keeps the path
    #    date and the event's received_ts coherent).
    payload_sha256 = hashlib.sha256(parsed.file_bytes).hexdigest()
    upload_id = derive_upload_session_id(tenant_id, store.store_id, template_id, payload_sha256)
    received_ts = now_utc()
    object_key = build_object_path(
        tenant_id=tenant_id,
        source_id=source_id,
        trace_id=trace_id,
        event_ts=received_ts,
        ext="csv",
    )
    gcs_uri = f"gs://{bucket}/{object_key}"

    # 6. GCS write, THEN publish (the worker fetches the object on consume; the
    #    reverse order would race a 404). The blocking client runs off the event
    #    loop (anyio, the root-CLAUDE.md async pattern).
    try:
        await anyio.to_thread.run_sync(
            lambda: storage.upload_bytes(object_key, parsed.file_bytes, content_type="text/csv")
        )
    except Exception as exc:
        failure = StorageError(
            f"GCS write failed for the uploaded CSV (bucket {bucket!r}): {type(exc).__name__}"
        )
        await _emit_failure(
            audit,
            identity,
            request,
            tenant_id,
            trace_id,
            FailureCode.GCS_WRITE_FAILED,
            failure,
            duration_ms=_elapsed_ms(t0),
            extra={"template_id": str(template_id), "store_code": store_code},
        )
        raise failure from exc

    envelope = build_csv_received(
        trace_id=trace_id,
        tenant_id=tenant_id,
        store_id=store.store_id,
        source_id=source_id,
        template_id=template_id,
        upload_session_id=upload_id,
        gcs_uri=gcs_uri,
        received_ts=received_ts,
        tenant_display_code=display_code,
        store_code=store_code,
    )
    try:
        await anyio.to_thread.run_sync(publisher.publish, CSV_RECEIVED_TOPIC, envelope.to_bytes())
    except Exception as exc:
        # The object is already written: an ACCEPTED ORPHAN (no bronze row exists,
        # nothing references it). Deliberately no compensating delete — a delete
        # can itself fail, and a client retry converges without it: same bytes →
        # the same deterministic upload_id → at most one bronze row ever.
        publish_failure = EventPublishError(
            f"csv.received publish failed after the GCS object was written: {type(exc).__name__}",
            topic=CSV_RECEIVED_TOPIC,
            tenant_id=str(tenant_id),
            trace_id=str(trace_id),
        )
        await _emit_failure(
            audit,
            identity,
            request,
            tenant_id,
            trace_id,
            FailureCode.PUBLISH_FAILED,
            publish_failure,
            duration_ms=_elapsed_ms(t0),
            extra={"template_id": str(template_id), "store_code": store_code, "upload_id": upload_id},
        )
        raise publish_failure from exc

    await audit.emit(
        stage=Stage.RECEIVED,
        outcome=Outcome.SUCCESS,
        tenant_id=tenant_id,
        trace_id=trace_id,
        row_count=tier0.row_count,
        duration_ms=_elapsed_ms(t0),
        event_data={
            "phase": "csv_upload_phase1",
            "upload_id": upload_id,
            "template_id": str(template_id),
            "gcs_uri": gcs_uri,
            "size_bytes": len(parsed.file_bytes),
            "topic": CSV_RECEIVED_TOPIC,
        },
        auth_principal=_auth_principal(identity),
        client_ip=_client_ip(request),
    )
    log.info("csv upload accepted and published")
    return CsvUploadResult(
        trace_id=trace_id,
        upload_id=upload_id,
        tenant_id=tenant_id,
        store_id=store.store_id,
        store_code=store_code,
        source_id=source_id,
        template_id=template_id,
        gcs_uri=gcs_uri,
        row_count=tier0.row_count,
        received_ts=received_ts,
        status="received",
    )


def _elapsed_ms(t0: float) -> int:
    """Whole-request elapsed for this endpoint's single audit row (Slice 30b)."""
    return max(int((time.monotonic() - t0) * 1000), 0)


async def _emit_failure(
    audit: UiAudit,
    identity: Identity,
    request: Request,
    tenant_id: UUID,
    trace_id: UUID,
    failure_code: str,
    failure: Exception,
    *,
    duration_ms: int | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """One FAILURE audit row — the 4xx gate family and the post-resolution 5xx paths.

    Tenant + trace are always known here (both exist before step 1). ``extra``
    rides event_data alongside the phase marker (step / reason / resolved ids).
    Fire-and-forget via UiAudit: an audit failure never alters the HTTP outcome.
    """
    await audit.emit(
        stage=Stage.RECEIVED,
        outcome=Outcome.FAILURE,
        tenant_id=tenant_id,
        trace_id=trace_id,
        duration_ms=duration_ms,
        failure_code=failure_code,
        failure_message=str(failure),
        event_data={"phase": "csv_upload_phase1", **(extra or {})},
        auth_principal=_auth_principal(identity),
        client_ip=_client_ip(request),
    )


def _auth_principal(identity: Identity) -> str:
    # The live bronze auth_principal comment's vocabulary: user:{user_id} for
    # csv_upload. Reused here so audit and bronze speak one principal form.
    return f"user:{identity.user_id}"


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client is not None else None
