"""bronze.data_ingress_events: template_id replay-lineage column (Slice 8)

The single DDL of Slice 8 (D71 carry): bronze persists WHICH mapping template
(``config.source_mappings`` template lineage, D68) a CSV was uploaded against,
so a replay (``ingress.resubmit``, Slice 12) can re-derive the template from
the bronze pointer rather than from a long-gone request. The column is
informational lineage, exactly like ``mapping_version_id`` beside it:

- **Nullable** — every pre-Slice-8 row genuinely has no template (the contract
  gained ``template_id`` in the same commit; history cannot be backfilled
  truthfully). New worker writes always populate it (the ``csv.received``
  contract requires the field).
- **No FK** — ``template_id`` is not FK-addressable (``config.source_mappings``
  is keyed by ``mapping_version_id``; ``template_id`` repeats across the
  template's version rows), and bronze deliberately does not enforce on a
  config table (the live ``mapping_version_id`` comment's rationale).
- **No index** — nothing queries bronze by template; the dedup key (D58) is
  unchanged.

Idempotent for 0001-fresh-bootstrap parity (the updated
``schemas/postgres/bronze/data_ingress_events.sql`` manifest carries the full
end state): the ADD COLUMN is gated on column existence; the COMMENT is
natively idempotent.

The register entry (bronze persists template_id, per D71) receives an
operator-assigned D-number at the commit gate.

See: docs/slices/slice-08-csv-upload-phase1.md, decisions.md D58/D71.

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-06

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

_COMMENT = (
    "The mapping template (config.source_mappings template lineage) this payload "
    "was uploaded against, carried on csv.received and persisted here for replay "
    "lineage (Slice 8 / D71). Informational, like mapping_version_id: no FK "
    "(template_id is not FK-addressable and bronze does not enforce on config "
    "tables). NULL only on pre-Slice-8 rows; the contract requires the field on "
    "every event since."
)


def upgrade() -> None:
    # Gated on existence for fresh-bootstrap parity (the schemas/postgres manifest
    # already carries the column on a 0001-fresh database).
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'bronze'
                  AND table_name = 'data_ingress_events'
                  AND column_name = 'template_id'
            ) THEN
                ALTER TABLE bronze.data_ingress_events ADD COLUMN template_id UUID NULL;
            END IF;
        END
        $$;
        """
    )
    op.execute(f"COMMENT ON COLUMN bronze.data_ingress_events.template_id IS '{_COMMENT}'")


def downgrade() -> None:
    op.execute("ALTER TABLE bronze.data_ingress_events DROP COLUMN IF EXISTS template_id")
