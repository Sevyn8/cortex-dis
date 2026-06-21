"""BqClient — Phase-1 stub. The single seam for BigQuery access in DIS.

Architecture / CLAUDE.md hard rule 8: all BigQuery access goes through this
client, which auto-injects ``WHERE tenant_id = :tenant_id`` on every query; direct
``google-cloud-bigquery`` import is forbidden in services.

In Phase 1 there is no BigQuery (audit and canonical live in Cloud SQL; see
``decisions.md`` D34). This stub is deliberately inert: it constructs without any
network or credential I/O and does **not** import ``google-cloud-bigquery``. Its
only job is to exist as the import seam so consumers can depend on the name now;
the real implementation lands in Phase 3 (build-guide Slice 21), replacing this
module without changing the import path.

No method body is fleshed out — the query seam raises ``NotImplementedError`` so
any accidental Phase-1 call fails loudly instead of silently no-op'ing.
"""

from __future__ import annotations

from typing import Any

from dis_core.errors import DisError
from dis_core.identifiers import TenantId


class BqNotAvailableError(DisError):
    """BigQuery is not available in Phase 1; the real BqClient lands in Phase 3 (Slice 21)."""


class BqClient:
    """Tenant-scoped BigQuery client seam. Phase-1 stub — performs no I/O.

    Construction stores configuration only; it opens no connection and contacts no
    Google API. The real Phase-3 client replaces this class behind the same name.
    """

    def __init__(self, *, project: str | None = None, dataset: str | None = None) -> None:
        self.project = project
        self.dataset = dataset

    def query(self, sql: str, *, tenant_id: TenantId, params: dict[str, Any] | None = None) -> Any:
        """Run a tenant-scoped query. Phase-1 stub: raises.

        The Phase-3 implementation auto-injects ``WHERE tenant_id = :tenant_id``
        (hard rule 8); ``tenant_id`` is required here so the seam already reflects
        that contract.
        """
        raise NotImplementedError(
            "BqClient is a Phase-1 stub (no BigQuery until Phase 3 / Slice 21); see decisions.md D34"
        )
