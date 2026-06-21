"""The ``template_type`` vocabulary — the ONE shared definition (Slice 14d).

``template_type`` is the packet axis: it parameterises which canonical model a
mapping template targets, and therefore which write path its rows take. It is
defined ONCE here and read by every consumer of the vocabulary — the type
endpoint and the field catalog (dis-ui-server), the rule-target validator
(dis-ui-server), and the streaming consumer's routing — with NO second copy
(slice principle: one vocabulary, one definition; the move to a lookup table is
deferred to when the set stabilises).

Why this lib and not the BFF: the streaming consumer cannot import a service,
and ``dis-validation`` already owns the model-introspection layer
(``mapping_produced_columns``, the provenance partition) that the catalog and
validator read, and depends only on ``dis-core`` + ``dis-canonical`` (its
import contract). So both ``dis-ui-server`` and ``streaming-consumer`` import the
type-to-model mapping from here. The endpoint's operator-facing display copy is
NOT here — it is presentation, keyed off these keys in the BFF.

The mapping is the legality rule, by construction:

- ``snapshot`` → ``StoreSkuCurrentPosition`` (the hot table; a direct catalogue
  write on the COMPLETE hot path, parallel to the event projections).
- ``sales`` → ``StoreSkuSaleEvent`` and ``inventory_change`` →
  ``StoreSkuChangeEvent`` formalise the implicit sale-vs-change discriminator
  (previously column-subset inference) into the stored type.
"""

from __future__ import annotations

from pydantic import BaseModel

from dis_canonical import StoreSkuChangeEvent, StoreSkuCurrentPosition, StoreSkuSaleEvent

# The vocabulary keys, in listing order (the order the type endpoint serves).
SNAPSHOT = "snapshot"
SALES = "sales"
INVENTORY_CHANGE = "inventory_change"

TEMPLATE_TYPES: tuple[str, ...] = (SNAPSHOT, SALES, INVENTORY_CHANGE)

# The single canonical model each ``template_type`` targets. The catalog serves
# this model's mapping-produced field set; the validator accepts ONLY this
# model's mapping-produced columns as legal rule targets; the consumer routes the
# resulting rows to this model's write path.
MODEL_BY_TYPE: dict[str, type[BaseModel]] = {
    SNAPSHOT: StoreSkuCurrentPosition,
    SALES: StoreSkuSaleEvent,
    INVENTORY_CHANGE: StoreSkuChangeEvent,
}


def is_template_type(value: str) -> bool:
    """True iff ``value`` is a member of the vocabulary."""
    return value in MODEL_BY_TYPE


def model_for_template_type(template_type: str) -> type[BaseModel]:
    """The canonical model a ``template_type`` targets; raises ``KeyError`` if unknown.

    Callers that accept external input validate with ``is_template_type`` first
    and raise their own domain error (e.g. the BFF's ``InvalidTemplateTypeError``);
    a ``KeyError`` here means an unvalidated value reached the routing layer, a bug.
    """
    return MODEL_BY_TYPE[template_type]
