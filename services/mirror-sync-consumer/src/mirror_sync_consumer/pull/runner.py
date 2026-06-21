"""Run-to-completion entrypoint for DB-pull mode.

One invocation = one sync pass = exit. No resident loop, no subscription (DB-pull is the
finite counterpart to the deferred Pub/Sub listener). The exit status is meaningful so a
scheduler/trigger detects failure: a clean pass (including a legitimately empty Customer
Master) exits 0; a config error, a CM target/context failure, a CM-unreachable error, a DIS
target-guard trip, or a write failure each exit with a distinct non-zero code.

Audit is **log-only** this slice (no ``audit.events`` rows): run start, run end, and the
per-tenant counts are structured log lines bound with ``service`` / ``stage`` / ``trace_id``
(and ``tenant_id`` per tenant).
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from dis_core.errors import CustomerMasterReadError, MirrorSyncError, RlsContextError
from dis_core.logging import DisLoggerAdapter, configure_logging, get_logger
from dis_core.trace_id import bind_trace_id, new_trace_id
from dis_rls import create_rls_engine
from mirror_sync_consumer.config import MirrorSyncConfig
from mirror_sync_consumer.pull.reader import create_cm_engine, read_customer_master
from mirror_sync_consumer.sinks.postgres import SyncResult, upsert_identity

_SERVICE = "mirror-sync-consumer"
_STAGE = "mirror_sync"

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_CM_READ = 3  # wrong CM target or platform context did not take effect
EXIT_CM_UNREACHABLE = 4
EXIT_TARGET = 5  # DIS write target/role guard tripped (dis-rls RlsContextError)
EXIT_WRITE = 6


def _log_per_tenant(log: DisLoggerAdapter, result: SyncResult) -> None:
    for pt in result.per_tenant:
        log.bind(tenant_id=str(pt.tenant_id)).info(
            "tenant synced",
            extra={
                "tenants_inserted": pt.tenants.inserted,
                "tenants_updated": pt.tenants.updated,
                "tenants_unchanged": pt.tenants.unchanged,
                "stores_inserted": pt.stores.inserted,
                "stores_updated": pt.stores.updated,
                "stores_unchanged": pt.stores.unchanged,
            },
        )


async def _run() -> int:
    trace_id: UUID = new_trace_id()  # the sync is a pipeline origin (hard rule 4): mint here
    bind_trace_id(trace_id)
    log = get_logger(_SERVICE, stage=_STAGE, trace_id=str(trace_id))

    try:
        config = MirrorSyncConfig.from_env()
    except MirrorSyncError as exc:
        log.error("mirror sync config error: %s", exc)
        return EXIT_CONFIG

    cm_engine = create_cm_engine(config.cm_db_url)
    write_engine = create_rls_engine(config.dis_db_url)
    try:
        log.info("mirror sync run start (DB-pull mode)")

        try:
            tenants, stores = await read_customer_master(cm_engine, config, trace_id=trace_id)
        except CustomerMasterReadError as exc:
            log.error("customer master read failed (target/context): %s", exc)
            return EXIT_CM_READ
        except SQLAlchemyError as exc:
            log.error("customer master unreachable: %s", exc)
            return EXIT_CM_UNREACHABLE

        if not tenants:
            # Valid empty first-load: confirmed PLATFORM context (read succeeded) returned zero
            # tenants. Not a failure — log and exit clean, writing nothing.
            log.info(
                "customer master returned zero tenants under platform context; "
                "valid empty state, nothing to mirror"
            )
            return EXIT_OK

        try:
            result = await upsert_identity(write_engine, tenants, stores, trace_id=trace_id)
        except RlsContextError as exc:
            log.error("dis write target/role guard tripped: %s", exc)
            return EXIT_TARGET
        except SQLAlchemyError as exc:
            log.error("mirror write failed: %s", exc)
            return EXIT_WRITE

        _log_per_tenant(log, result)
        tenant_totals, store_totals = result.totals()
        log.info(
            "mirror sync run complete",
            extra={
                "tenants_seen": tenant_totals.seen,
                "tenants_inserted": tenant_totals.inserted,
                "tenants_updated": tenant_totals.updated,
                "tenants_unchanged": tenant_totals.unchanged,
                "stores_seen": store_totals.seen,
                "stores_inserted": store_totals.inserted,
                "stores_updated": store_totals.updated,
                "stores_unchanged": store_totals.unchanged,
                "stores_skipped_no_tenant": result.skipped_stores,
            },
        )
        if result.skipped_stores:
            log.error(
                "stores referenced a tenant Customer Master did not return; not mirrored",
                extra={"stores_skipped_no_tenant": result.skipped_stores},
            )
        return EXIT_OK
    finally:
        await cm_engine.dispose()
        await write_engine.dispose()


def main() -> int:
    """CLI / scheduler entrypoint. Returns a process exit code."""
    configure_logging()
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
