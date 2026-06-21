"""Inert Phase-3 BigQuery audit writer seam.

Mirrors the Slice 3 ``BqClient`` stub and Slice 4 ``dis-pii`` seam discipline: import-safe,
constructs with no BigQuery / network / DB contact, and **does not import**
``google-cloud-bigquery``. Its only job is to exist behind the stable :class:`AuditWriter`
interface (``decisions.md`` D34, hard rule 8) and mark where the real archive writer lands
in Phase 3 (build-guide Slice 21). No method body is fleshed out.
"""

from __future__ import annotations

from dis_audit.event import AuditEvent
from dis_core.bq import BqClient


class BigQueryAuditWriter:
    """Phase-3 BigQuery audit writer — inert seam. Performs no I/O.

    Construction stores the (optional) ``BqClient`` seam only; it opens no connection and
    contacts no Google API. ``write`` is the unimplemented Phase-3 point.
    """

    def __init__(self, bq_client: BqClient | None = None) -> None:
        self.bq_client = bq_client

    async def write(self, event: AuditEvent) -> bool:
        raise NotImplementedError(
            "BigQueryAuditWriter is a Phase-1 placeholder seam; the BigQuery audit archive "
            "lands in Phase 3 / Slice 21 (see decisions.md D34). Phase 1 writes audit to "
            "Cloud SQL via PostgresAuditWriter."
        )
