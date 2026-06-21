"""The create/edit ``mapping_rules`` gate — D49 shape + the 14b semantic layer.

A mapping-template write stores config the streaming consumer will later parse,
route, and run; anything it would refuse must be a clean 400 HERE, never a
stored-invalid-config write. Four steps, all raising ``MappingConfigError``
(mapped to 400 by ``errors_http``):

1. **Shape + D49 args** — ``SourceMapping.model_validate`` (the engine contract:
   frozen, ``extra="forbid"``, mandatory locale/separator/format declarations,
   derive composition typing). A pydantic ``ValidationError`` is wrapped, exactly
   as the consumer wraps it (``streaming_consumer/pipeline/mapping.py``).
2. **Non-empty rename** — the consumer refuses an empty mapping (no canonical
   contribution); so do we.
3. **Routing** — the target set must fit EXACTLY one event model's
   mapping-produced column set. Zero matches → unknown/foreign targets; two →
   ambiguous (the discriminating sets are disjoint by design, e.g.
   ``source_sale_timestamp`` vs ``source_event_timestamp``). Same rule as the
   consumer's ``route_target_model``; both sides derive from the ONE source,
   ``dis_validation.mapping_produced_columns`` (drift-guarded), so the SETS
   cannot diverge — only this ~10-line check is repeated (surfaced in the slice
   plan as a later promotion candidate into dis-validation).
4. **Mandatory coverage** — every required (non-Optional) field of the routed
   model that is mapping-produced must be provided by rename or derive
   (constant/copy/date_from_datetime count: "PROVIDED", not "a CSV column must
   point at it"). Derived live from ``model_fields`` ∩ the provenance partition,
   never hardcoded. For change events this is the five polymorphic-common
   keys/discriminators only — subtype payload columns are all Optional, so
   pricing-only and inventory-only templates both pass. (The row-level
   ``value_before OR value_after`` CHECK is deliberately NOT lifted to config
   validation: step 4 is strictly NOT-NULL-derived — one canonical truth, no
   hand-curated extras. An authored change-template lint is a surfaced later
   refinement, operator-gated.)

The catalog endpoint derives from the same two sources (same models, same
provenance accessor), so what the catalog shows mappable and what this gate
accepts cannot drift (slice principle: one canonical truth).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

from dis_canonical import StoreSkuChangeEvent, StoreSkuCurrentPosition, StoreSkuSaleEvent
from dis_core.errors import InvalidTemplateTypeError, MappingConfigError
from dis_enrichment import CURRENT_POSITION, enrichment_fields
from dis_mapping import SourceMapping
from dis_ui_server.schemas.mapping_fields import FieldSection
from dis_validation import (
    TEMPLATE_TYPES,
    is_template_type,
    mandatory_mapping_produced,
    mapping_produced_columns,
    model_for_template_type,
)

# The routing universe: the two event tables of the dual-write — identical to the
# consumer's EVENT_MODELS (streaming_consumer/pipeline/mapping.py; not importable
# across services, cross-referenced instead). The hot table is never a routing
# target; signal_history raises inside mapping_produced_columns by design.
EVENT_MODELS: tuple[type[BaseModel], ...] = (StoreSkuSaleEvent, StoreSkuChangeEvent)

# Wire section labels, keyed by routed model (shared with the field catalog).
SECTION_BY_MODEL: dict[type[BaseModel], FieldSection] = {
    StoreSkuSaleEvent: "sale_event",
    StoreSkuChangeEvent: "change_event",
}


def parse_mapping_rules(raw: dict[str, Any], *, tenant_id: str) -> SourceMapping:
    """Step 1+2: the D49 engine contract, plus the consumer's non-empty-rename rule."""
    try:
        source = SourceMapping.model_validate(raw)
    except ValidationError as exc:
        # Wrap without echoing values: a mapping_rules document is config, but a
        # malformed one can contain anything — only the failure SHAPE survives.
        raise MappingConfigError(
            f"mapping_rules do not parse as the D49 shape: {exc.error_count()} validation error(s); "
            "the contract is {version, rename, normalize, cast, derive} (libs/dis-mapping)",
            tenant_id=tenant_id,
        ) from exc
    except MappingConfigError as exc:
        # SourceMapping's own validators raise without a tenant in scope (the
        # engine never holds one); attach it here so the envelope carries the
        # load-bearing context (code-quality rule 5).
        exc.tenant_id = exc.tenant_id or tenant_id
        raise
    if not source.rename:
        raise MappingConfigError(
            "mapping_rules declare no rename targets; an empty mapping cannot produce a "
            "canonical contribution (the streaming consumer refuses it)",
            tenant_id=tenant_id,
        )
    return source


def route_target_model(source: SourceMapping, *, tenant_id: str) -> type[BaseModel]:
    """Step 3: the one event model this template's contribution targets."""
    targets = set(source.target_columns)
    matches = [model for model in EVENT_MODELS if targets <= mapping_produced_columns(model)]
    if len(matches) != 1:
        names = [model.__name__ for model in matches]
        raise MappingConfigError(
            f"mapping target columns {sorted(targets)} fit {len(matches)} event models "
            f"({names or 'none'}); a template must target exactly one of "
            f"{[m.__name__ for m in EVENT_MODELS]} — check the field catalog "
            "(GET /template-mapping-fields) for the mappable columns per section",
            tenant_id=tenant_id,
        )
    return matches[0]


def _model_label(model: type[BaseModel]) -> str:
    """A human label for error messages — the wire section for events, else the model name."""
    return SECTION_BY_MODEL.get(model, model.__name__)


def enrichment_guaranteed_for(model: type[BaseModel]) -> frozenset[str]:
    """The enrichment value-guaranteed columns for ``model`` (Slice 16i, D95/D98).

    Current-position is the only enriched table, so the hot model returns
    ``enrichment_fields(CURRENT_POSITION)`` (currency, tax_treatment) and the event
    models return the empty set (enrichment never runs on the event path — the D98
    asymmetry), leaving their mandatory sets unchanged. Subtracted from
    ``mandatory_mapping_produced`` so an enrichment-supplied column is not demanded of
    the mapping while staying mapping-produced by origin (still legal to MAP, just not
    required). Mirrors the consumer's ``target_model is StoreSkuCurrentPosition`` gate.
    """
    if model is StoreSkuCurrentPosition:
        return frozenset(enrichment_fields(CURRENT_POSITION))
    return frozenset()


def check_mandatory_coverage(source: SourceMapping, model: type[BaseModel], *, tenant_id: str) -> None:
    """Step 4: every mandatory mapping-produced column is provided by rename or derive."""
    missing = mandatory_mapping_produced(model, enrichment_guaranteed_for(model)) - set(source.target_columns)
    if missing:
        raise MappingConfigError(
            f"mapping_rules leave mandatory {_model_label(model)} column(s) "
            f"{sorted(missing)} unprovided; each must come from a rename or a derive "
            "(constant / copy / date_from_datetime)",
            tenant_id=tenant_id,
        )


# Presence-pairing implications for the catalogue (snapshot) target — the
# shape-level subset of the hot table's CHECK constraints (promo_identifier
# requires promo_price; the expiry triple is all-or-none). Mirrors the consumer's
# HOT_CHECK_IMPLICATIONS (streaming_consumer.pipeline.mapping; not importable
# across services, cross-referenced). Enforced here so a violating catalogue
# template is a clean 400, not a write-time CHECK failure.
_CATALOGUE_PRESENCE_PAIRINGS: tuple[tuple[frozenset[str], frozenset[str]], ...] = (
    (frozenset({"promo_identifier"}), frozenset({"promo_price"})),
    (
        frozenset({"expiry_date", "expiry_source", "expiry_confidence"}),
        frozenset({"expiry_date", "expiry_source", "expiry_confidence"}),
    ),
)


def require_template_type(template_type: str, *, tenant_id: str) -> None:
    """Reject a ``template_type`` outside the in-code vocabulary (clean 400)."""
    if not is_template_type(template_type):
        raise InvalidTemplateTypeError(
            f"unknown template_type {template_type!r}; expected one of {list(TEMPLATE_TYPES)}",
            template_type=template_type,
            tenant_id=tenant_id,
        )


def check_target_legality(
    source: SourceMapping, model: type[BaseModel], *, template_type: str, tenant_id: str
) -> None:
    """The type-keyed legality rule: every rule target must be a column this type may write.

    Replaces the untyped "fit exactly one event model" inference: with the type
    stored, the question is "does this rule's target match what this template type
    may write." Rejects event targets for the snapshot type and hot-table targets
    for the event types, both directions (the produced sets are disjoint by
    construction)."""
    illegal = set(source.target_columns) - mapping_produced_columns(model)
    if illegal:
        raise MappingConfigError(
            f"mapping target columns {sorted(illegal)} are not legal for template_type "
            f"{template_type!r} (target {model.__name__}); check the field catalog "
            f"(GET /template-mapping-fields?template_type={template_type}) for the mappable columns",
            tenant_id=tenant_id,
        )


def check_presence_pairings(source: SourceMapping, *, tenant_id: str) -> None:
    """Catalogue presence pairings (promo / expiry) — clean 400 before the write."""
    targets = set(source.target_columns)
    for trigger, companion in _CATALOGUE_PRESENCE_PAIRINGS:
        if (trigger & targets) and not (companion <= targets):
            missing = sorted(companion - targets)
            raise MappingConfigError(
                f"mapping_rules provide {sorted(trigger & targets)} but leave required "
                f"companion column(s) {missing} unprovided (the hot table's presence-pairing "
                "CHECK: promo_identifier requires promo_price; the expiry triple is all-or-none)",
                tenant_id=tenant_id,
            )


def validate_mapping_rules(raw: dict[str, Any], *, tenant_id: str) -> SourceMapping:
    """The untyped four-step gate (legacy): infers the routed event model.

    Retained for any caller that has no ``template_type`` in hand. The type-aware
    create/edit path uses ``validate_mapping_rules_for_type`` instead."""
    source = parse_mapping_rules(raw, tenant_id=tenant_id)
    model = route_target_model(source, tenant_id=tenant_id)
    check_mandatory_coverage(source, model, tenant_id=tenant_id)
    return source


def validate_mapping_rules_for_type(
    raw: dict[str, Any], *, template_type: str, tenant_id: str
) -> SourceMapping:
    """The type-keyed gate (Slice 14d): shape + non-empty rename + target legality
    by type + mandatory coverage (+ catalogue presence pairings). Returns the
    validated document for storage/serving."""
    require_template_type(template_type, tenant_id=tenant_id)
    source = parse_mapping_rules(raw, tenant_id=tenant_id)
    model = model_for_template_type(template_type)
    check_target_legality(source, model, template_type=template_type, tenant_id=tenant_id)
    check_mandatory_coverage(source, model, tenant_id=tenant_id)
    if model is StoreSkuCurrentPosition:
        check_presence_pairings(source, tenant_id=tenant_id)
    return source
