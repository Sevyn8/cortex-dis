"""The schemas/postgres/*.sql RLS policy text honors the two-GUC end-state (Slice 17b).

Closes adversarial-pass escape #1: the DDL files are what migration 0001's manifest applies
on a fresh build, but migration 0011 then DROP+CREATEs every policy at head from its OWN
inline text — so no head-level test (incl. test_migration_0011's catalog equality) verifies
the DDL files themselves. A drifted/wrong DDL policy is masked at head and would only bite if
0011 is ever squashed and the DDL becomes the fresh-build source of truth again.

This is a PURE (no-DB) source-text contract: each of the 13 DDL files' CREATE POLICY block
must carry the asymmetric two-GUC form independently of 0011 — PLATFORM widens USING only;
WITH CHECK stays NULLIF-tenant-pinned with no PLATFORM branch; audit.events is the USING-only
outlier with its tenant-less branch.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_DDL_ROOT = Path(__file__).resolve().parents[2] / "schemas" / "postgres"
_PLATFORM = "current_setting('app.user_type', true) = 'PLATFORM'"
_NULLIF_TENANT = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"

# The 12 tables carrying the symmetric `tenant_isolation` policy (file under schemas/postgres).
_TENANT_FILES = [
    "bronze/data_ingress_events.sql",
    "canonical/store_sku_change_events.sql",
    "canonical/store_sku_current_position.sql",
    "canonical/store_sku_sale_events.sql",
    "canonical/store_sku_signal_history.sql",
    "config/source_mappings.sql",
    "quarantine/quarantined_chunks.sql",
    "quarantine/quarantined_rows.sql",
    "staging/store_sku_change_events.sql",
    "staging/store_sku_current_position.sql",
    "staging/store_sku_sale_events.sql",
    "staging/store_sku_signal_history.sql",
]
_AUDIT_FILE = "audit/events.sql"


def _policy_block(rel: str) -> str:
    """The single ``CREATE POLICY ... ;`` block from a DDL file (excludes any preceding
    comment that might mention WITH CHECK, e.g. config/source_mappings.sql)."""
    text = (_DDL_ROOT / rel).read_text()
    start = text.index("CREATE POLICY")
    end = text.index(";", start)
    return text[start : end + 1]


def _split_using_with_check(block: str) -> tuple[str, str | None]:
    using_at = block.index("USING")
    wc_at = block.find("WITH CHECK")
    if wc_at == -1:
        return block[using_at:], None
    return block[using_at:wc_at], block[wc_at:]


@pytest.mark.parametrize("rel", _TENANT_FILES)
def test_tenant_isolation_ddl_carries_the_two_guc_end_state(rel: str) -> None:
    using, with_check = _split_using_with_check(_policy_block(rel))
    # USING widens to PLATFORM (see-all) and NULLIF-wraps the tenant comparison.
    assert _PLATFORM in using, f"{rel}: USING lacks the PLATFORM see-all branch"
    assert _NULLIF_TENANT in using, f"{rel}: USING lacks the NULLIF tenant comparison"
    # WITH CHECK stays tenant-pinned (NULLIF) and NEVER widens to PLATFORM (write-nothing).
    assert with_check is not None, f"{rel}: policy has no WITH CHECK — the tenant write-pin is gone"
    assert _NULLIF_TENANT in with_check, f"{rel}: WITH CHECK lacks the NULLIF tenant pin"
    assert _PLATFORM not in with_check, (
        f"{rel}: WITH CHECK carries a PLATFORM branch — cross-tenant writes are open"
    )


def test_audit_events_ddl_is_the_using_only_outlier() -> None:
    using, with_check = _split_using_with_check(_policy_block(_AUDIT_FILE))
    assert with_check is None, "audit.events DDL grew a WITH CHECK — the outlier shape changed"
    assert _PLATFORM in using, "audit.events DDL USING lacks the PLATFORM branch"
    assert "tenant_id IS NULL" in using, "audit.events DDL USING lost its tenant-less branch"
    assert _NULLIF_TENANT in using, "audit.events DDL USING lacks the NULLIF tenant comparison"
