"""source_mappings: template grain + RLS ON (Slice 14a)

The mapping grain becomes ``(tenant_id, source_id, template_id)``: one source
may carry multiple named mapping templates (e.g. ``manual_csv_upload`` carrying
sales, inventory, pricing), each with its own version lineage. The table also
carries ``tenant_id`` per row yet was RLS-OFF behind a stale "holds
configuration, not tenant data" comment; it now gets the same single-GUC
``tenant_isolation`` policy as every other DIS tenant table. The two register
decisions (template grain; config RLS ON) receive operator-assigned D-numbers
at the commit gate.

Steps (upgrade):

1.  ``CREATE EXTENSION IF NOT EXISTS btree_gist`` — required by the EXCLUDE
    constraint below. If the target environment cannot create it the migration
    RAISES (operator confirm: no fallback to a weaker constraint).
2.  Add ``template_id uuid`` / ``template_name TEXT COLLATE "C"`` (nullable),
    backfill, then SET NOT NULL — the 3-step shape because the table has live
    rows (the 0003 empty-table single-step does not apply). Backfill mints ONE
    ``public.uuidv7()`` per ``(tenant_id, source_id)`` GROUP (rows of one
    source are version lineage of one template; per-row minting would break
    lineage and ``predecessor_version_id`` coherence) and labels every
    backfilled row ``'default'`` — deterministic, unique per source since each
    group gets exactly one template.
3.  Rekey ``uq_csm_seq_per_source`` to
    ``(tenant_id, source_id, template_id, version_seq_per_source)`` — required
    so two templates under one source can each start at seq 1 (derived
    necessity; the old key would collide).
4.  Rekey ``uq_csm_active_per_source`` to
    ``(tenant_id, source_id, template_id) WHERE status = 'ACTIVE'`` — exact
    live predicate form preserved, key columns widened.
5.  ``ex_csm_template_name_per_source`` EXCLUDE (btree_gist): a name maps to
    at most one template among non-DEPRECATED rows. A plain unique index
    cannot express this — ACTIVE v1 + STAGED v2 of the SAME template share a
    name legitimately (the documented shadow lifecycle), so only rows with
    DIFFERENT ``template_id`` may conflict. DEPRECATED frees a name for reuse.
6.  ``CREATE OR REPLACE`` the ``set_csm_version_seq`` trigger function with the
    template-scoped MAX scan. Run unconditionally so the delta path and the
    manifest-bootstrapped fresh path converge on one body
    (``pg_get_functiondef``-compared by the migration test).
7.  ``ALTER VIEW config.source_mappings_v SET (security_invoker = true)`` —
    the view is owned by the admin role; owner-rights execution would silently
    bypass the new policy for every querying role. (Live sweep: this is the
    only view in the database and no SECURITY DEFINER function exists.)
8.  LAST: ``ENABLE`` + ``FORCE ROW LEVEL SECURITY`` + the single-GUC
    ``tenant_isolation`` policy, shape-matched to the live canonical policy.
    Last so the backfill never depends on the admin role's bypass posture
    (local admin is SUPERUSER; a cloud admin may not be BYPASSRLS).
9.  Comment updates: table / status / version_seq_per_source / new columns.
    ``version_seq_per_source`` keeps its (now slightly misnomered) name —
    renaming ripples into the contract's ``version`` mapping and the consumer
    for no enforcement gain; the comment carries the correction.

Idempotent for 0001-fresh-bootstrap parity (the updated schemas/postgres
manifest already carries the full end state): the column block is gated on
column existence, the rekeys on the live definition containing ``template_id``,
the EXCLUDE and policy on existence; extension/function/view/RLS statements are
natively idempotent.

Downgrade restores the pre-0005 shape but is CONDITIONAL: it pre-checks that
every ``(tenant, source)`` has at most one distinct template_id, at most one
ACTIVE row, and no duplicate ``(tenant, source, version_seq)`` — and raises
loudly otherwise (the old keys cannot hold over multi-template data). The
btree_gist extension is left installed (harmless, may be shared).

See: docs/slices/slice-14a-source-mappings-migration.md, decisions.md D6/D15/
D17/D22 (pin unchanged), D49 (mapping_rules untouched).

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-05

"""

from __future__ import annotations

import os

from alembic import op

# revision identifiers, used by Alembic.
revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

# Target-safety guard (Slice 1 §A.2 pattern, copied from 0001/0002/0003/0004).
_EXPECTED_DB = os.environ.get("POSTGRES_DB", "ithina_dis_db")
_CM_DB = "ithina_platform_db"

_TABLE = "config.source_mappings"
_VIEW = "config.source_mappings_v"
_SEQ_CONSTRAINT = "uq_csm_seq_per_source"
_ACTIVE_INDEX = "uq_csm_active_per_source"
_EXCLUDE_CONSTRAINT = "ex_csm_template_name_per_source"
_POLICY = "tenant_isolation"

# The trigger-function body, template-scoped. The schemas/postgres manifest
# carries the IDENTICAL body; the migration test compares pg_get_functiondef
# across the delta-path and fresh-bootstrap databases, so any drift fails loud.
# Not SECURITY DEFINER: it runs as the invoking role, so under RLS its MAX scan
# sees exactly the GUC tenant's rows — which IS NEW's tenant, because the
# policy WITH CHECK pins NEW.tenant_id to the GUC. With the GUC unset the scan
# sees zero rows and the INSERT itself then fails the WITH CHECK (fail-closed;
# the error reads as a row-level security violation, not "tenant unset").
_SET_VERSION_SEQ_FN = """\
CREATE OR REPLACE FUNCTION config.set_csm_version_seq()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.version_seq_per_source IS NULL OR NEW.version_seq_per_source = 0 THEN
        SELECT COALESCE(MAX(version_seq_per_source), 0) + 1
        INTO NEW.version_seq_per_source
        FROM config.source_mappings
        WHERE tenant_id = NEW.tenant_id
          AND source_id = NEW.source_id
          AND template_id = NEW.template_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

# Pre-0005 body, restored on downgrade (verbatim from the prior manifest).
_SET_VERSION_SEQ_FN_PRE_0005 = """\
CREATE OR REPLACE FUNCTION config.set_csm_version_seq()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.version_seq_per_source IS NULL OR NEW.version_seq_per_source = 0 THEN
        SELECT COALESCE(MAX(version_seq_per_source), 0) + 1
        INTO NEW.version_seq_per_source
        FROM config.source_mappings
        WHERE tenant_id = NEW.tenant_id
          AND source_id = NEW.source_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def check_migration_target(current: str, *, expected_db: str = _EXPECTED_DB, cm_db: str = _CM_DB) -> None:
    """Pure target check: refuse Customer Master outright, require the DIS database."""
    if current == cm_db:
        raise RuntimeError(
            f"Refusing to run DIS migration: connected to the Customer Master "
            f"database '{current}'. Point POSTGRES_ADMIN_URL at the DIS database."
        )
    if current != expected_db:
        raise RuntimeError(
            f"Refusing to run DIS migration: connected to '{current}' but expected "
            f"DIS database '{expected_db}' (POSTGRES_DB). Check POSTGRES_ADMIN_URL."
        )


def _guard_target() -> None:
    current = op.get_bind().exec_driver_sql("SELECT current_database()").scalar()
    check_migration_target(str(current))


def _exec(sql: str) -> None:
    """Raw DBAPI execution, bypassing text() bind parsing (``::`` casts in the
    RLS policy and the ``:=``-free plpgsql body would otherwise misparse)."""
    op.get_bind().exec_driver_sql(sql)


def _column_exists(column: str) -> bool:
    row = (
        op.get_bind()
        .exec_driver_sql(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'config' AND table_name = 'source_mappings' "
            "AND column_name = %s",
            (column,),
        )
        .scalar()
    )
    return row is not None


def _constraint_def(name: str) -> str | None:
    """pg_get_constraintdef for a constraint on config.source_mappings, or None."""
    return (
        op.get_bind()
        .exec_driver_sql(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'config.source_mappings'::regclass AND conname = %s",
            (name,),
        )
        .scalar()
    )


def _index_def(name: str) -> str | None:
    return (
        op.get_bind()
        .exec_driver_sql(
            "SELECT indexdef FROM pg_indexes "
            "WHERE schemaname = 'config' AND tablename = 'source_mappings' AND indexname = %s",
            (name,),
        )
        .scalar()
    )


def _policy_exists(name: str) -> bool:
    row = (
        op.get_bind()
        .exec_driver_sql(
            "SELECT 1 FROM pg_policies "
            "WHERE schemaname = 'config' AND tablename = 'source_mappings' AND policyname = %s",
            (name,),
        )
        .scalar()
    )
    return row is not None


def _scalar(sql: str) -> int:
    return int(op.get_bind().exec_driver_sql(sql).scalar() or 0)


def upgrade() -> None:
    _guard_target()

    # 1. Extension for the EXCLUDE constraint. Unguarded by design: if the
    #    target cannot create btree_gist this RAISES — never a silent fallback
    #    to a weaker name constraint (operator confirm).
    _exec("CREATE EXTENSION IF NOT EXISTS btree_gist")

    # 2. Columns + backfill + NOT NULL (delta path only; a fresh bootstrap
    #    already carries them NOT NULL from the manifest).
    if not _column_exists("template_id"):
        _exec(f"ALTER TABLE {_TABLE} ADD COLUMN template_id UUID")
        _exec(f'ALTER TABLE {_TABLE} ADD COLUMN template_name TEXT COLLATE "C"')
        # ONE template per (tenant, source) GROUP — rows of one source are the
        # version lineage of one template. The two-step CTE makes the volatile
        # uuidv7() unambiguously once-per-group.
        _exec(
            "WITH grouped AS ("
            "    SELECT DISTINCT tenant_id, source_id FROM config.source_mappings"
            "), keyed AS ("
            "    SELECT tenant_id, source_id, public.uuidv7() AS template_id FROM grouped"
            ") "
            "UPDATE config.source_mappings sm "
            "SET template_id = keyed.template_id, template_name = 'default' "
            "FROM keyed "
            "WHERE sm.tenant_id = keyed.tenant_id AND sm.source_id = keyed.source_id"
        )
        # Loud by construction: a row the backfill somehow missed fails here.
        _exec(f"ALTER TABLE {_TABLE} ALTER COLUMN template_id SET NOT NULL")
        _exec(f"ALTER TABLE {_TABLE} ALTER COLUMN template_name SET NOT NULL")

    # 3. Rekey the per-source sequence uniqueness to the template grain
    #    (two templates under one source each start at seq 1 — the old key
    #    would collide). Shape-checked, not name-checked: the fresh path
    #    carries the same constraint NAME already rekeyed.
    seq_def = _constraint_def(_SEQ_CONSTRAINT)
    if seq_def is None or "template_id" not in seq_def:
        _exec(f"ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {_SEQ_CONSTRAINT}")
        _exec(
            f"ALTER TABLE {_TABLE} ADD CONSTRAINT {_SEQ_CONSTRAINT} "
            "UNIQUE (tenant_id, source_id, template_id, version_seq_per_source)"
        )

    # 4. Rekey active-uniqueness: one ACTIVE per (tenant, source, template),
    #    exact live partial-predicate form preserved.
    idx_def = _index_def(_ACTIVE_INDEX)
    if idx_def is None or "template_id" not in idx_def:
        _exec(f"DROP INDEX IF EXISTS config.{_ACTIVE_INDEX}")
        _exec(
            f"CREATE UNIQUE INDEX {_ACTIVE_INDEX} ON {_TABLE} "
            "(tenant_id, source_id, template_id) WHERE status = 'ACTIVE'"
        )

    # 5. Name-to-template uniqueness among non-DEPRECATED rows. EXCLUDE, not a
    #    plain unique index: version rows of ONE template share a name
    #    legitimately (ACTIVE v1 + STAGED v2 shadow rollout), so only rows
    #    with a DIFFERENT template_id may conflict.
    if _constraint_def(_EXCLUDE_CONSTRAINT) is None:
        _exec(
            f"ALTER TABLE {_TABLE} ADD CONSTRAINT {_EXCLUDE_CONSTRAINT} "
            "EXCLUDE USING gist ("
            "tenant_id WITH =, source_id WITH =, template_name WITH =, template_id WITH <>"
            ") WHERE (status <> 'DEPRECATED')"
        )

    # 6. Template-scoped version sequencing (unconditional CREATE OR REPLACE —
    #    converges the delta and fresh paths on one body).
    _exec(_SET_VERSION_SEQ_FN)

    # 7. Invoker-rights view: owned by the admin role, so owner-rights
    #    execution would bypass the policy below for every querying role.
    _exec(f"ALTER VIEW {_VIEW} SET (security_invoker = true)")

    # 8. RLS LAST (backfill above never depends on the admin bypass posture).
    #    Single-GUC policy, shape-matched to the live canonical tenant tables.
    _exec(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    _exec(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    if not _policy_exists(_POLICY):
        _exec(
            f"CREATE POLICY {_POLICY} ON {_TABLE} "
            "AS PERMISSIVE FOR ALL TO PUBLIC "
            "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
            "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )

    # 9. Comments: per-template grain + RLS ON correction (the prior table
    #    comment's "configuration, not tenant data" rationale is retired).
    _exec(
        f"COMMENT ON TABLE {_TABLE} IS "
        "'Versioned mapping configurations per (tenant, source, template). A source may "
        "carry multiple named templates (e.g. sales, inventory, pricing), each with its "
        "own version lineage. Immutable once written; edits create new versions. The FK "
        "target for canonical.*.mapping_version_id (B1 architecture v0.6). RLS ON "
        "(ENABLE + FORCE, single-GUC tenant_isolation policy, Slice 14a): rows are "
        "per-tenant data, read by the streaming consumer per-lookup inside a "
        "tenant-scoped rls_session (D6 side input).'"
    )
    _exec(
        f"COMMENT ON COLUMN {_TABLE}.status IS "
        "'Lifecycle status. DRAFT: not promoted. STAGED: shadow rollout (writes go to "
        "staging.*). ACTIVE: in production. DEPRECATED: superseded, retained for replay. "
        "At most one ACTIVE per (tenant, source, template) enforced by "
        "uq_csm_active_per_source.'"
    )
    _exec(
        f"COMMENT ON COLUMN {_TABLE}.version_seq_per_source IS "
        "'Per-(tenant, source, template) sequence number, starting at 1. Set "
        "automatically by the trg_csm_set_version_seq trigger on INSERT if NULL or 0 is "
        "supplied. The column name predates the template grain and is kept to avoid "
        "contract churn (Slice 14a); it sequences per template. Forms part of the "
        "generated label.'"
    )
    _exec(
        f"COMMENT ON COLUMN {_TABLE}.template_id IS "
        "'Stable identity of the mapping template under (tenant_id, source_id). UUIDv7, "
        "minted server-side at DRAFT creation (Slice 14b write path); immutable once set "
        "(write-path enforced convention, Slice 14a). All version rows of one template "
        "share this id. Pre-14a rows were backfilled with one template per "
        "(tenant, source).'"
    )
    _exec(
        f"COMMENT ON COLUMN {_TABLE}.template_name IS "
        "'Operator-set human label for the template. Editable. Unique per "
        "(tenant_id, source_id) among non-DEPRECATED rows: "
        "ex_csm_template_name_per_source rejects two different template_ids sharing a "
        "name, while version rows of one template share the name freely. Backfilled to "
        "default for pre-14a rows.'"
    )
    _exec(
        f"COMMENT ON COLUMN {_TABLE}.predecessor_version_id IS "
        "'The mapping_version_id this version was edited from. NULL for the first "
        "version of a (tenant, source, template). Informational only (not a FK).'"
    )
    _exec(
        f"COMMENT ON VIEW {_VIEW} IS "
        "'View of source_mappings with a generated human-readable label column. Label "
        "pattern: {first_word_of_tenant_name}-{source_id}-v{seq}-{YYYYMMDD}. Tenant "
        "name fetched from identity_mirror.tenants. security_invoker: executes with "
        "the rights of the querying role so the tenant_isolation policy applies "
        "(Slice 14a). Label does not yet incorporate the template (14b gap).'"
    )


def downgrade() -> None:
    _guard_target()

    if _column_exists("template_id"):
        # The old (tenant, source) keys cannot hold over multi-template data —
        # pre-check and raise loudly rather than fail mid-DDL.
        multi_template = _scalar(
            "SELECT COUNT(*) FROM ("
            "  SELECT tenant_id, source_id FROM config.source_mappings "
            "  GROUP BY tenant_id, source_id HAVING COUNT(DISTINCT template_id) > 1"
            ") x"
        )
        multi_active = _scalar(
            "SELECT COUNT(*) FROM ("
            "  SELECT tenant_id, source_id FROM config.source_mappings "
            "  WHERE status = 'ACTIVE' "
            "  GROUP BY tenant_id, source_id HAVING COUNT(*) > 1"
            ") x"
        )
        dup_seq = _scalar(
            "SELECT COUNT(*) FROM ("
            "  SELECT tenant_id, source_id, version_seq_per_source "
            "  FROM config.source_mappings "
            "  GROUP BY tenant_id, source_id, version_seq_per_source HAVING COUNT(*) > 1"
            ") x"
        )
        if multi_template or multi_active or dup_seq:
            raise RuntimeError(
                "Refusing to downgrade 0005: the pre-0005 (tenant, source) keys cannot "
                f"hold over the live data ({multi_template} source(s) with multiple "
                f"templates, {multi_active} with multiple ACTIVE rows, {dup_seq} "
                "duplicate (tenant, source, version_seq) group(s)). Resolve the rows "
                "(one template, one ACTIVE, unique seqs per source) and re-run."
            )

    # Reverse order of upgrade.
    _exec(f"DROP POLICY IF EXISTS {_POLICY} ON {_TABLE}")
    _exec(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY")
    _exec(f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY")
    _exec(f"ALTER VIEW {_VIEW} SET (security_invoker = false)")
    _exec(_SET_VERSION_SEQ_FN_PRE_0005)
    _exec(f"ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {_EXCLUDE_CONSTRAINT}")

    _exec(f"DROP INDEX IF EXISTS config.{_ACTIVE_INDEX}")
    _exec(f"CREATE UNIQUE INDEX {_ACTIVE_INDEX} ON {_TABLE} (tenant_id, source_id) WHERE status = 'ACTIVE'")

    _exec(f"ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {_SEQ_CONSTRAINT}")
    _exec(
        f"ALTER TABLE {_TABLE} ADD CONSTRAINT {_SEQ_CONSTRAINT} "
        "UNIQUE (tenant_id, source_id, version_seq_per_source)"
    )

    _exec(f"ALTER TABLE {_TABLE} DROP COLUMN IF EXISTS template_name")
    _exec(f"ALTER TABLE {_TABLE} DROP COLUMN IF EXISTS template_id")

    # Restore the pre-0005 comments verbatim (btree_gist is left installed).
    _exec(
        f"COMMENT ON TABLE {_TABLE} IS "
        "'Versioned mapping configurations per (tenant, source). Immutable once written; "
        "edits create new versions. The FK target for canonical.*.mapping_version_id "
        "(B1 architecture v0.6). Read by the streaming consumer as a refreshing side "
        "input; new versions trigger refresh via mapping.changed Pub/Sub event.'"
    )
    _exec(
        f"COMMENT ON COLUMN {_TABLE}.status IS "
        "'Lifecycle status. DRAFT: not promoted. STAGED: shadow rollout (writes go to "
        "staging.*). ACTIVE: in production. DEPRECATED: superseded, retained for replay. "
        "At most one ACTIVE per (tenant, source) enforced by uq_csm_active_per_source.'"
    )
    _exec(
        f"COMMENT ON COLUMN {_TABLE}.version_seq_per_source IS "
        "'Per-(tenant, source) sequence number, starting at 1. Set automatically by the "
        "trg_csm_set_version_seq trigger on INSERT if NULL or 0 is supplied. Forms part "
        "of the generated label.'"
    )
    _exec(
        f"COMMENT ON COLUMN {_TABLE}.predecessor_version_id IS "
        "'The mapping_version_id this version was edited from. NULL for the first "
        "version of a (tenant, source). Informational only (not a FK).'"
    )
    _exec(
        f"COMMENT ON VIEW {_VIEW} IS "
        "'View of source_mappings with a generated human-readable label column. Label "
        "pattern: {first_word_of_tenant_name}-{source_id}-v{seq}-{YYYYMMDD}. Tenant "
        "name fetched from identity_mirror.tenants.'"
    )
