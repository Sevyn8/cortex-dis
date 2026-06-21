"""Bronze fetch: cross-check the pointer, read the bronze row, download the chunk.

The event is the trust boundary (D54): identity and ``trace_id`` are READ off it,
never re-resolved. The cross-checks here are consistency checks against that
boundary, not re-resolution:

- the ``gcs_uri`` bucket must be the configured bronze bucket, and the object path
  must parse via ``dis-storage`` ``parse_object_path`` (hard rule 9, D53) with the
  path's tenant/source segments matching the event's;
- the bronze row named by ``bronze_ref`` must exist (bronze is the recoverable
  source, D5) and agree with the event on ``trace_id``, ``source_id`` and
  ``gcs_uri`` — a disagreement is a malformed producer (loud), never silently
  reconciled.

The bronze read runs under ``rls_session`` with the event's tenant (hard rules 1
and 12) — RLS scoping doubles as the tenant cross-check: another tenant's
``bronze_ref`` reads as absent.

The chunk parses to an all-string Polars frame (``infer_schema=False``): the
mapping engine's normalize sub-stage owns string→canonical conversion (D20);
nothing is type-guessed here. The chunk arrives ALREADY tokenized (D24) — no
``dis-pii`` dependency, and the frame is never logged.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

import polars as pl
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from dis_core.errors import EventContractError, EventPathMismatchError
from dis_rls import rls_session
from dis_storage import parse_object_path, split_object_uri
from streaming_consumer.envelope import IngressReadyEvent


class ObjectStore(Protocol):
    """The read seam over dis-storage's client (tests inject a fake)."""

    def download_bytes(self, object_path: str) -> bytes: ...


@dataclass(frozen=True)
class BronzeMeta:
    """The bronze row the event points at (the consumer's recoverable source, D5)."""

    bronze_id: UUID
    source_id: str
    dis_channel: str
    gcs_uri: str
    received_at: datetime
    row_count: int | None


@dataclass(frozen=True)
class FetchedChunk:
    """One fetched, parsed chunk plus its bronze provenance."""

    frame: pl.DataFrame
    bronze: BronzeMeta


def cross_check_path(event: IngressReadyEvent, *, bronze_bucket: str) -> str:
    """Verify the event's ``gcs_uri`` against the bucket, path scheme, and identity.

    Returns the object key for the download. Raises ``EventPathMismatchError`` on
    any disagreement (terminal-shaped, but Slice 10's minimal disposition still
    nacks failures after the FAILURE audit; see the service CLAUDE.md).
    """
    bucket, object_key = split_object_uri(event.gcs_uri)
    if bucket != bronze_bucket:
        raise EventPathMismatchError(
            f"gcs_uri bucket {bucket!r} is not the configured bronze bucket {bronze_bucket!r}",
            tenant_id=str(event.tenant_id),
            trace_id=str(event.trace_id),
        )
    parsed = parse_object_path(object_key)
    if str(parsed.tenant_id) != str(event.tenant_id) or parsed.source_id != event.source_id:
        raise EventPathMismatchError(
            "gcs_uri path identity disagrees with the event "
            f"(path tenant={parsed.tenant_id}, source={parsed.source_id!r}; "
            f"event source={event.source_id!r})",
            tenant_id=str(event.tenant_id),
            trace_id=str(event.trace_id),
        )
    return object_key


async def read_bronze_row(engine: AsyncEngine, event: IngressReadyEvent) -> BronzeMeta:
    """Read the bronze row named by ``bronze_ref`` under the event's tenant (RLS).

    Absent (including: belongs to another tenant, RLS-invisible) or disagreeing
    rows raise ``EventContractError`` — the producer published a pointer the
    bronze record does not back.
    """
    async with rls_session(engine, event.tenant_id) as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT id, source_id, dis_channel, gcs_uri, received_at, row_count "
                    "FROM bronze.data_ingress_events WHERE id = CAST(:bronze_ref AS uuid) "
                    "AND trace_id = CAST(:trace_id AS uuid)"
                ),
                {"bronze_ref": str(event.bronze_ref), "trace_id": str(event.trace_id)},
            )
        ).first()
    if row is None:
        raise EventContractError(
            f"bronze_ref {event.bronze_ref} has no bronze row for this tenant/trace "
            "(absent, foreign-tenant, or trace mismatch)",
            field="bronze_ref",
            tenant_id=str(event.tenant_id),
            trace_id=str(event.trace_id),
        )
    if row.source_id != event.source_id or row.gcs_uri != event.gcs_uri:
        raise EventContractError(
            "bronze row disagrees with the event "
            f"(bronze source_id={row.source_id!r}, event source_id={event.source_id!r})",
            field="bronze_ref",
            tenant_id=str(event.tenant_id),
            trace_id=str(event.trace_id),
        )
    return BronzeMeta(
        bronze_id=UUID(str(row.id)),
        source_id=str(row.source_id),
        dis_channel=str(row.dis_channel),
        gcs_uri=str(row.gcs_uri),
        received_at=row.received_at,
        row_count=row.row_count,
    )


def parse_chunk(data: bytes, *, separator: str, tenant_id: str, trace_id: str) -> pl.DataFrame:
    """Parse the CSV bytes to an all-string frame; empty/unparseable raises loudly.

    ``separator`` is the delimiter the worker detected and carried on the envelope
    (Slice 16f) — no longer a hardcoded comma. Polars' default ``"`` quoting still
    applies, so a quoted field containing the separator stays one field (verified).
    """
    try:
        frame = pl.read_csv(io.BytesIO(data), separator=separator, infer_schema=False)
    except Exception as exc:
        raise EventContractError(
            f"bronze object is not parseable CSV: {type(exc).__name__}",
            tenant_id=tenant_id,
            trace_id=trace_id,
        ) from exc
    if frame.height == 0:
        raise EventContractError(
            "bronze object parsed to an empty chunk (zero data rows)",
            tenant_id=tenant_id,
            trace_id=trace_id,
        )
    return frame


async def fetch_chunk(
    engine: AsyncEngine,
    storage: ObjectStore,
    event: IngressReadyEvent,
    *,
    bronze_bucket: str,
    on_bronze: Callable[[BronzeMeta], None] | None = None,
) -> FetchedChunk:
    """The fetch stage: cross-check, bronze read, download, parse.

    ``on_bronze`` fires the moment the bronze row is read, BEFORE the download
    and parse: the caller's flow context learns ``bronze_id``/``dis_channel``
    even when a later step of this stage raises, so a download/parse failure is
    classifiable as POST-fetch (the Slice 11a known-columns guard keys on
    ``dis_channel``; without this, a deterministic unparseable/empty bronze
    object could never be held and nacked forever — the storm class).
    """
    object_key = cross_check_path(event, bronze_bucket=bronze_bucket)
    bronze = await read_bronze_row(engine, event)
    if on_bronze is not None:
        on_bronze(bronze)
    data = storage.download_bytes(object_key)
    frame = parse_chunk(
        data,
        separator=event.delimiter,
        tenant_id=str(event.tenant_id),
        trace_id=str(event.trace_id),
    )
    return FetchedChunk(frame=frame, bronze=bronze)


@dataclass(frozen=True)
class StoreFacts:
    """The store row's enrichment facts (slice-5b), read once and handed in.

    ``tax_treatment`` feeds the event-path injection (unchanged) AND the
    current-position enrichment (D98); ``currency`` is current-position enrichment
    (D95). Both are NOT NULL on ``identity_mirror.stores``.
    """

    tax_treatment: str
    currency: str


async def read_store_facts(engine: AsyncEngine, event: IngressReadyEvent) -> StoreFacts:
    """Read the store's enrichment facts from ``identity_mirror.stores`` (one round-trip).

    A data-need read (the consumer denormalizes onto sale/hot rows and enriches the
    current-position row per D95/D98), NOT an identity validation — existence
    enforcement at the write is the composite FK (D39); no Identity Service is called
    (D28, Slice 13). Widened from the former ``tax_treatment``-only read to also
    return ``currency`` (same key, single round-trip). The store-absent precondition
    (D96) is unchanged: a missing row fails loud here.
    """
    async with rls_session(engine, event.tenant_id) as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT tax_treatment, currency FROM identity_mirror.stores "
                    "WHERE tenant_id = CAST(:tenant_id AS uuid) AND store_id = CAST(:store_id AS uuid)"
                ),
                {"tenant_id": str(event.tenant_id), "store_id": str(event.store_id)},
            )
        ).first()
    if row is None:
        raise EventContractError(
            f"store {event.store_id} is not in identity_mirror for this tenant; "
            "the sale/current-position paths need its store facts and the write would "
            "violate the composite store FK (D39) anyway",
            field="store_id",
            tenant_id=str(event.tenant_id),
            trace_id=str(event.trace_id),
        )
    return StoreFacts(tax_treatment=str(row.tax_treatment), currency=str(row.currency))
