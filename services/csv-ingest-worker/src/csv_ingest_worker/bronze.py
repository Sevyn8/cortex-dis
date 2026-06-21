"""The bronze sink: dedup lookup, the one metadata-only INSERT, and the publish mark.

Every statement here runs on a connection yielded by ``dis-rls`` ``rls_session``
under the EVENT's tenant (hard rules 1 & 12) — tenant scoping is the RLS policy's
(``bronze.data_ingress_events`` is FORCE RLS, ``tenant_isolation`` USING + WITH
CHECK on ``app.tenant_id``), inherited target guard included
(``current_database()=='ithina_dis_db'``, NOBYPASSRLS; DIS on 5433, never CM).

Metadata only: pointer, identity, hash, counts, status. NEVER payload bytes, never
cell values (hard rule 2).

Idempotency key (D54 / build-guide): ``(tenant, source_payload_id=upload_session_id,
payload_sha256)`` within ``DEDUP_WINDOW_HOURS`` measured against the prior row's
``received_at``. The key components are required values — empty ones raise
``EventContractError`` here as the last line even though the envelope already
enforces them (code-quality rule 4: never a silent fallback). The pre-9b smoke rows
carry NULL ``source_payload_id``/``payload_sha256`` and can never match the
non-NULL equality.

CONCURRENCY (registered in decisions.md D58): the check is query-based — there is
no UNIQUE constraint over the key (a 24h *window* cannot be a plain unique index) —
so it is correct for a SINGLE worker instance. Scaling to concurrent instances
requires a constraint/upsert design first.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from csv_ingest_worker.config import DEDUP_WINDOW_HOURS
from dis_core.errors import EventContractError
from dis_core.timestamps import now_utc

# The worker writes only these two lifecycle states; PUBLISHED is reached via
# mark_published, PROCESSED/QUARANTINED belong to later pipeline stages (Slice 10/11).
ProcessingStatus = Literal["RECEIVED", "FAILED"]

DIS_CHANNEL = "csv_upload"
CONTENT_TYPE = "text/csv"

_STATUS_PUBLISHED = "PUBLISHED"


@dataclass(frozen=True)
class PriorIngest:
    """The prior bronze row a dedup hit found (the idempotent-return source)."""

    bronze_id: UUID
    trace_id: UUID
    store_id: UUID | None
    source_id: str
    gcs_uri: str
    received_at: datetime
    published_at: datetime | None
    processing_status: str

    @property
    def is_published(self) -> bool:
        """True when the prior ingest completed its publish (full no-op on redelivery)."""
        return self.published_at is not None or self.processing_status == _STATUS_PUBLISHED


@dataclass(frozen=True)
class BronzeRow:
    """One metadata-only bronze row (id minted by the caller via dis-core ids)."""

    id: UUID
    tenant_id: UUID
    store_id: UUID
    source_id: str
    trace_id: UUID
    gcs_uri: str
    payload_size_bytes: int
    payload_sha256: str
    row_count: int | None
    source_payload_id: str
    # Replay lineage (Slice 8 / D71): persisted from the event; informational like
    # mapping_version_id. NULL only on pre-Slice-8 rows — and deliberately NOT read
    # back for the resume-and-mark re-publish (the envelope takes it off the
    # incoming event), so a NULL prior row can never wedge the publish.
    template_id: UUID
    received_at: datetime
    processing_status: ProcessingStatus


def _require_key_component(name: str, value: str, *, tenant_id: str, trace_id: str) -> None:
    if not value or not value.strip():
        raise EventContractError(
            f"idempotency key component {name!r} is missing or empty; the dedup key is a "
            "required value (no silent fallback)",
            field=name,
            tenant_id=tenant_id,
            trace_id=trace_id,
        )


async def find_prior(
    conn: AsyncConnection,
    *,
    upload_session_id: str,
    payload_sha256: str,
    tenant_id: str,
    trace_id: str,
) -> PriorIngest | None:
    """The dedup lookup: most recent same-key row within the window, or None.

    Tenant scoping is the RLS session's (``conn`` must come from ``rls_session``
    under the event's tenant). The window is measured against the prior row's
    ``received_at`` (server-side, monotonic), not the producer's event timestamp.
    """
    _require_key_component("upload_session_id", upload_session_id, tenant_id=tenant_id, trace_id=trace_id)
    _require_key_component("payload_sha256", payload_sha256, tenant_id=tenant_id, trace_id=trace_id)
    cutoff = now_utc() - timedelta(hours=DEDUP_WINDOW_HOURS)
    result = await conn.execute(
        text(
            "SELECT id, trace_id, store_id, source_id, gcs_uri, received_at, "
            "       published_at, processing_status "
            "FROM bronze.data_ingress_events "
            "WHERE source_payload_id = :spid "
            "  AND payload_sha256 = :sha "
            "  AND dis_channel = :channel "
            "  AND received_at >= :cutoff "
            "ORDER BY received_at DESC "
            "LIMIT 1"
        ),
        {
            "spid": upload_session_id,
            "sha": payload_sha256,
            "channel": DIS_CHANNEL,
            "cutoff": cutoff,
        },
    )
    row = result.one_or_none()
    if row is None:
        return None
    return PriorIngest(
        bronze_id=row.id,
        trace_id=row.trace_id,
        store_id=row.store_id,
        source_id=row.source_id,
        gcs_uri=row.gcs_uri,
        received_at=row.received_at,
        published_at=row.published_at,
        processing_status=row.processing_status,
    )


async def insert_row(conn: AsyncConnection, row: BronzeRow) -> None:
    """INSERT the one metadata-only bronze row (RLS WITH CHECK enforces the tenant)."""
    await conn.execute(
        text(
            "INSERT INTO bronze.data_ingress_events "
            "(id, tenant_id, store_id, source_id, dis_channel, trace_id, gcs_uri, "
            " payload_size_bytes, payload_sha256, row_count, content_type, "
            " source_payload_id, template_id, received_at, processing_status) "
            "VALUES "
            "(:id, :tenant_id, :store_id, :source_id, :dis_channel, :trace_id, :gcs_uri, "
            " :payload_size_bytes, :payload_sha256, :row_count, :content_type, "
            " :source_payload_id, :template_id, :received_at, :processing_status)"
        ),
        {
            "id": row.id,
            "tenant_id": row.tenant_id,
            "store_id": row.store_id,
            "source_id": row.source_id,
            "dis_channel": DIS_CHANNEL,
            "trace_id": row.trace_id,
            "gcs_uri": row.gcs_uri,
            "payload_size_bytes": row.payload_size_bytes,
            "payload_sha256": row.payload_sha256,
            "row_count": row.row_count,
            "content_type": CONTENT_TYPE,
            "source_payload_id": row.source_payload_id,
            "template_id": row.template_id,
            "received_at": row.received_at,
            "processing_status": row.processing_status,
        },
    )


async def mark_published(conn: AsyncConnection, *, bronze_id: UUID, published_at: datetime) -> None:
    """Stamp the publish: ``published_at`` + status PUBLISHED (resume-and-mark, D59)."""
    await conn.execute(
        text(
            "UPDATE bronze.data_ingress_events "
            "SET published_at = :published_at, processing_status = :status "
            "WHERE id = :id"
        ),
        {"published_at": published_at, "status": _STATUS_PUBLISHED, "id": bronze_id},
    )
