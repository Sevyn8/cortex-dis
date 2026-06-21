"""Fixtures for the Slice 10 consumer integration tests.

These tests WRITE the DIS database and use the Pub/Sub + GCS emulators, so — the
Slice 4/7 lesson — they must NOT skip silently when the stack is absent: a missing
env or unreachable emulator is a loud ERROR (``StackRequiredError``), never a skip.
Everything runs against ``ithina_dis_db`` on 5433; Customer Master (5432) is never
touched.

Date robustness (M-D38/D64 gate finding, RESOLVED by migration 0009): the event
tables are PLAIN for beta (D77's scope clause revised — partitioning returns at
Slice 21 with automation), so any event date lands and the suite needs no
partition provisioning. The former ``event_partitions`` fixture (which created
the daily partitions the tests' dates needed) is retired with the partitions.

Each test mints a unique trace and uses per-test SKUs/transaction ids so runs
cannot couple; created canonical/audit/bronze rows are deleted in teardown via
the admin engine.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url

from dis_core.ids import new_uuid7
from dis_storage import build_object_path

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from dis_storage.client import StorageClient
    from streaming_consumer.envelope import IngressReadyEvent
    from streaming_consumer.orchestrate import ConsumerPipeline


class StackRequiredError(RuntimeError):
    """The local stack is required for these load-bearing tests but is absent."""


_REQUIRED_ENV = (
    "POSTGRES_URL",
    "POSTGRES_ADMIN_URL",
    "PUBSUB_EMULATOR_HOST",
    "STORAGE_EMULATOR_HOST",
    "GCS_BUCKET_BRONZE",
)

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

# The consumer-test source registrations (distinct from the seeder's
# manual_csv_upload so its empty mapping_rules row is never read here).
SALE_SOURCE_ID = "sc_pos_v1"
CHANGE_SOURCE_ID = "sc_inv_v1"
BAD_SUBTYPE_SOURCE_ID = "sc_pos_badsub_v1"
CATALOGUE_SOURCE_ID = "sc_cat_v1"
# A snapshot that maps NEITHER product_category NOR unit_cost (Slice 16j): both are
# nullable now, so this still classifies COMPLETE and lands a hot row with both NULL.
CATALOGUE_MINIMAL_SOURCE_ID = "sc_cat_min_v1"

_MAPPING_FILES = {
    SALE_SOURCE_ID: "sale_pos_v1.json",
    CHANGE_SOURCE_ID: "inventory_count_v1.json",
    BAD_SUBTYPE_SOURCE_ID: "sale_pos_bad_subtype_v1.json",
    CATALOGUE_SOURCE_ID: "catalogue_snapshot_v1.json",
    CATALOGUE_MINIMAL_SOURCE_ID: "catalogue_snapshot_minimal_v1.json",
}

# The stored template_type per consumer-test source (Slice 14d): the consumer
# routes by this column. Backfill set the same values for the pre-existing rows.
_TEMPLATE_TYPES = {
    SALE_SOURCE_ID: "sales",
    CHANGE_SOURCE_ID: "inventory_change",
    BAD_SUBTYPE_SOURCE_ID: "sales",
    CATALOGUE_SOURCE_ID: "snapshot",
    CATALOGUE_MINIMAL_SOURCE_ID: "snapshot",
}

# Pinned per-source template ids (Slice 14a grain): the rekeyed
# uq_csm_seq_per_source conflict target includes template_id, so the upsert
# only lands on the same row across runs when the id is deterministic. Used
# only when the (tenant, source) carries no 'default' template yet — a DB that
# predates 14a was BACKFILLED with minted ids, and inserting a different id
# under the same name would (correctly) trip ex_csm_template_name_per_source.
_TEMPLATE_IDS = {
    SALE_SOURCE_ID: UUID("019e97d0-0000-7000-8000-0000000000a1"),
    CHANGE_SOURCE_ID: UUID("019e97d0-0000-7000-8000-0000000000a2"),
    BAD_SUBTYPE_SOURCE_ID: UUID("019e97d0-0000-7000-8000-0000000000a3"),
    CATALOGUE_SOURCE_ID: UUID("019e97d0-0000-7000-8000-0000000000a4"),
    CATALOGUE_MINIMAL_SOURCE_ID: UUID("019e97d0-0000-7000-8000-0000000000a5"),
}

# All test event timestamps anchor here: today at a mid-day hour, so a ±1-day
# spread stays deterministic across the suite (the event tables are plain —
# migration 0009 — so no partition window constrains the dates).
BASE_TS = datetime.now(tz=UTC).replace(hour=12, minute=0, second=0, microsecond=0)


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise StackRequiredError(
            f"{name} is not set — the Slice 10 consumer integration tests refuse to skip "
            "silently. Bring up the stack (make run-local) and load .env."
        )
    return value


@pytest.fixture(scope="session")
def stack_env() -> dict[str, str]:
    return {name: _require_env(name) for name in _REQUIRED_ENV}


@pytest.fixture(scope="session")
def seeded(stack_env: dict[str, str]) -> None:
    """Sync identity_mirror (mirror-sync owns it) then seed the default mapping.

    identity_mirror is owned by mirror-sync; the seeder only writes
    config.source_mappings and requires the tenant FK target to already exist,
    so the sync MUST run first.
    """
    from dis_testing.identity_sync import sync_identity_mirror
    from dis_testing.seed import seed_default_fixtures

    sync_identity_mirror(stack_env["POSTGRES_ADMIN_URL"], stack_env["POSTGRES_URL"])
    seed_default_fixtures(url=stack_env["POSTGRES_URL"])


@pytest.fixture(scope="session")
def admin_engine_session(stack_env: dict[str, str]) -> Iterator[Engine]:
    """Session-scoped admin engine (mapping provisioning, cleanup)."""
    url = make_url(stack_env["POSTGRES_ADMIN_URL"])
    assert url.database == "ithina_dis_db"  # target safety for the fixture itself
    assert url.port == 5433
    eng = create_engine(stack_env["POSTGRES_ADMIN_URL"])
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture(scope="session")
def consumer_mappings(admin_engine_session: Engine, seeded: None) -> Iterator[dict[str, int]]:
    """Seed the consumer-test ACTIVE mappings; returns source_id -> version id.

    Test-only upsert by the (tenant, source, seq) natural key so a rules edit in
    the fixture file lands on re-run (live mapping rows are immutable in
    production; this is dev-DB seeding, the admin path).
    """
    from dis_testing.fixtures import PRIMARY_TENANT

    versions: dict[str, int] = {}
    with admin_engine_session.begin() as conn:
        for source_id, filename in _MAPPING_FILES.items():
            rules = json.loads((_FIXTURES / "mappings" / filename).read_text())
            # Adopt the EXISTING 'default' template id when the source already
            # carries one (e.g. the 0005 backfill minted it); pin only on a
            # virgin (tenant, source). Keeps the upsert landing on one row.
            existing = conn.execute(
                text(
                    "SELECT template_id FROM config.source_mappings "
                    "WHERE tenant_id = CAST(:tenant_id AS uuid) AND source_id = :source_id "
                    "AND template_name = 'default' AND status <> 'DEPRECATED' LIMIT 1"
                ),
                {"tenant_id": str(PRIMARY_TENANT.uuid), "source_id": source_id},
            ).scalar()
            template_id = str(existing) if existing else str(_TEMPLATE_IDS[source_id])
            row = conn.execute(
                text(
                    "INSERT INTO config.source_mappings "
                    "(tenant_id, source_id, template_id, template_name, template_type, "
                    "version_seq_per_source, status, mapping_rules, activated_at) "
                    "VALUES (CAST(:tenant_id AS uuid), :source_id, "
                    "CAST(:template_id AS uuid), 'default', :template_type, 1, 'ACTIVE', "
                    "CAST(:rules AS JSONB), NOW()) "
                    "ON CONFLICT (tenant_id, source_id, template_id, version_seq_per_source) "
                    "DO UPDATE SET mapping_rules = EXCLUDED.mapping_rules, "
                    "template_type = EXCLUDED.template_type "
                    "RETURNING mapping_version_id"
                ),
                {
                    "tenant_id": str(PRIMARY_TENANT.uuid),
                    "source_id": source_id,
                    "template_id": template_id,
                    "template_type": _TEMPLATE_TYPES[source_id],
                    "rules": json.dumps(rules),
                },
            ).first()
            assert row is not None
            versions[source_id] = int(row.mapping_version_id)
    try:
        yield versions
    finally:
        # Revert the seeded consumer-test mappings at session end so the suite leaves
        # config.source_mappings at its reset baseline — the D100 post-suite clean-state
        # guard asserts no non-baseline mapping survives. Admin engine bypasses RLS.
        with admin_engine_session.begin() as conn:
            conn.execute(
                text("DELETE FROM config.source_mappings WHERE source_id = ANY(:sids)"),
                {"sids": list(_MAPPING_FILES)},
            )


@pytest.fixture
async def engine(stack_env: dict[str, str], seeded: None) -> AsyncIterator[AsyncEngine]:
    """The consumer's RLS engine (loop-scoped per test, the Slice 6 pattern)."""
    from dis_rls import create_rls_engine

    eng = create_rls_engine(stack_env["POSTGRES_URL"])
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.fixture
def dis_admin(admin_engine_session: Engine) -> Engine:
    """Per-test alias for the admin engine (independent re-reads, cleanup)."""
    return admin_engine_session


@dataclass
class Cleanup:
    """Trace ids + SKUs to scrub in teardown (canonical + audit + bronze + quarantine)."""

    traces: list[UUID] = field(default_factory=list)
    skus: list[str] = field(default_factory=list)


@pytest.fixture
def cleanup(dis_admin: Engine) -> Iterator[Cleanup]:
    items = Cleanup()
    yield items
    if not (items.traces or items.skus):
        return
    with dis_admin.begin() as conn:
        if items.traces:
            params = {"tids": items.traces}
            conn.execute(
                text("DELETE FROM canonical.store_sku_sale_events WHERE trace_id = ANY(:tids)"),
                params,
            )
            conn.execute(
                text("DELETE FROM canonical.store_sku_change_events WHERE trace_id = ANY(:tids)"),
                params,
            )
            conn.execute(text("DELETE FROM audit.events WHERE trace_id = ANY(:tids)"), params)
            conn.execute(
                text("DELETE FROM quarantine.quarantined_rows WHERE trace_id = ANY(:tids)"),
                params,
            )
            conn.execute(
                text("DELETE FROM quarantine.quarantined_chunks WHERE trace_id = ANY(:tids)"),
                params,
            )
            conn.execute(
                text("DELETE FROM bronze.data_ingress_events WHERE trace_id = ANY(:tids)"),
                params,
            )
        if items.skus:
            conn.execute(
                text("DELETE FROM canonical.store_sku_current_position WHERE sku_id = ANY(:skus)"),
                {"skus": items.skus},
            )


@pytest.fixture
def storage(stack_env: dict[str, str]) -> StorageClient:
    """The dis-storage client on the bronze bucket (created idempotently)."""
    from google.api_core.exceptions import Conflict

    from dis_storage.client import StorageClient

    bucket = stack_env["GCS_BUCKET_BRONZE"]
    try:
        client = StorageClient(bucket=bucket)
        try:
            client._client.create_bucket(bucket)  # noqa: SLF001 - test provisioning
        except Conflict:
            pass
    except Exception as exc:
        raise StackRequiredError(
            f"GCS emulator unreachable ({exc!r}); refusing to skip. make run-local."
        ) from exc
    return client


@pytest.fixture
def pipeline(
    engine: AsyncEngine,
    storage: StorageClient,
    stack_env: dict[str, str],
    consumer_mappings: dict[str, int],
) -> ConsumerPipeline:
    """A fully wired consumer pipeline against the live stack."""
    from dis_audit import AuditBackend, select_writer
    from dis_quarantine import PostgresQuarantineWriter
    from streaming_consumer.orchestrate import ConsumerPipeline
    from streaming_consumer.sinks.audit import ConsumerAudit
    from streaming_consumer.sinks.quarantine import ConsumerQuarantine

    return ConsumerPipeline(
        engine=engine,
        storage=storage,
        audit=ConsumerAudit(select_writer(AuditBackend.POSTGRES, engine=engine)),
        quarantine=ConsumerQuarantine(PostgresQuarantineWriter(engine)),
        bronze_bucket=stack_env["GCS_BUCKET_BRONZE"],
    )


@dataclass(frozen=True)
class SeededChunk:
    """One seeded ingress: GCS object + bronze row + the typed envelope."""

    event: IngressReadyEvent
    bronze_ref: UUID
    trace_id: UUID


def seed_chunk(
    dis_admin: Engine,
    storage: StorageClient,
    cleanup: Cleanup,
    *,
    csv_data: bytes,
    source_id: str,
    bronze_bucket: str,
    store_uuid: UUID | None = None,
    tenant_uuid: UUID | None = None,
    event_store_uuid: UUID | None = None,
    template_id: UUID | None = None,
) -> SeededChunk:
    """Play the 9b worker: land the object + bronze row, return the envelope.

    ``event_store_uuid`` overrides the ENVELOPE's store only (the bronze row
    keeps a mirror-valid store — bronze carries its own composite store FK):
    the malformed-producer construction the canonical no-orphan FK (D39) is the
    last line of defense against.

    ``template_id`` overrides the envelope's template (the unknown-template
    negative path, or a second-template chunk). When omitted, the source's real
    ACTIVE 'default' template is resolved from the DB — since Slice 8a the
    lookup is template-KEYED (D71), so the happy path must name the template
    the ``consumer_mappings`` fixture actually seeded; a source with none (a
    negative-path tenant/source) gets a minted id, immaterial because such a
    chunk fails before or at the mapping load anyway.
    """
    from dis_testing.fixtures import PRIMARY_STORE, PRIMARY_TENANT
    from streaming_consumer.envelope import IngressReadyEvent

    tenant = tenant_uuid or PRIMARY_TENANT.uuid
    store = store_uuid or PRIMARY_STORE.uuid
    trace_id = new_uuid7()
    bronze_ref = new_uuid7()
    cleanup.traces.append(trace_id)

    if template_id is None:
        # The same adopt-existing select the consumer_mappings fixture uses:
        # the live 'default' template id is backfill-minted, not pinned.
        with dis_admin.connect() as conn:
            existing = conn.execute(
                text(
                    "SELECT template_id FROM config.source_mappings "
                    "WHERE tenant_id = CAST(:tenant_id AS uuid) AND source_id = :source_id "
                    "AND template_name = 'default' AND status = 'ACTIVE' LIMIT 1"
                ),
                {"tenant_id": str(tenant), "source_id": source_id},
            ).scalar()
        template_id = UUID(str(existing)) if existing else new_uuid7()

    object_key = build_object_path(
        tenant_id=tenant,
        source_id=source_id,
        trace_id=trace_id,
        event_ts=BASE_TS,
        ext="csv",
    )
    storage.upload_bytes(object_key, csv_data, content_type="text/csv")
    gcs_uri = f"gs://{bronze_bucket}/{object_key}"

    with dis_admin.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO bronze.data_ingress_events "
                "(id, tenant_id, store_id, source_id, dis_channel, trace_id, gcs_uri, "
                " payload_size_bytes, received_at, processing_status, published_at) "
                "VALUES (CAST(:id AS uuid), CAST(:tenant_id AS uuid), CAST(:store_id AS uuid), "
                " :source_id, 'csv_upload', CAST(:trace_id AS uuid), :gcs_uri, "
                " :size, NOW(), 'PUBLISHED', NOW())"
            ),
            {
                "id": str(bronze_ref),
                "tenant_id": str(tenant),
                "store_id": str(store),
                "source_id": source_id,
                "trace_id": str(trace_id),
                "gcs_uri": gcs_uri,
                "size": len(csv_data),
            },
        )

    event = IngressReadyEvent(
        schema_version=1,
        trace_id=trace_id,
        tenant_id=tenant,
        store_id=event_store_uuid or store,
        source_id=source_id,
        # Consumed since Slice 8a (D71): keys the active-mapping lookup —
        # resolved/overridden above.
        template_id=template_id,
        bronze_ref=bronze_ref,
        gcs_uri=gcs_uri,
        received_ts=BASE_TS,
        tenant_display_code="buc-ees",
        store_code="TX-101",
    )
    return SeededChunk(event=event, bronze_ref=bronze_ref, trace_id=trace_id)


def seed_hot_row(
    dis_admin: Engine,
    cleanup: Cleanup,
    *,
    sku_id: str,
    mapping_version_id: int,
    sku_variant: str | None = None,
    sku_lot_batch: str | None = None,
) -> UUID:
    """Pre-seed the hot row a sale chunk merges into (D63: catalogue-before-sales)."""
    from dis_testing.fixtures import PRIMARY_STORE, PRIMARY_TENANT

    row_id = new_uuid7()
    cleanup.skus.append(sku_id)
    with dis_admin.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO canonical.store_sku_current_position "
                "(id, tenant_id, store_id, sku_id, sku_variant, sku_lot_batch, "
                " product_name, product_category, current_retail_price, unit_cost, "
                " tax_treatment, currency, mapping_version_id, trace_id, dis_channel) "
                "VALUES (CAST(:id AS uuid), CAST(:tenant_id AS uuid), CAST(:store_id AS uuid), "
                " :sku_id, :sku_variant, :sku_lot_batch, "
                " 'Seeded Widget', 'Hardware', 1.0000, 0.5000, "
                " 'EXCLUSIVE', 'USD', :mapping_version_id, CAST(:trace_id AS uuid), 'csv_upload')"
            ),
            {
                "id": str(row_id),
                "tenant_id": str(PRIMARY_TENANT.uuid),
                "store_id": str(PRIMARY_STORE.uuid),
                "sku_id": sku_id,
                "sku_variant": sku_variant,
                "sku_lot_batch": sku_lot_batch,
                "mapping_version_id": mapping_version_id,
                "trace_id": str(new_uuid7()),
            },
        )
    return row_id


def sale_csv(rows: list[tuple[str, str, str, str, str, str, str]]) -> bytes:
    """sold_at, sku, qty, retail, price, txn, line."""
    body = "\n".join(",".join(row) for row in rows)
    return f"sold_at,sku,qty,retail,price,txn,line\n{body}\n".encode()


def change_csv(rows: list[tuple[str, str, str]]) -> bytes:
    """counted_at, sku, stock."""
    body = "\n".join(",".join(row) for row in rows)
    return f"counted_at,sku,stock\n{body}\n".encode()


def catalogue_csv(rows: list[tuple[str, str, str, str, str, str]]) -> bytes:
    """code, name, category, price, cost, qty (the snapshot/catalogue shape, Slice 14d)."""
    body = "\n".join(",".join(row) for row in rows)
    return f"code,name,category,price,cost,qty\n{body}\n".encode()


def catalogue_minimal_csv(rows: list[tuple[str, str, str, str]]) -> bytes:
    """code, name, price, qty — the Slice 16j minimal snapshot: NO category, NO cost,
    so the landed hot row carries product_category and unit_cost as NULL."""
    body = "\n".join(",".join(row) for row in rows)
    return f"code,name,price,qty\n{body}\n".encode()


def ts(offset_minutes: int = 0, *, day_offset: int = 0) -> str:
    """A chunk timestamp string anchored at BASE_TS."""
    moment = BASE_TS + timedelta(days=day_offset, minutes=offset_minutes)
    return f"{moment:%Y-%m-%d %H:%M:%S}"


def event_date_of(day_offset: int = 0) -> date:
    return (BASE_TS + timedelta(days=day_offset)).date()


def drain_subscription(project_id: str) -> int:
    """Pull-and-ACK everything currently on the consumer's real subscription.

    Test hygiene only: non-allowlisted failures still nack-and-redeliver (the
    11a posture quarantines only the deterministic allowlist), and OTHER suites
    (the worker) publish ingress.ready messages nobody consumes — without a
    drain either poisons later subscriber-level tests on the shared
    subscription. Subscriber-level assertions must additionally be PER-TRACE
    (held/audit counts for the test's own trace), never poll-count-based.
    """
    from google.cloud import pubsub_v1

    from streaming_consumer.config import INGRESS_READY_SUBSCRIPTION

    client = pubsub_v1.SubscriberClient()
    sub_path = client.subscription_path(project_id, INGRESS_READY_SUBSCRIPTION)
    drained = 0
    try:
        while True:
            response = client.pull(
                request={"subscription": sub_path, "max_messages": 50}, timeout=3, retry=None
            )
            if not response.received_messages:
                return drained
            client.acknowledge(
                request={
                    "subscription": sub_path,
                    "ack_ids": [m.ack_id for m in response.received_messages],
                }
            )
            drained += len(response.received_messages)
    except Exception:  # noqa: BLE001 - empty-subscription pull timeouts are fine
        return drained
    finally:
        client.close()
