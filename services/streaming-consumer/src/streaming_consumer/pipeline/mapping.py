"""Mapping config load (per-lookup side-input, D6) + routing + engine apply.

- **Active selection (template-keyed since Slice 8a, D71):** ``SELECT … WHERE
  tenant_id AND source_id AND template_id AND status='ACTIVE'`` — the live
  partial unique index ``uq_csm_active_per_source`` is
  ``(tenant_id, source_id, template_id) WHERE status='ACTIVE'`` (one ACTIVE per
  template under a source, D68), so the keyed lookup returns at most one row
  genuinely, not by ``.first()`` luck. ``template_id`` is read off the
  ``ingress.ready`` envelope (required field; an absent value never reaches this
  lookup — it fails the envelope contract-reject first). An absent ACTIVE mapping
  for the named template raises ``MappingConfigError`` (required value,
  code-quality rule 4 — never a silent fallback). STAGED/shadow reads are out of
  scope for this consumer (the next slice's promote/shadow path).
- **Refresh mechanism:** per-lookup, no cache (Slice 10 plan §4). Zero staleness;
  one indexed SELECT per chunk is invisible at beta volume. ``mapping.changed``
  event-driven refresh (D6) is DEFERRED; trigger: sustained chunk rates where the
  per-chunk SELECT is a measured cost, or an operator latency requirement.
- **Suites:** the live rows' ``pre/post_validation_suite_ref`` are NULL ("use
  default"). A non-NULL ``module:ClassName`` ref is NOT supported in Slice 10 —
  it raises ``SuiteDefinitionError`` (no dynamic import; D61 declarative-only
  spirit). Registered scope limit.
- **Routing (sale-versus-change):** static, mapping-load-time. The mapping's
  target column set must be a subset of EXACTLY one event model's
  mapping-produced set (``dis-validation`` provenance); zero or two matches is a
  config error. Per-row branching is Slice 11.

The lookup runs through ``rls_session`` (hard rule 12); ``config.source_mappings``
is RLS ON+FORCE since migration 0005, so the tenant GUC set by ``rls_session``
scopes the lookup.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace

import polars as pl
from pydantic import BaseModel, ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from dis_canonical import StoreSkuChangeEvent, StoreSkuCurrentPosition, StoreSkuSaleEvent
from dis_core.errors import MappingConfigError, SuiteDefinitionError
from dis_core.logging import LogContext
from dis_enrichment import CURRENT_POSITION, apply_enrichment, enrichment_fields
from dis_mapping import MappingResult, SourceMapping, apply_mapping
from dis_rls import rls_session
from dis_validation import SNAPSHOT, mandatory_mapping_produced, mapping_produced_columns
from streaming_consumer.envelope import IngressReadyEvent

# The routing universe: the two event tables of the dual-write (architecture 4.6).
# The hot table is always the upsert side, never a routing target.
EVENT_MODELS: tuple[type[BaseModel], ...] = (StoreSkuSaleEvent, StoreSkuChangeEvent)

# ---------------------------------------------------------------------------
# Hot-projection registries (D63) + the completeness discriminator (REVISED
# D63, operator-ratified: hot-row CREATION is COMPLETENESS-gated, not
# event-type-gated). Load-time concerns, so they live here; normalize.py
# consumes them for the per-row write shape.
# ---------------------------------------------------------------------------

# Sale events: event column -> hot column (D63 register text).
SALE_HOT_PROJECTION: dict[str, str] = {
    "unit_retail_price": "current_retail_price",
    "unit_cost": "unit_cost",
    "promo_identifier": "promo_identifier",
    "currency": "currency",
}

# Change events: (event_category, attribute_name) -> hot column (consumer
# convention; register-anchor gap recorded in the carried limits).
CHANGE_HOT_PROJECTION: dict[tuple[str, str], str] = {
    ("PRICE", "current_retail_price"): "current_retail_price",
    ("COST", "unit_cost"): "unit_cost",
    ("INVENTORY", "stock_qty"): "stock_qty",
    ("CATALOGUE", "product_name"): "product_name",
    ("STATUS", "sku_status"): "sku_status",
}

# The completeness partition, MODEL-DERIVED (Slice 16h) — required-in-model ∩
# mapping-produced for the HOT model, the same derivation the create-time gate uses
# (dis-ui-server check_mandatory_coverage). DB/model nullability is the single source
# of truth: a future NOT NULL <-> NULLABLE change (16j) flows through with no edit
# here. PINNED to StoreSkuCurrentPosition (the hot/current-position model) — NEVER the
# routed target_model: the completeness question is always about the hot row, and the
# routed model only ever feeds guaranteed_hot_columns below.
#
# This derived set is {sku_id, product_name, product_category, current_retail_price,
# unit_cost} (5 members). The enrichment-guaranteed fields are SUBTRACTED (Slice 16i,
# D95): currency is mapping-produced by origin but its VALUE is enrichment-guaranteed,
# so it is not required FROM the mapping (and tax_treatment was never in the set — it
# is enrichment-produced, not mapping-produced). This matches the old hand-curated
# literal exactly. Completeness is unaffected by the subtraction: guaranteed_hot_columns
# still unions enrichment_fields(CURRENT_POSITION) back in (below), so currency counts
# toward a complete hot row even when the mapping does not project it; sku_id stays in
# guaranteed for every create-validated mapping. The subtraction set is handed in (the
# lib stays pure and never imports dis-enrichment).
HOT_REQUIRED_FROM_PROJECTION = mandatory_mapping_produced(
    StoreSkuCurrentPosition, enrichment_guaranteed=frozenset(enrichment_fields(CURRENT_POSITION))
)
# Presence-pairing CHECKs (shape-level; value-range CHECKs are runtime data):
# projecting any column in the trigger set requires every column in the
# companion set (ck_sscp_promo_identifier_requires_price,
# ck_sscp_expiry_triple_pairing).
HOT_CHECK_IMPLICATIONS: tuple[tuple[frozenset[str], frozenset[str]], ...] = (
    (frozenset({"promo_identifier"}), frozenset({"promo_price"})),
    (
        frozenset({"expiry_date", "expiry_source", "expiry_confidence"}),
        frozenset({"expiry_date", "expiry_source", "expiry_confidence"}),
    ),
)


def _constant_derive(source: SourceMapping, column: str) -> str | None:
    """The pinned value when ``column`` is derived as a constant, else None."""
    specs = source.derive.get(column)
    if specs and specs[0].op == "constant":
        value = specs[0].args.get("value")
        return value if isinstance(value, str) else None
    return None


def guaranteed_hot_columns(source: SourceMapping, target_model: type[BaseModel]) -> frozenset[str]:
    """The hot columns this mapping's projection STATICALLY guarantees.

    Sale: the registry image of the mapping's targets. Change: only when
    ``event_category`` AND ``attribute_name`` are derive-CONSTANTS is the
    (single) projected hot column knowable at load; data-driven category or
    attribute means nothing is guaranteed (conservative — the incomplete path
    never inserts, so under-claiming is safe). Per-row NULL values can still
    void a guaranteed column at runtime; that is a loud data error at the
    write, not a classification concern.
    """
    targets = set(source.target_columns)
    if target_model is StoreSkuCurrentPosition:
        # CATALOGUE (snapshot) identity projection: a mapping-produced target IS the
        # same-named hot column (no projection registry). The completeness gate then
        # checks HOT_REQUIRED_FROM_PROJECTION ⊆ this set — true for a valid snapshot,
        # whose mandatory set the create-time validator already enforced.
        # slice-5b (D95): the enrichment-guaranteed fields (currency, tax_treatment)
        # are unconditionally supplied by dis-enrichment on this path, so they count
        # toward completeness even when the mapping does not project them.
        mapped = frozenset(targets) & mapping_produced_columns(StoreSkuCurrentPosition)
        return mapped | frozenset(enrichment_fields(CURRENT_POSITION))
    if target_model is StoreSkuSaleEvent:
        return frozenset(SALE_HOT_PROJECTION[t] for t in targets if t in SALE_HOT_PROJECTION)
    category = _constant_derive(source, "event_category")
    attribute = _constant_derive(source, "attribute_name")
    if category is None or attribute is None:
        return frozenset()
    hot_column = CHANGE_HOT_PROJECTION.get((category, attribute))
    return frozenset({hot_column}) if hot_column else frozenset()


def event_contendable_hot_columns() -> frozenset[str]:
    """Hot columns ANY event projection can mutate — DERIVED from the live registries.

    The union of the SALE and CHANGE projection images. Registry-driven by
    construction: extend a projection registry and this set follows, with no
    second list to keep in sync (the catalogue staleness set below depends on it).
    """
    return frozenset(SALE_HOT_PROJECTION.values()) | frozenset(CHANGE_HOT_PROJECTION.values())


def catalogue_staleness_columns() -> frozenset[str]:
    """The set the catalogue write stamps in ``attribute_staleness_map``.

    The intersection the slice settled (Slice 14d): columns the catalogue write
    CAN set (the hot model's mapping-produced columns) AND that an event path CAN
    mutate (``event_contendable_hot_columns``). DERIVED, never a literal list — a
    projection-registry change flows through automatically. The per-write stamp is
    further narrowed to the columns a given snapshot row actually sets. Stamping
    exactly the contendable attributes gives the deferred collision slice
    per-attribute freshness for every contendable attribute (no row-level fallback);
    descriptive fields no event contends for are not stamped, and the compute-owned
    map entries are never written here."""
    return mapping_produced_columns(StoreSkuCurrentPosition) & event_contendable_hot_columns()


def classify_hot_completeness(source: SourceMapping, target_model: type[BaseModel]) -> bool:
    """REVISED D63: may this mapping's candidate CREATE a hot row?

    Complete iff the statically guaranteed projection covers every
    discriminating NOT NULL column AND every presence-pairing CHECK
    implication. No current production mapping is complete (sale targets can
    never carry product_name/product_category; change mappings guarantee at
    most one hot column) — the complete path is the future catalogue slice's,
    defined by completeness so it needs no upsert change when it arrives.
    """
    guaranteed = guaranteed_hot_columns(source, target_model)
    if not HOT_REQUIRED_FROM_PROJECTION <= guaranteed:
        return False
    return all(
        companion <= guaranteed for trigger, companion in HOT_CHECK_IMPLICATIONS if trigger & guaranteed
    )


@dataclass(frozen=True)
class LoadedMapping:
    """One ACTIVE mapping row, parsed, routed, and completeness-classified."""

    mapping_version_id: int
    source: SourceMapping
    target_model: type[BaseModel]
    # REVISED D63: True iff this mapping's candidate (projection + the
    # consumer-injected fields) can satisfy every hot NOT NULL and CHECK —
    # the ONLY path allowed to INSERT a hot row. Resolved at load, per mapping.
    hot_complete: bool = False


def route_target_model(source: SourceMapping, *, tenant_id: str, trace_id: str) -> type[BaseModel]:
    """Resolve the one event model this mapping's contribution targets.

    The discriminating mapping-produced sets are disjoint (e.g.
    ``source_sale_timestamp`` vs ``source_event_timestamp``), so a real mapping
    matches exactly one; zero or several is an authoring error, raised loudly.
    """
    targets = set(source.target_columns)
    matches = [m for m in EVENT_MODELS if targets <= mapping_produced_columns(m)]
    if len(matches) != 1:
        names = [m.__name__ for m in matches]
        raise MappingConfigError(
            f"mapping target columns {sorted(targets)} fit {len(matches)} event models "
            f"({names or 'none'}); routing requires exactly one (sale-versus-change is "
            "decided per mapping, Slice 10)",
            tenant_id=tenant_id,
            trace_id=trace_id,
        )
    return matches[0]


async def load_active_mapping(engine: AsyncEngine, event: IngressReadyEvent) -> LoadedMapping:
    """Per-lookup load of the ACTIVE mapping for (tenant, source, template); loud when absent.

    The ``template_id`` predicate (Slice 8a, D71) plus ``uq_csm_active_per_source``
    make this at most one row — ``.first()`` is exact, never arbitrary.
    """
    async with rls_session(engine, event.tenant_id) as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT mapping_version_id, mapping_rules, template_type, "
                    "pre_validation_suite_ref, post_validation_suite_ref "
                    "FROM config.source_mappings "
                    "WHERE tenant_id = CAST(:tenant_id AS uuid) AND source_id = :source_id "
                    "AND template_id = CAST(:template_id AS uuid) "
                    "AND status = 'ACTIVE'"
                ),
                {
                    "tenant_id": str(event.tenant_id),
                    "source_id": event.source_id,
                    "template_id": str(event.template_id),
                },
            )
        ).first()
    if row is None:
        raise MappingConfigError(
            f"no ACTIVE mapping for source_id={event.source_id!r} "
            f"template_id={event.template_id}; the mapping config is a required "
            "value (code-quality rule 4)",
            tenant_id=str(event.tenant_id),
            trace_id=str(event.trace_id),
        )
    if row.pre_validation_suite_ref is not None or row.post_validation_suite_ref is not None:
        raise SuiteDefinitionError(
            "module:ClassName suite refs are not supported in Slice 10 (NULL = default "
            "is the only live state; dynamic suite import is a registered scope limit)",
        )
    raw_rules = row.mapping_rules if isinstance(row.mapping_rules, dict) else json.loads(row.mapping_rules)
    try:
        source = SourceMapping.model_validate(raw_rules)
    except ValidationError as exc:
        raise MappingConfigError(
            f"mapping_rules for mapping_version_id={row.mapping_version_id} do not parse: "
            f"{type(exc).__name__}",
            tenant_id=str(event.tenant_id),
            trace_id=str(event.trace_id),
        ) from exc
    if not source.rename:
        raise MappingConfigError(
            f"mapping_version_id={row.mapping_version_id} declares no rename targets; "
            "an empty mapping cannot produce a canonical contribution",
            tenant_id=str(event.tenant_id),
            trace_id=str(event.trace_id),
        )
    # Routing by the STORED type (Slice 14d): a snapshot template writes the hot
    # table directly (the catalogue path); the event types keep the UNCHANGED
    # column-inference routing — `route_target_model` is byte-identical for them,
    # so a sale/change template still routes exactly as before.
    target: type[BaseModel]
    if row.template_type == SNAPSHOT:
        target = StoreSkuCurrentPosition
    else:
        target = route_target_model(source, tenant_id=str(event.tenant_id), trace_id=str(event.trace_id))
    return LoadedMapping(
        mapping_version_id=int(row.mapping_version_id),
        source=source,
        target_model=target,
        hot_complete=classify_hot_completeness(source, target),
    )


def apply_loaded_mapping(
    loaded: LoadedMapping,
    frame: pl.DataFrame,
    *,
    tenant_id: str,
    trace_id: str,
) -> MappingResult:
    """Run the pure four-sub-stage engine over the validated chunk."""
    return apply_mapping(
        loaded.source,
        frame,
        log_context=LogContext(tenant_id=tenant_id, trace_id=trace_id),
    )


def apply_loaded_enrichment(
    facts: Mapping[str, object],
    result: MappingResult,
    *,
    tenant_id: str,
    trace_id: str,
) -> MappingResult:
    """Enrich the contribution from the handed-in internal-source facts (slice-5b; D94).

    Wraps the pure ``apply_enrichment`` the way ``apply_loaded_mapping`` wraps
    ``apply_mapping``: the consumer reads the store facts and hands them in. The
    enriched contribution REPLACES ``result.contribution`` while reusing the original
    ``source_row_indices`` and ``failures`` unchanged (column-wise mutation only — the
    D94 row-alignment contract). The caller gates this to the current-position target;
    the table token is CURRENT_POSITION (the only wired table this slice).
    """
    enriched = apply_enrichment(
        result.contribution,
        facts,
        table=CURRENT_POSITION,
        log_context=LogContext(tenant_id=tenant_id, trace_id=trace_id),
    )
    return replace(result, contribution=enriched.contribution)
