"""The completeness partition, anchored to the LIVE schema (REVISED D63; Slice 16h).

The code constants in ``pipeline/mapping.py`` (``HOT_REQUIRED_FROM_PROJECTION``,
``HOT_CHECK_IMPLICATIONS``) are MODEL-DERIVED — the required set since Slice 16h is
``mandatory_mapping_produced(StoreSkuCurrentPosition)``. This test re-derives them
from the live ``information_schema`` / ``pg_constraint`` at RUN time and asserts exact
agreement, so a hot-schema change (a new NOT NULL column, a dropped pairing CHECK)
cannot silently leave the gate stale: it fails HERE, loudly. The required-set anchor
is the live-NOT-NULL set ∩ the mapping_produced provenance partition.

ERROR-not-skip: the conftest's stack fixtures raise StackRequiredError when the
DB is absent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from dis_canonical import StoreSkuCurrentPosition
from dis_enrichment import CURRENT_POSITION, enrichment_fields
from dis_validation import mapping_produced_columns
from streaming_consumer.pipeline.mapping import (
    HOT_CHECK_IMPLICATIONS,
    HOT_REQUIRED_FROM_PROJECTION,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration


def test_required_from_projection_matches_live_not_null_set(dis_admin: Engine) -> None:
    # Slice 16h/16i: HOT_REQUIRED_FROM_PROJECTION is MODEL-DERIVED
    # (mandatory_mapping_produced(StoreSkuCurrentPosition) = required-in-model ∩
    # mapping_produced, minus the enrichment value-guaranteed fields). Re-derive the
    # SAME set straight from the live schema and assert exact agreement, so a hot-schema
    # nullability change cannot silently leave the gate stale.
    #
    # The principled exclusions: intersect with mapping_produced (the provenance
    # partition) — drops consumer-injected columns (tenant_id, store_id,
    # mapping_version_id, trace_id, dis_channel) AND enrichment-PRODUCED tax_treatment —
    # then subtract enrichment_fields(CURRENT_POSITION), which removes the enrichment
    # value-guaranteed currency (Slice 16i: mapping-produced by origin, but the lib
    # supplies its value, so it is not required FROM the mapping). That is exactly the
    # 5-member required set the gate uses — no hand-curated subtraction list to drift. A
    # future 16j nullability change flips a column out of both live NOT-NULL-no-default
    # and model is_required together, so this stays green; a re-baked literal breaks it.
    with dis_admin.begin() as conn:
        live_not_null = {
            row.column_name
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='canonical' AND table_name='store_sku_current_position' "
                    "AND is_nullable='NO' AND column_default IS NULL"
                )
            )
        }
    produced = mapping_produced_columns(StoreSkuCurrentPosition)
    enrichment_guaranteed = frozenset(enrichment_fields(CURRENT_POSITION))
    derived_required = (frozenset(live_not_null) & produced) - enrichment_guaranteed
    assert derived_required == HOT_REQUIRED_FROM_PROJECTION, (
        f"live-derived: {sorted(derived_required)} vs "
        f"code constant: {sorted(HOT_REQUIRED_FROM_PROJECTION)} — the gate is stale"
    )


def test_check_implications_match_live_pairing_constraints(dis_admin: Engine) -> None:
    with dis_admin.begin() as conn:
        constraint_defs = {
            row.conname: row.definition
            for row in conn.execute(
                text(
                    "SELECT conname, pg_get_constraintdef(oid) AS definition FROM pg_constraint "
                    "WHERE conrelid = 'canonical.store_sku_current_position'::regclass "
                    "AND contype = 'c'"
                )
            )
        }
    # The two presence-pairing CHECKs the implications encode must exist live
    # with the encoded shape.
    promo = constraint_defs.get("ck_sscp_promo_identifier_requires_price", "")
    assert "promo_identifier IS NULL" in promo and "promo_price IS NOT NULL" in promo
    expiry = constraint_defs.get("ck_sscp_expiry_triple_pairing", "")
    for column in ("expiry_date", "expiry_source", "expiry_confidence"):
        assert column in expiry
    # And the code constant encodes exactly those two implications.
    triggers = {frozenset(t) for t, _c in HOT_CHECK_IMPLICATIONS}
    assert triggers == {
        frozenset({"promo_identifier"}),
        frozenset({"expiry_date", "expiry_source", "expiry_confidence"}),
    }
