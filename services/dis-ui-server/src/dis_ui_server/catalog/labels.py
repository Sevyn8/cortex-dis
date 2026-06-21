"""Authored operator-facing labels for the field catalog — merged by key, never structural.

STRUCTURE (keys, mandatory-ness, datatypes, allowed values) is DERIVED from the
canonical models + the dis-validation provenance partition in
``field_catalog.py``; this module authors only what code cannot derive: the
display name and the operator-facing description. The builder's both-directions
drift check (``FieldCatalogDriftError``, fails boot) keeps this dict honest: a
new mapping-produced canonical column without a label here refuses to start, as
does a label whose column is gone.

Keyed by section (the routed event model's wire label) then canonical column
name, because a column like ``sku_id`` is a distinct operator decision per
template kind and may warrant different guidance.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FieldLabel:
    """The authored half of one catalog entry (display + description)."""

    display_name: str
    description: str


@dataclass(frozen=True)
class CatalogueFieldLabel(FieldLabel):
    """An authored snapshot-roster entry — adds the within-packet ``section``.

    The catalogue packet groups its fields by domain (identity / product /
    pricing / inventory / expiry / regulatory_status), so the section is authored
    per field, unlike the event packets where the section is the routed model's
    single wire label.
    """

    section: str = "identity"


LABELS: dict[str, dict[str, FieldLabel]] = {
    "sale_event": {
        "event_date": FieldLabel(
            "Event date",
            "Calendar date of the sale at UTC, derived from the sale timestamp "
            "(use a date_from_datetime derive on the timestamp column).",
        ),
        "sku_id": FieldLabel("SKU", "The product identifier as your system reports it."),
        "sku_variant": FieldLabel(
            "SKU variant", "Variant qualifier (size, colour) when your SKUs carry one."
        ),
        "sku_lot_batch": FieldLabel("Lot / batch", "Lot or batch number when tracked."),
        "event_subtype": FieldLabel("Sale type", "Whether the line is a sale, a return, or a void."),
        "source_sale_timestamp": FieldLabel(
            "Sale timestamp",
            "When the sale happened, per your system's clock. Declare the exact "
            "format and timezone — they are never guessed.",
        ),
        "transaction_id": FieldLabel(
            "Transaction ID", "Your receipt/transaction reference; enables exact correction matching."
        ),
        "line_item_seq": FieldLabel("Line number", "Position of this line within the transaction."),
        "quantity": FieldLabel("Quantity", "Units sold (negative or RETURN-typed rows represent returns)."),
        "unit_retail_price": FieldLabel("Unit retail price", "Listed shelf price per unit at sale time."),
        "unit_sale_price": FieldLabel("Unit sale price", "Price per unit actually charged after discounts."),
        "discount_amount": FieldLabel("Discount amount", "Absolute discount applied to the line."),
        "discount_pct": FieldLabel("Discount %", "Percentage discount applied to the line."),
        "unit_cost": FieldLabel("Unit cost", "What each unit costs you, if your file includes it."),
        "promo_identifier": FieldLabel("Promotion ID", "Identifier of the promotion applied, if any."),
        "tax_amount": FieldLabel("Tax amount", "Tax charged on the line."),
        "currency": FieldLabel(
            "Currency",
            "The currency your prices are in, as a 3-letter code (for example, EUR or USD).",
        ),
        "payment_method": FieldLabel("Payment method", "How the transaction was paid (cash, card, ...)."),
        "customer_token": FieldLabel(
            "Customer token",
            "A privacy-safe stand-in for the customer. Real customer details are "
            "never imported; they are replaced with this token before the data "
            "reaches here.",
        ),
        "sale_channel": FieldLabel("Sale channel", "Originating channel (in-store, online, ...)."),
    },
    "change_event": {
        "event_date": FieldLabel(
            "Event date",
            "Calendar date of the change at UTC, derived from the event timestamp "
            "(use a date_from_datetime derive on the timestamp column).",
        ),
        "sku_id": FieldLabel("SKU", "The product identifier as your system reports it."),
        "sku_variant": FieldLabel(
            "SKU variant", "Variant qualifier (size, colour) when your SKUs carry one."
        ),
        "sku_lot_batch": FieldLabel("Lot / batch", "Lot or batch number when tracked."),
        "event_category": FieldLabel(
            "Change category",
            "What kind of change this template carries (inventory, price, cost, ...). "
            "Usually a constant derive per template.",
        ),
        "event_subtype": FieldLabel("Change subtype", "Your finer-grained change label (free-form)."),
        "source_event_timestamp": FieldLabel(
            "Change timestamp",
            "When the change happened, per your system's clock. Declare the exact "
            "format and timezone — they are never guessed.",
        ),
        "effective_from": FieldLabel("Effective from", "When the new value takes effect, if scheduled."),
        "effective_until": FieldLabel("Effective until", "When the value expires, if bounded."),
        "attribute_name": FieldLabel(
            "Attribute",
            "Which attribute changed (e.g. stock_qty, current_retail_price). "
            "Usually a constant derive per template.",
        ),
        "value_before": FieldLabel(
            "Value before", "The previous value of the field, before this change, if your file includes it."
        ),
        "value_after": FieldLabel("Value after", "The attribute's value after the change."),
        "reason_code": FieldLabel("Reason code", "Your system's code for why the change happened."),
        "reason_note": FieldLabel("Reason note", "Free-text note accompanying the change."),
        "change_context": FieldLabel(
            "Change context", "Additional structured context your system attaches to the change."
        ),
    },
}


# The catalogue / snapshot roster (Slice 14d): the authored half of the
# store_sku_current_position field set, keyed by canonical column. The 28 keys
# are EXACTLY the mapping-produced columns of StoreSkuCurrentPosition (drift-guard
# enforced at boot). tax_treatment is deliberately ABSENT: it is consumer-injected
# (store-denormalized), not catalogue-file data — the deliberate file-vs-store
# asymmetry with currency (which IS file-supplied/mapping-produced). The
# __ignore__ sentinel is NOT here (it is not a canonical column); the catalog
# builder appends it to every field set.
SNAPSHOT_LABELS: dict[str, CatalogueFieldLabel] = {
    # section: identity
    "sku_id": CatalogueFieldLabel("SKU", "The product identifier as your system reports it.", "identity"),
    "sku_variant": CatalogueFieldLabel(
        "SKU variant", "Variant qualifier (size, colour) when your SKUs carry one.", "identity"
    ),
    "sku_lot_batch": CatalogueFieldLabel("Lot / batch", "Lot or batch number when tracked.", "identity"),
    "barcode": CatalogueFieldLabel(
        "Barcode", "Scannable barcode (EAN, UPC, ...) for the item, if present.", "identity"
    ),
    # section: product
    "product_name": CatalogueFieldLabel("Product name", "Human-readable product name.", "product"),
    "product_description": CatalogueFieldLabel(
        "Product description", "Longer descriptive text for the product, if provided.", "product"
    ),
    "product_category": CatalogueFieldLabel(
        "Category", "Top-level category your system files the product under.", "product"
    ),
    "product_sub_category": CatalogueFieldLabel(
        "Sub-category", "Sub-category beneath the main category.", "product"
    ),
    "product_department": CatalogueFieldLabel(
        "Department", "Store department or division the product belongs to.", "product"
    ),
    "supplier_id": CatalogueFieldLabel(
        "Supplier ID", "Your identifier for the product's supplier.", "product"
    ),
    "packaging_type": CatalogueFieldLabel(
        "Packaging type", "How the unit is packaged (each, case, pack, ...).", "product"
    ),
    "sku_size": CatalogueFieldLabel("Size", "Declared size of the unit (e.g. 500ml, 1kg).", "product"),
    "unit_of_measure": CatalogueFieldLabel(
        "Unit of measure", "Unit the quantity is counted in (each, kg, litre, ...).", "product"
    ),
    # section: pricing
    "current_retail_price": CatalogueFieldLabel(
        "Retail price", "Current listed shelf price per unit.", "pricing"
    ),
    "unit_cost": CatalogueFieldLabel(
        "Unit cost", "What each unit costs you, if your file includes it.", "pricing"
    ),
    "promo_price": CatalogueFieldLabel(
        "Promo price", "Promotional price per unit while a promotion is active.", "pricing"
    ),
    "promo_identifier": CatalogueFieldLabel(
        "Promotion ID", "Identifier of the promotion applied, if any.", "pricing"
    ),
    "currency": CatalogueFieldLabel(
        "Currency",
        "The currency your prices are in, as a 3-letter code (for example, EUR or USD).",
        "pricing",
    ),
    # section: inventory
    "stock_qty": CatalogueFieldLabel("Stock quantity", "Units currently on hand.", "inventory"),
    "reorder_point": CatalogueFieldLabel(
        "Reorder point", "Stock level at which the item is reordered.", "inventory"
    ),
    "lead_time_days": CatalogueFieldLabel(
        "Lead time (days)", "Days between reorder and replenishment.", "inventory"
    ),
    "receipt_date": CatalogueFieldLabel("Receipt date", "Date the current stock was received.", "inventory"),
    # section: expiry
    "expiry_date": CatalogueFieldLabel(
        "Expiry date", "Expiry or best-before date for the current stock.", "expiry"
    ),
    "expiry_source": CatalogueFieldLabel(
        "Expiry source", "Where the expiry date came from (label, system, estimate).", "expiry"
    ),
    "expiry_confidence": CatalogueFieldLabel(
        "Expiry confidence", "How reliable the expiry value is, as your system rates it.", "expiry"
    ),
    # section: regulatory_status
    "regulatory_flag": CatalogueFieldLabel(
        "Regulatory flag", "Whether the item is subject to regulatory handling.", "regulatory_status"
    ),
    "regulatory_type": CatalogueFieldLabel(
        "Regulatory type", "The kind of regulatory handling that applies.", "regulatory_status"
    ),
    "sku_status": CatalogueFieldLabel(
        "SKU status", "Lifecycle state of the SKU (active, discontinued, ...).", "regulatory_status"
    ),
}
