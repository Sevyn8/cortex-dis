"""Column provenance: which canonical columns the mapping produces (slice-05 OQ9).

The partial-contribution framing rests on this line: the engine emits the
source-owned, mapping-produced columns; the consumer injects identity
(``tenant_id``/``store_id``), ``trace_id``, ``mapping_version_id`` and the other
write-time columns (D8, hard rule 5, D22). The line was drawn from the LIVE
``ithina_dis_db`` canonical schema (introspected columns + column comments,
slice-05 plan mode), not from DDL files or docs.

The registry carries an EXPLICIT five-way partition per model (consumer-injected,
DB-generated, compute-owned, mapping-produced, and — slice-5b, D95 —
enrichment-produced: canonical values written by the dis-enrichment lib from an
authoritative internal source, NOT the mapping). ``assert_no_drift`` checks the
partition against ``model_fields`` exactly, BOTH directions — so a canonical
column added to the model (the Slice 3 live-schema reconciliation forces that)
cannot silently join any set: it must be classified here or the guard errors.
This guard ERRORS (``SuiteDriftError``), it never skips — criterion 6.

``store_sku_signal_history`` is not mapping-produced at all (daily-compute
output; no ``mapping_version_id`` column exists on it — D22/D31/D32). Requesting
a mapping-time treatment of it raises by design.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from dis_canonical import (
    StoreSkuChangeEvent,
    StoreSkuCurrentPosition,
    StoreSkuSaleEvent,
    StoreSkuSignalHistory,
)
from dis_core.errors import SuiteDriftError


@dataclass(frozen=True)
class ColumnProvenance:
    """One model's explicit column partition (everything except mapping-produced)."""

    consumer_injected: frozenset[str]
    db_generated: frozenset[str]
    compute_owned: frozenset[str]
    mapping_produced: frozenset[str]
    # Enrichment-produced (slice-5b, D95): canonical values the dis-enrichment lib
    # writes from an authoritative internal source, NOT the mapping. Subject to the
    # SAME canonical-shape gate as mapping-produced (D94), so the drift guard admits
    # them as source-owned (canonical_shape.materialize). Default empty: only the
    # current-position table registers any this slice.
    enrichment_produced: frozenset[str] = frozenset()


# Common to all three mapping-fed tables (introspected evidence inline):
_COMMON_CONSUMER_INJECTED = frozenset(
    {
        "tenant_id",  # identity — consumer-injected after the engine runs (D8)
        "store_id",  # identity — composite store FK (D39); engine never holds it
        "mapping_version_id",  # stamped by the streaming consumer (hard rule 5, D22)
        "trace_id",  # minted at the receiver, propagated by the consumer (hard rule 4)
        "dis_channel",  # live comment: "Ingress channel that delivered the data" — envelope fact
        "ingest_metadata",  # live comment names dis_received_timestamp/dis_published_timestamp/
        # csv_row_num — consumer-known lineage, not source data
    }
)
_COMMON_DB_GENERATED = frozenset(
    {
        "id",  # uuid NOT NULL DEFAULT uuidv7() (live default)
        "last_updated_at",  # timestamptz NOT NULL DEFAULT now() (live default)
    }
)

PROVENANCE: dict[type[BaseModel], ColumnProvenance] = {
    StoreSkuCurrentPosition: ColumnProvenance(
        consumer_injected=_COMMON_CONSUMER_INJECTED
        | frozenset(
            {
                # Live comment: "Comparison reference for the event-time-wins
                # conditional upsert ... Consumer-injected by the streaming
                # consumer." (D64, migration 0003.)
                "last_source_event_at",
            }
        ),
        db_generated=_COMMON_DB_GENERATED,
        compute_owned=frozenset(
            {
                # Daily-compute output (D31); refreshed on current_position daily.
                "velocity_7day",
                "stock_age_days",
                "unit_cost_trend_30day",
                "attribute_staleness_map",
                # OPERATOR-CONFIRMED JUDGMENT, not introspected fact (slice-05 plan
                # Revision 2, confirmation 3): live comment says "Previous-day
                # retail price for change-detection by ROOS agents ... TBD" — no
                # source asserts yesterday's price, so it is classified
                # compute-owned. Owning slice: 18 (daily-compute). Revisitable.
                "yesterday_retail_price",
            }
        ),
        mapping_produced=frozenset(
            {
                "sku_id",
                "sku_variant",
                "sku_lot_batch",
                "barcode",
                "product_name",
                "product_description",
                "product_category",
                "product_sub_category",
                "product_department",
                "supplier_id",
                "packaging_type",
                "sku_size",
                "unit_of_measure",
                "current_retail_price",
                "unit_cost",
                "promo_price",
                "promo_identifier",
                "stock_qty",
                "lead_time_days",
                "expiry_date",
                "receipt_date",
                "expiry_source",
                "expiry_confidence",
                # SLICE-10 VERIFICATION ITEM (family judgment, not inscription):
                # the live comment names no populator ("TRUE if this SKU is
                # regulated. Default FALSE; NULLABLE ... to allow 'unknown'");
                # classified mapping-produced as catalogue-data family. Verify when
                # Slice 10 builds the write path.
                "regulatory_flag",
                "regulatory_type",
                # UNCONFIRMABLE FROM LIVE EVIDENCE (flagged, slice-05 adversarial
                # pass): currency carries NO column comment, while its sibling
                # tax_treatment is explicitly "Denormalized from store" and
                # identity_mirror.stores also carries a currency column — so
                # store-denormalization is plausible by symmetry. Kept
                # mapping-produced (the mapping can produce it, e.g. a per-source
                # `constant`); owner: Slice 10 settles who writes it.
                "currency",
                "reorder_point",
                "sku_status",
            }
        ),
        # Enrichment-produced (slice-5b, D98): tax_treatment migrates from
        # consumer-injected into the dis-enrichment lib on the current-position
        # path and becomes canonical-shape-validated (the TaxTreatment enum vocab).
        # The event models keep tax_treatment consumer-injected (event path out of
        # scope) — the deliberate D98 asymmetry. currency STAYS mapping-produced:
        # the lib overrides its VALUE, not the column's origin (the mapping still
        # produces the column; the lib's value wins, D95).
        enrichment_produced=frozenset({"tax_treatment"}),
    ),
    StoreSkuSaleEvent: ColumnProvenance(
        consumer_injected=_COMMON_CONSUMER_INJECTED
        | frozenset(
            {
                # D33 dedup key (D38 resolution, migration 0003). Live comments:
                # "Consumer-injected from the ingress.ready envelope" /
                # "Consumer-injected (D38 resolution)" — populated by the
                # consumer (envelope source_id; transaction_id:line_item_seq or
                # the D65 bronze_ref:row_index fallback), never by the engine.
                "source_id",
                "source_event_id",
                # Live comment: "Soft cross-reference ... at write time" — a DB row
                # id the engine cannot know.
                "store_sku_current_position_id",
                # SLICE-10 VERIFICATION ITEM (interpretation, not inscription): the
                # live comment says "the id of the original SALE event ... may be
                # NULL if the source does not provide it" — but the id is a
                # DIS-internal uuid no source can mint; the source provides its own
                # reference, which the consumer resolves to the DIS id at write
                # time. Classified consumer-injected on that reasoning; verify when
                # Slice 10 builds the write path.
                "related_sale_event_id",
                # Live comment: "Denormalized from store." (same evidence as the hot
                # table; reclassified during the slice-05 adversarial pass).
                "tax_treatment",
            }
        ),
        db_generated=_COMMON_DB_GENERATED,
        compute_owned=frozenset(),
        mapping_produced=frozenset(
            {
                # OPERATOR-CONFIRMED JUDGMENT (slice-05 plan Revision 2,
                # confirmation 3): event_date is mapping-produced via the derive
                # sub-stage (live comment: "DATE derived from
                # source_sale_timestamp::date at UTC. CHECK constraint enforces").
                # The consumer could equally stamp it at write; the slice fixes the
                # consumer-injected set to exactly identity/trace_id/
                # mapping_version_id, so it lands in derive. Owning slice: 10.
                "event_date",
                "sku_id",
                "sku_variant",
                "sku_lot_batch",
                "event_subtype",
                "source_sale_timestamp",
                "transaction_id",
                "line_item_seq",
                "quantity",
                "unit_retail_price",
                "unit_sale_price",
                "discount_amount",
                "discount_pct",
                "unit_cost",
                "promo_identifier",
                "tax_amount",
                # currency: same unconfirmable-provenance flag as on the hot table.
                "currency",
                "payment_method",
                # Arrives tokenized at the receiver (D24); mapped as data.
                "customer_token",
                "sale_channel",
            }
        ),
    ),
    StoreSkuChangeEvent: ColumnProvenance(
        consumer_injected=_COMMON_CONSUMER_INJECTED
        | frozenset(
            {
                # D33 dedup key (D38 resolution, migration 0003); same evidence
                # as the sale model. Change events have no native source event
                # id, so the D65 fallback always applies — still consumer-known.
                "source_id",
                "source_event_id",
                "store_sku_current_position_id",
                # Live comment: "Typed shortcut for numeric attributes ... Populated
                # by the streaming consumer for INVENTORY, PRICE, and COST changes
                # alongside JSONB value_before." — explicit. (Reclassified from
                # mapping-produced during the slice-05 adversarial pass.)
                "numeric_value_before",
                # Live comment: "Same population rules as numeric_value_before."
                "numeric_value_after",
                # Family inference (near-evidence): the signed delta of the two
                # consumer-populated shortcut columns can only be consumer-computed.
                "numeric_change",
            }
        ),
        db_generated=_COMMON_DB_GENERATED,
        compute_owned=frozenset(),
        mapping_produced=frozenset(
            {
                "event_date",  # derive; same operator-confirmed judgment as sale events
                "sku_id",
                "sku_variant",
                "sku_lot_batch",
                "event_category",
                "event_subtype",
                "source_event_timestamp",
                "effective_from",
                "effective_until",
                "attribute_name",
                "value_before",
                "value_after",
                "reason_code",
                "reason_note",
                "change_context",
            }
        ),
    ),
}

# Daily-compute output, never mapping-produced (D22/D31/D32): no mapping-time
# suite exists for it; asking for one is drift by definition.
NOT_MAPPING_PRODUCED: frozenset[type[BaseModel]] = frozenset({StoreSkuSignalHistory})


def assert_no_drift(model: type[BaseModel]) -> None:
    """Assert the provenance partition matches ``model.model_fields`` exactly.

    Both directions: every model field is classified in exactly one set, and every
    classified column exists on the model. Errors (never skips) on any mismatch —
    slice-05 criterion 6.
    """
    if model in NOT_MAPPING_PRODUCED:
        raise SuiteDriftError(
            f"{model.__name__} is daily-compute output, not mapping-produced "
            "(D22/D31/D32; it carries no mapping_version_id) — no mapping-time "
            "suite or provenance partition exists for it",
            model=model.__name__,
        )
    provenance = PROVENANCE.get(model)
    if provenance is None:
        raise SuiteDriftError(
            f"{model.__name__} has no provenance registration; classify its columns "
            "before building a canonical-shape suite for it",
            model=model.__name__,
        )

    sets = {
        "consumer_injected": provenance.consumer_injected,
        "db_generated": provenance.db_generated,
        "compute_owned": provenance.compute_owned,
        "mapping_produced": provenance.mapping_produced,
        "enrichment_produced": provenance.enrichment_produced,
    }
    names = list(sets)
    for i, first in enumerate(names):
        for second in names[i + 1 :]:
            overlap = sets[first] & sets[second]
            if overlap:
                raise SuiteDriftError(
                    f"{model.__name__}: column(s) {sorted(overlap)} classified in both {first} and {second}",
                    model=model.__name__,
                    column=sorted(overlap)[0],
                )

    model_fields = frozenset(model.model_fields)
    classified = frozenset().union(*sets.values())
    unclassified = model_fields - classified
    if unclassified:
        raise SuiteDriftError(
            f"{model.__name__}: model field(s) {sorted(unclassified)} are not classified "
            "in the provenance registry — a new canonical column must be classified "
            "before suites can be built (it does NOT silently join any set)",
            model=model.__name__,
            column=sorted(unclassified)[0],
        )
    stale = classified - model_fields
    if stale:
        raise SuiteDriftError(
            f"{model.__name__}: registry column(s) {sorted(stale)} are absent from the "
            "model — the registry is stale",
            model=model.__name__,
            column=sorted(stale)[0],
        )


def mapping_produced_columns(model: type[BaseModel]) -> frozenset[str]:
    """The columns the mapping engine may produce for ``model`` (drift-checked)."""
    assert_no_drift(model)
    return PROVENANCE[model].mapping_produced


def enrichment_produced_columns(model: type[BaseModel]) -> frozenset[str]:
    """The columns the dis-enrichment lib may produce for ``model`` (drift-checked, slice-5b).

    These are source-owned for the canonical-shape gate (D94): the suite drift
    guard admits ``owned ⊆ mapping_produced ∪ enrichment_produced``. Empty for every
    model except the current-position table this slice (D95/D98).
    """
    assert_no_drift(model)
    return PROVENANCE[model].enrichment_produced


def mandatory_mapping_produced(
    model: type[BaseModel], enrichment_guaranteed: frozenset[str] = frozenset()
) -> frozenset[str]:
    """The columns a mapping MUST supply for ``model``: required (non-Optional) AND
    mapping-produced, minus any column whose value is enrichment-guaranteed.

    The intersection of pydantic required-ness with the provenance partition gives the
    columns the mapping is the ORIGIN of; subtracting ``enrichment_guaranteed`` (Slice
    16i, D95) drops the columns the dis-enrichment lib supplies the VALUE for (currency
    on the current-position path), so they are not demanded of the mapping even though
    they remain mapping-produced by origin (still legal to map, just not required).

    ``enrichment_guaranteed`` is passed IN by the caller (the consumer / dis-ui-server
    hand in ``enrichment_fields(table)``) so this lib stays pure — it never imports
    dis-enrichment. Default empty preserves the plain required-∩-mapping-produced set
    (the sale/change event models, which enrichment does not touch — D98).

    Promoted here in Slice 16h so a SINGLE derivation feeds both the create-time gate
    (dis-ui-server ``check_mandatory_coverage``) and the write-time completeness gate
    (streaming-consumer ``classify_hot_completeness``); a canonical-model change flows
    through automatically (and trips the provenance drift guard first if unclassified).
    """
    produced = mapping_produced_columns(model)
    required = frozenset(name for name, field in model.model_fields.items() if field.is_required())
    return (required & produced) - enrichment_guaranteed
